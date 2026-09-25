"""Single-iteration and autonomous project loops."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import fcntl
import subprocess
import time
import uuid

from .browser.chatgpt import ChatGPTPage
from .browser.session import BrowserSession
from playwright.sync_api import Error as PlaywrightError
from .config import AppConfig
from .ci.local import LocalCI
from .git_gate import GitPushError, snapshot as git_snapshot, assert_unchanged_before_ci, changed_paths, meaningful_change, commit_and_push, prepare_repository, can_clear_legacy_dirty_recovery, is_ancestor
from .execution import ExecutionPolicy, ExecutionResult, execute_request, format_execution_results, parse_execution_requests
from .controller import Controller
from .project import build_continuation_prompt, inspect_project
from .rollover import rollover, rollover_from_max_length
from .remote_ci import verify_github_actions
from .safety import SafetyLimits
from .state import AgentState, IterationStage, RunState, load_state, save_state
from .trace import trace
from .trajectory import append_event, trajectory_path
from .runtime import ExecutionRuntime
from .verification import RepositoryVerifier
from .recovery import RecoveryManager


@dataclass(frozen=True)
class RunResult:
    state: AgentState
    response: str | None = None


class DeadlineReached(RuntimeError):
    """The configured hard stop was reached while an operation was in flight."""


class RolloverLimitReached(RuntimeError):
    """The configured conversation rollover budget was exhausted."""


def _trajectory_event(state: AgentState, project_name: str, phase: str, event: str, **data) -> None:
    """Persist an append-only event without making trajectory logging fatal."""
    try:
        append_event(
            trajectory_path(state_dir(project_name)),
            run_id=state.run_id,
            project=project_name,
            iteration=state.iteration,
            phase=phase,
            event=event,
            **data,
        )
    except OSError as exc:
        trace("trajectory.error", error=type(exc).__name__, detail=str(exc))


def _remaining_timeout(deadline: datetime | None, configured: float) -> float:
    if deadline is None:
        return configured
    remaining = (deadline - datetime.now().astimezone()).total_seconds()
    if remaining <= 0:
        raise DeadlineReached("deadline reached during operation")
    return min(configured, max(0.1, remaining))


def _is_browser_connection_error(exc: Exception) -> bool:
    if isinstance(exc, PlaywrightError):
        return True
    text = str(exc).casefold()
    return any(token in text for token in (
        "target closed", "browser has been closed", "connection closed",
        "websocket", "cdp", "transport", "playwright",
    ))

def _is_max_length_error(exc: Exception) -> bool:
    return "maximum length" in str(exc).casefold()



def state_dir(project: str) -> Path:
    return Path("state") / project


def state_path(project: str) -> Path:
    return state_dir(project) / "current.json"


@contextmanager
def _project_lock(project: str):
    directory = state_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".lock"
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"project is already running: {project}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def save_response(project: str, response: str) -> None:
    d = state_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    (d / "last_response.md").write_text(response.rstrip() + "\n", encoding="utf-8")


def _run_local_ci(project, state: AgentState, deadline: datetime | None = None) -> bool:
    stage = project.ci_stage
    if not stage:
        state.last_ci_result = None
        return True
    commands = project.ci_stages.get(stage)
    if commands is None:
        raise RuntimeError(f"configured CI stage is not defined: {stage}")
    trace("ci.start", project=project.name, stage=stage, root=str(project.project_root))
    _trajectory_event(state, project.name, "verification", "local_ci.started", stage=stage)
    result = LocalCI().run(
        project=project.name,
        stage=stage,
        project_root=project.project_root,
        commands=commands,
        timeout_seconds=_remaining_timeout(deadline, project.ci_timeout_seconds),
    )
    lines = [f"stage={stage} success={result.success}"]
    for command in result.commands:
        lines.append(f"$ {' '.join(command.command)}")
        if command.stdout:
            lines.append(command.stdout.rstrip())
        if command.stderr:
            lines.append(command.stderr.rstrip())
        lines.append(f"exit_code={command.returncode} duration={command.duration_seconds:.2f}s")
    state.last_ci_result = "\n".join(lines)[-12000:]
    _trajectory_event(state, project.name, "verification", "local_ci.completed", stage=stage, success=result.success)
    trace("ci.complete", project=project.name, stage=stage, success=result.success,
          result_tail=state.last_ci_result[-1500:])
    return result.success


def _execute_agent_requests(response: str, project) -> tuple[str, bool, bool]:
    if not project.execution_enabled:
        return response, False, False
    try:
        requests = parse_execution_requests(response)
    except Exception as exc:
        return (
            "LabOS server execution results:\n"
            "[1] action=protocol success=False exit_code=None\n"
            "stdout=\n"
            f"stderr=invalid execution request: {exc}"
        ), True, False
    if not requests:
        trace("execution.none", response_chars=len(response))
        return response, False, False
    policy = ExecutionPolicy(
        allowed_roots=tuple(path.resolve() for path in project.execution_allowed_roots),
        command_timeout_seconds=project.execution_command_timeout_seconds,
    )
    trace("execution.requests", count=len(requests),
          actions=[str(request.get("action", "unknown")) for request in requests])
    runtime = ExecutionRuntime(project.project_root, policy)
    observation = runtime.apply(requests)
    formatted = format_execution_results(observation.results)
    trace("execution.complete", results=[
        {"action": result.action, "success": result.success, "exit_code": result.exit_code}
        for result in observation.results
    ])
    return formatted, True, observation.success


def _select_chat_page(context, project):
    return ChatGPTPage.select_page(
        context,
        project_name=project.project_name,
        project_url=project.project_url,
    )


def _prepare_state(project_name: str, *, recover: bool = False) -> AgentState:
    state = load_state(state_path(project_name))
    if state is None:
        run_id = uuid.uuid4().hex
        return AgentState(project=project_name, run_id=run_id, branch_name=f"agent/{run_id[:12]}")

    if recover and state.state in {RunState.WORKING, RunState.STARTING, RunState.ROLLOVER, RunState.ITERATION_SUCCEEDED}:
        state.failure_history.append({
            "timestamp": datetime.now().astimezone().isoformat(),
            "iteration": state.iteration,
            "reason": "recovered stale controller state after controller restart",
        })
        state.failure_history = state.failure_history[-20:]
        state.state = RunState.IDLE
        state.started_at = None
        state.stopped_at = None
        state.last_action = None
        state.reason = None
    elif recover and state.state in {RunState.STOPPED, RunState.ERROR}:
        state.run_id = state.run_id or uuid.uuid4().hex
        state.state = RunState.IDLE
        state.consecutive_failures = 0
        state.consecutive_no_progress = 0
        state.started_at = None
        state.stopped_at = None
        state.last_action = None
        state.reason = None
    elif state.state in {RunState.STOPPED, RunState.COMPLETED, RunState.ERROR, RunState.BLOCKED}:
        state.run_id = uuid.uuid4().hex
        state.branch_name = f"agent/{state.run_id[:12]}"
        state.state = RunState.IDLE
        state.iteration = 0
        state.rollover_count = 0
        state.consecutive_failures = 0
        state.consecutive_no_progress = 0
        state.iteration_at_last_rollover = 0
        state.started_at = None
        state.stopped_at = None
        state.last_action = None
        state.reason = None
        state.last_ci_result = None
        state.last_progress_result = None
        state.pending_ci_fix = False
        state.pending_ci_baseline_untracked = []
        state.pending_ci_worktree_fingerprint = None
        state.pending_remote_ci_fix = False
        state.pending_remote_ci_sha = None
        state.pending_remote_ci_result = None
    if state.branch_name == "main":
        state.branch_name = f"agent/{state.run_id[:12]}"
    return state


def _handle_no_progress(controller: Controller, state: AgentState) -> bool:
    controller.mark_no_progress("assistant response completed but repository working tree did not change")
    if state.consecutive_no_progress >= controller.limits.max_consecutive_no_progress:
        controller.stop("maximum consecutive no-progress iterations reached")
        return True
    return False


def _response_declares_done(response: str) -> bool:
    return "LABOS_DONE" in response


def _resolve_execution(chat, response: str, project, project_name: str, config: AppConfig, deadline: datetime | None = None, state: AgentState | None = None) -> str:
    had_execution = False
    for attempt in range(4):
        if deadline is not None and datetime.now().astimezone() >= deadline:
            raise DeadlineReached("deadline reached before server execution")

        execution_feedback, requested_execution, execution_succeeded = _execute_agent_requests(response, project)
        if requested_execution:
            had_execution = True
            if state is not None:
                state.execution_requested = True
                state.iteration_stage = IterationStage.EXECUTION_REQUIRED
                state.execution_applied = execution_succeeded
                if execution_succeeded:
                    state.iteration_stage = IterationStage.EXECUTION_APPLIED
            response = chat.send_and_wait_for_response(
                "The LabOS controller executed your requested server operations. Use these real results and continue the implementation; do not claim execution that is not shown here. "
                "If more implementation is required, issue another labos-exec request. If the implementation is complete, end your response with LABOS_DONE.\n\n"
                + execution_feedback,
                timeout_seconds=_remaining_timeout(deadline, config.browser.response_timeout_seconds),
                quiet_seconds=config.browser.quiet_seconds,
                require_input_available=False,
            )
            if state is not None:
                _trajectory_event(state, project_name, "reasoning", "reasoning.completed", response_chars=len(response))
            save_response(project_name, response)
            continue

        if not project.execution_enabled:
            return response

        if _response_declares_done(response):
            if had_execution:
                return response
            raise RuntimeError(
                "server execution is required but the assistant declared LABOS_DONE without producing an execution request"
            )

        tick = chr(96)
        response = chat.send_and_wait_for_response(
            "STOP. Your response did not contain a server execution request or the required LABOS_DONE completion marker. "
            "Do not describe an intended change without performing it. You do not have direct access to the LabOS controller filesystem. "
            "Before doing anything else, issue at least one controlled server operation using a fenced "
            + tick * 3
            + "labos-exec JSON block. Use write_file for source/documentation changes, or read_file/run_command for inspection. "
            "Wait for the real execution result and then continue. If no further change is needed, issue a final inspection request and end with LABOS_DONE.",
            timeout_seconds=_remaining_timeout(deadline, config.browser.response_timeout_seconds),
            quiet_seconds=config.browser.quiet_seconds,
        )
        save_response(project_name, response)

    raise RuntimeError("server execution handshake did not complete within the maximum protocol rounds")


def _recovery(state: AgentState, project) -> RecoveryManager:
    return RecoveryManager(state, project)


def _mark_dirty_recovery(state: AgentState, before_git, current_git=None, *, project=None) -> None:
    """Compatibility wrapper; recovery ownership lives in RecoveryManager."""
    if project is None:
        raise RuntimeError("project is required for recovery coordination")
    _recovery(state, project).record_dirty(before_git, current_git)


def _mark_push_failure_recovery(state: AgentState, before_git, current_git=None, *, project=None) -> None:
    if project is None:
        raise RuntimeError("project is required for recovery coordination")
    _recovery(state, project).record_push_failure(before_git, current_git)


def _migrate_legacy_dirty_recovery(state: AgentState, project) -> None:
    _recovery(state, project).migrate_legacy()


def _reconcile_committed_recovery(state: AgentState, project) -> None:
    _recovery(state, project).reconcile_committed(
        revalidate=lambda: _run_local_ci(project, state)
    )


def _commit_recovery_baseline(state: AgentState, project) -> set[str] | None:
    return _recovery(state, project).baseline()


def _rollover_and_process_resume(
    chat, project, state: AgentState, project_name: str, config: AppConfig,
    *, deadline: datetime | None, max_rollovers: int, recovery_baseline=None,
) -> tuple[str, str]:
    if state.rollover_count >= max_rollovers:
        raise RolloverLimitReached("maximum rollovers reached")
    baseline = git_snapshot(project.project_root)
    state.rollover_count += 1
    state.iteration_at_last_rollover = state.iteration
    state.transition(RunState.ROLLOVER, reason="conversation rollover requested")
    continuation, _, resume_response = rollover_from_max_length(
        chat, project, state_dir(project_name),
        timeout_seconds=_remaining_timeout(deadline, config.browser.response_timeout_seconds),
        quiet_seconds=config.browser.quiet_seconds,
    )
    response = _resolve_execution(chat, resume_response, project, project_name, config, deadline=deadline)
    save_response(project_name, response)
    after = git_snapshot(project.project_root)
    recovery_reference = recovery_baseline or baseline
    if after.worktree_fingerprint != recovery_reference.worktree_fingerprint:
        _mark_dirty_recovery(state, recovery_reference, project=project)
    state.reason = "conversation rolled over and resumed"
    return continuation, response

def _run_once_impl(config: AppConfig, project_name: str) -> RunResult:
    project = config.projects.get(project_name)
    if project is None:
        raise RuntimeError(f"unknown project: {project_name}")

    with _project_lock(project_name):
        state = _prepare_state(project_name, recover=True)
        controller = Controller(state=state, limits=SafetyLimits(max_iterations=state.iteration + 1))
        controller.start()
        controller.begin_iteration(datetime.now().astimezone())
        _trajectory_event(state, project_name, "iteration", "iteration.started", started_head=git_snapshot(project.project_root).head)
        save_state(state_path(project_name), state)

        _migrate_legacy_dirty_recovery(state, project)
        _reconcile_committed_recovery(state, project)
        prepare_repository(
            project.project_root,
            branch_name=state.branch_name,
            allow_dirty=state.pending_ci_fix,
            expected_dirty_fingerprint=state.pending_ci_worktree_fingerprint,
        )
        snapshot = inspect_project(project.project_root, project.repository, project.state_files)
        before_git = git_snapshot(project.project_root)
        state.start_iteration(before_git.head)
        controller.chatgpt_working()

        trace("iteration.browser.attach", project=project_name, iteration=state.iteration,
              cdp=config.browser.cdp_url, profile=str(config.browser.profile_dir))
        with BrowserSession(config.browser.profile_dir, cdp_url=config.browser.cdp_url) as session:
            context = session.context
            chat = ChatGPTPage(_select_chat_page(context, project))
            if chat.status().rollover_required:
                continuation, response = _rollover_and_process_resume(
                    chat, project, state, project_name, config,
                    deadline=None, max_rollovers=controller.limits.max_rollovers,
                )
                save_response(project_name, response)
                save_state(state_path(project_name), state)
            else:
                chat.assert_ready()
            if project.project_name and not chat.project_context_present(project.project_name):
                controller.block(f"ChatGPT Project context not detected: {project.project_name}")
                save_state(state_path(project_name), state)
                return RunResult(state)

            prompt = build_continuation_prompt(
                snapshot,
                project.continuation_message,
                state.last_ci_result,
                state.last_progress_result,
                project.execution_enabled,
            )
            trace("chat.prompt", project=project_name, iteration=state.iteration,
                  prompt_chars=len(prompt), project_composer=bool(project.project_name))
            try:
                response = (
                    chat.send_project_message_and_wait_for_response(
                        project.project_name,
                        prompt,
                        timeout_seconds=config.browser.response_timeout_seconds,
                        quiet_seconds=config.browser.quiet_seconds,
                    )
                    if project.project_name
                    else chat.send_and_wait_for_response(
                        prompt,
                        timeout_seconds=config.browser.response_timeout_seconds,
                        quiet_seconds=config.browser.quiet_seconds,
                    )
                )
            except Exception as exc:
                controller.mark_failure(str(exc))
                save_state(state_path(project_name), state)
                return RunResult(state)

            trace("chat.response", project=project_name, iteration=state.iteration,
                  response_chars=len(response), response_tail=response[-1000:])
            save_response(project_name, response)
            try:
                response = _resolve_execution(chat, response, project, project_name, config, deadline=None, state=state)
                save_response(project_name, response)
                assert_unchanged_before_ci(project.project_root, before_git)
                verifier = RepositoryVerifier(project.project_root)
                verification = verifier.compare(before_git)
                after_git = verification.after
                recovery_pending = state.pending_ci_fix
                _trajectory_event(
                    state,
                    project_name,
                    "verification",
                    "repository.observed",
                    changed=not verification.clean_relative_to_baseline,
                    meaningful=verification.meaningful_change,
                    recovery_pending=recovery_pending,
                )
                if verification.clean_relative_to_baseline and not recovery_pending:
                    _handle_no_progress(controller, state)
                    save_state(state_path(project_name), state)
                    return RunResult(state, response)
                meaningful = verification.meaningful_change or recovery_pending
                controller.progress_verified(meaningful=meaningful)
                if not state.meaningful_progress:
                    _handle_no_progress(controller, state)
                    save_state(state_path(project_name), state)
                    return RunResult(state, response)
                controller.ci_running()
                ci_ok = _run_local_ci(project, state, deadline=None)
            except Exception as exc:
                try:
                    after_git = git_snapshot(project.project_root)
                    if after_git.worktree_fingerprint != before_git.worktree_fingerprint:
                        _mark_dirty_recovery(state, before_git, project=project)
                except Exception:
                    pass
                if isinstance(exc, DeadlineReached):
                    controller.stop(str(exc))
                else:
                    controller.mark_failure(str(exc))
                save_state(state_path(project_name), state)
                return RunResult(state, response)

            if not ci_ok:
                state.pending_ci_fix = True
                state.pending_ci_baseline_untracked = list(before_git.untracked_paths)
                state.pending_ci_worktree_fingerprint = after_git.worktree_fingerprint
                controller.mark_failure("local CI failed")
                save_state(state_path(project_name), state)
                return RunResult(state, response)

            try:
                controller.ci_passed()
                controller.committing()
                committed_sha = commit_and_push(
                    project.project_root,
                    before_git,
                    f"lab-agent: iteration {state.iteration}",
                    branch_name=state.branch_name,
                    allow_preexisting_tracked_changes=state.pending_ci_fix,
                    baseline_untracked=_commit_recovery_baseline(state, project),
                )
                if not committed_sha:
                    _mark_dirty_recovery(state, before_git, git_snapshot(project.project_root), project=project)
                    controller.mark_failure("working tree changed but Git gate produced no commit")
                    save_state(state_path(project_name), state)
                    return RunResult(state, response)
                state.commit_created = True
                _trajectory_event(state, project_name, "commit", "commit.created", sha=committed_sha)
                controller.pushing()
                state.last_action = f"committed and pushed {committed_sha}"
                state.last_commit_sha = committed_sha
                controller.remote_verified(committed_sha)
                _trajectory_event(state, project_name, "remote_verification", "push.verified", sha=committed_sha)
                remote_ci = verify_github_actions(
                    project.repository,
                    committed_sha,
                    timeout_seconds=project.remote_ci_timeout_seconds,
                    poll_seconds=project.remote_ci_poll_seconds,
                )
                state.pending_remote_ci_result = remote_ci.summary
                _trajectory_event(state, project_name, "remote_verification", "github_ci.completed", sha=committed_sha, success=remote_ci.success, summary=remote_ci.summary)
                if not remote_ci.success:
                    state.pending_remote_ci_fix = True
                    state.pending_remote_ci_sha = committed_sha
                    state.last_ci_result = (state.last_ci_result or "") + "\n" + state.pending_remote_ci_result
                    controller.mark_failure(f"GitHub Actions failed for exact SHA {committed_sha}: {remote_ci.summary}")
                    save_state(state_path(project_name), state)
                    return RunResult(state, response)
                controller.github_ci_verified()
            except GitPushError as exc:
                _mark_push_failure_recovery(state, before_git, git_snapshot(project.project_root), project=project)
                controller.mark_failure(f"validated changes could not be pushed; recovery is pending: {exc}")
                save_state(state_path(project_name), state)
                return RunResult(state, response)
            except Exception as exc:
                _mark_dirty_recovery(state, before_git, git_snapshot(project.project_root), project=project)
                controller.mark_failure(f"validated changes could not be committed/pushed: {exc}")
                save_state(state_path(project_name), state)
                return RunResult(state, response)

            controller.mark_success(continue_running=False)
            state.pending_ci_fix = False
            state.pending_ci_baseline_untracked = []
            state.pending_ci_worktree_fingerprint = None
            save_state(state_path(project_name), state)
            return RunResult(state, response)


def run_once(config: AppConfig, project_name: str) -> RunResult:
    try:
        return _run_once_impl(config, project_name)
    except Exception as exc:
        with _project_lock(project_name):
            state = load_state(state_path(project_name))
            if state is not None and state.state in {RunState.WORKING, RunState.STARTING, RunState.ROLLOVER}:
                state.record_failure(str(exc))
                state.consecutive_failures += 1
                state.transition(RunState.ERROR, reason=str(exc))
                save_state(state_path(project_name), state)
                return RunResult(state)
        raise


def _run_loop_impl(
    config: AppConfig,
    project_name: str,
    *,
    deadline: datetime | None,
    max_iterations: int,
    max_rollovers: int,
) -> RunResult:
    project = config.projects.get(project_name)
    if project is None:
        raise RuntimeError(f"unknown project: {project_name}")

    with _project_lock(project_name):
        state = _prepare_state(project_name, recover=True)
        controller = Controller(
            state=state,
            limits=SafetyLimits(
                deadline=deadline,
                max_iterations=max_iterations,
                max_rollovers=max_rollovers,
            ),
        )
        controller.start()
        save_state(state_path(project_name), state)

        with BrowserSession(config.browser.profile_dir, cdp_url=config.browser.cdp_url) as session:
            context = session.context
            chat = ChatGPTPage(_select_chat_page(context, project))
            if chat.status().rollover_required:
                try:
                    continuation, resume_response = _rollover_and_process_resume(
                        chat, project, state, project_name, config,
                        deadline=deadline, max_rollovers=max_rollovers,
                    )
                except RolloverLimitReached as exc:
                    controller.stop(str(exc))
                    save_state(state_path(project_name), state)
                    return RunResult(state)
                save_response(project_name, resume_response)
                save_state(state_path(project_name), state)
            else:
                chat.assert_ready()
            if project.project_name and not chat.project_context_present(project.project_name):
                controller.block(f"ChatGPT Project context not detected: {project.project_name}")
                save_state(state_path(project_name), state)
                return RunResult(state)

            continuation = project.continuation_message
            while True:
                controller.begin_iteration(datetime.now().astimezone())
                if state.state == RunState.STOPPED:
                    save_state(state_path(project_name), state)
                    return RunResult(state)

                before_git = None
                try:
                    _migrate_legacy_dirty_recovery(state, project)
                    # Reconcile a previously validated/pushed recovery before the
                    # dirty-worktree gate. The one-shot path already does this;
                    # the autonomous continue path must do the same or it can
                    # reject a valid recovery fingerprint after a restart.
                    _reconcile_committed_recovery(state, project)
                    prepare_repository(
                        project.project_root,
                        branch_name=state.branch_name,
                        allow_dirty=state.pending_ci_fix,
                        expected_dirty_fingerprint=state.pending_ci_worktree_fingerprint,
                    )
                    snapshot = inspect_project(
                        project.project_root,
                        project.repository,
                        project.state_files,
                    )
                    prompt = build_continuation_prompt(
                        snapshot,
                        continuation,
                        state.last_ci_result,
                        state.last_progress_result,
                        project.execution_enabled,
                    )
                    before_git = git_snapshot(project.project_root)
                    state.start_iteration(before_git.head)
                    controller.chatgpt_working()
                    if chat.status().rollover_required:
                        continuation, response = _rollover_and_process_resume(
                            chat, project, state, project_name, config,
                            deadline=deadline, max_rollovers=max_rollovers, recovery_baseline=before_git,
                        )
                        save_state(state_path(project_name), state)
                        continue
                    if deadline is not None and datetime.now().astimezone() >= deadline:
                        raise DeadlineReached("deadline reached before ChatGPT request")
                    trace("chat.prompt", project=project_name, iteration=state.iteration,
                          prompt_chars=len(prompt), project_composer=bool(project.project_name))
                    response = (
                        chat.send_project_message_and_wait_for_response(
                            project.project_name,
                            prompt,
                            timeout_seconds=_remaining_timeout(deadline, config.browser.response_timeout_seconds),
                            quiet_seconds=config.browser.quiet_seconds,
                        )
                        if project.project_name
                        else chat.send_and_wait_for_response(
                            prompt,
                            timeout_seconds=_remaining_timeout(deadline, config.browser.response_timeout_seconds),
                            quiet_seconds=config.browser.quiet_seconds,
                        )
                    )
                    trace("chat.response", project=project_name, iteration=state.iteration,
                          response_chars=len(response), response_tail=response[-1000:])
                    save_response(project_name, response)
                    response = _resolve_execution(chat, response, project, project_name, config, deadline=deadline, state=state)
                    assert_unchanged_before_ci(project.project_root, before_git)
                    verifier = RepositoryVerifier(project.project_root)
                    verification = verifier.compare(before_git)
                    after_git = verification.after
                    recovery_pending = state.pending_ci_fix
                    _trajectory_event(
                        state,
                        project_name,
                        "verification",
                        "repository.observed",
                        changed=not verification.clean_relative_to_baseline,
                        meaningful=verification.meaningful_change,
                        changed_paths=list(verification.changed_paths),
                        recovery_pending=recovery_pending,
                    )
                    if verification.clean_relative_to_baseline and not recovery_pending:
                        should_stop = _handle_no_progress(controller, state)
                        save_state(state_path(project_name), state)
                        if should_stop:
                            return RunResult(state, response)
                        time.sleep(2)
                        continue
                    meaningful = verification.meaningful_change or recovery_pending
                    controller.progress_verified(meaningful=meaningful)
                    if not state.meaningful_progress:
                        should_stop = _handle_no_progress(controller, state)
                        save_state(state_path(project_name), state)
                        if should_stop:
                            return RunResult(state, response)
                        time.sleep(2)
                        continue

                    controller.ci_running()
                    if deadline is not None and datetime.now().astimezone() >= deadline:
                        _mark_dirty_recovery(state, before_git, project=project)
                        controller.stop("deadline reached before LocalCI")
                        save_state(state_path(project_name), state)
                        return RunResult(state, response)

                    ci_ok = _run_local_ci(project, state, deadline=deadline)
                    if ci_ok:
                        controller.ci_passed()
                    if not ci_ok:
                        state.pending_ci_fix = True
                        state.pending_ci_baseline_untracked = list(before_git.untracked_paths)
                        controller.mark_failure("local CI failed")
                        save_state(state_path(project_name), state)
                        if state.consecutive_failures >= controller.limits.max_consecutive_failures:
                            controller.stop("maximum consecutive failures reached")
                            save_state(state_path(project_name), state)
                            return RunResult(state, response)
                        time.sleep(2)
                        continue

                    if deadline is not None and datetime.now().astimezone() >= deadline:
                        _mark_dirty_recovery(state, before_git)
                        controller.stop("deadline reached before commit")
                        save_state(state_path(project_name), state)
                        return RunResult(state, response)

                    try:
                        controller.committing()
                        committed_sha = commit_and_push(
                            project.project_root,
                            before_git,
                            f"lab-agent: iteration {state.iteration}",
                            branch_name=state.branch_name,
                            allow_preexisting_tracked_changes=state.pending_ci_fix,
                            baseline_untracked=_commit_recovery_baseline(state, project),
                        )
                    except GitPushError as exc:
                        _mark_push_failure_recovery(state, before_git, git_snapshot(project.project_root), project=project)
                        controller.mark_failure(
                            f"validated changes could not be pushed; recovery is pending: {exc}"
                        )
                        save_state(state_path(project_name), state)
                        if state.consecutive_failures >= controller.limits.max_consecutive_failures:
                            controller.stop("maximum consecutive failures reached")
                            save_state(state_path(project_name), state)
                            return RunResult(state, response)
                        time.sleep(2)
                        continue

                    if not committed_sha:
                        _mark_dirty_recovery(state, before_git, git_snapshot(project.project_root))
                        controller.mark_failure("working tree changed but Git gate produced no commit")
                        save_state(state_path(project_name), state)
                        if state.consecutive_failures >= controller.limits.max_consecutive_failures:
                            controller.stop("maximum consecutive failures reached")
                            save_state(state_path(project_name), state)
                            return RunResult(state, response)
                        time.sleep(2)
                        continue
                    state.commit_created = True
                    trace("git.commit_push.complete", project=project_name, iteration=state.iteration,
                          commit_sha=committed_sha)
                    controller.pushing()
                    state.last_action = f"committed and pushed {committed_sha}"
                    state.last_commit_sha = committed_sha
                    controller.remote_verified(committed_sha)
                    remote_ci = verify_github_actions(
                        project.repository,
                        committed_sha,
                        timeout_seconds=project.remote_ci_timeout_seconds,
                        poll_seconds=project.remote_ci_poll_seconds,
                    )
                    state.pending_remote_ci_result = remote_ci.summary
                    trace("github_ci.complete", project=project_name, iteration=state.iteration,
                          sha=committed_sha, success=remote_ci.success, summary=remote_ci.summary)
                    if not remote_ci.success:
                        state.pending_remote_ci_fix = True
                        state.pending_remote_ci_sha = committed_sha
                        controller.mark_failure(
                            f"GitHub Actions failed for exact SHA {committed_sha}: {remote_ci.summary}"
                        )
                        save_state(state_path(project_name), state)
                        if state.consecutive_failures >= controller.limits.max_consecutive_failures:
                            controller.stop("maximum consecutive failures reached")
                            save_state(state_path(project_name), state)
                            return RunResult(state, response)
                        time.sleep(2)
                        continue
                    state.pending_remote_ci_fix = False
                    state.pending_remote_ci_sha = None
                    controller.github_ci_verified()
                    controller.mark_success(continue_running=True)
                    state.pending_ci_fix = False
                    state.pending_ci_baseline_untracked = []
                    state.pending_ci_worktree_fingerprint = None
                except Exception as exc:
                    if before_git is not None:
                        try:
                            after_git = git_snapshot(project.project_root)
                            if after_git.worktree_fingerprint != before_git.worktree_fingerprint:
                                _mark_dirty_recovery(state, before_git, after_git, project=project)
                        except Exception:
                            pass
                    if isinstance(exc, RolloverLimitReached):
                        controller.stop(str(exc))
                        save_state(state_path(project_name), state)
                        return RunResult(state)
                    if _is_max_length_error(exc):
                        try:
                            rollover_required = chat.status().rollover_required
                        except Exception:
                            rollover_required = False
                        if rollover_required:
                            try:
                                continuation, response = _rollover_and_process_resume(
                                    chat, project, state, project_name, config,
                                    deadline=deadline, max_rollovers=max_rollovers, recovery_baseline=before_git,
                                )
                                save_state(state_path(project_name), state)
                                continue
                            except Exception as rollover_exc:
                                exc = rollover_exc
                    if _is_browser_connection_error(exc):
                        try:
                            context = session.reconnect()
                            chat = ChatGPTPage(_select_chat_page(context, project))
                            if chat.status().rollover_required:
                                continuation, response = _rollover_and_process_resume(
                                    chat, project, state, project_name, config,
                                    deadline=deadline, max_rollovers=max_rollovers,
                                    recovery_baseline=before_git,
                                )
                                save_response(project_name, response)
                                save_state(state_path(project_name), state)
                                continue
                            chat.assert_ready()
                            if project.project_name and not chat.project_context_present(project.project_name):
                                raise RuntimeError("ChatGPT Project context not detected: " + project.project_name)
                            save_state(state_path(project_name), state)
                            time.sleep(1)
                            continue
                        except Exception as reconnect_exc:
                            exc = reconnect_exc
                    if isinstance(exc, DeadlineReached):
                        controller.stop(str(exc))
                        save_state(state_path(project_name), state)
                        return RunResult(state)
                    controller.mark_failure(str(exc))
                    save_state(state_path(project_name), state)
                    if state.consecutive_failures >= controller.limits.max_consecutive_failures:
                        controller.stop("maximum consecutive failures reached")
                        save_state(state_path(project_name), state)
                        return RunResult(state)
                    time.sleep(2)
                    continue

                save_state(state_path(project_name), state)
                if (
                    (state.iteration - state.iteration_at_last_rollover) >= project.rollover_after_iterations
                    or len(response) >= project.rollover_after_response_chars
                ):
                    if state.rollover_count >= max_rollovers:
                        controller.stop("maximum rollovers reached")
                        save_state(state_path(project_name), state)
                        return RunResult(state, response)
                    try:
                        baseline = git_snapshot(project.project_root)
                        controller.rollover()
                        continuation, _, resume_response = rollover(
                            chat,
                            project,
                            state_dir(project_name),
                            timeout_seconds=_remaining_timeout(deadline, config.browser.response_timeout_seconds),
                            quiet_seconds=config.browser.quiet_seconds,
                        )
                        resume_response = _resolve_execution(chat, resume_response, project, project_name, config, deadline=deadline)
                        save_response(project_name, resume_response)
                        after = git_snapshot(project.project_root)
                        if after.worktree_fingerprint != baseline.worktree_fingerprint:
                            _mark_dirty_recovery(state, baseline, project=project)
                        state.reason = "conversation rolled over and resumed"
                        save_state(state_path(project_name), state)
                    except Exception as exc:
                        controller.block(f"conversation rollover failed: {exc}")
                        save_state(state_path(project_name), state)
                        return RunResult(state, response)

def run_loop(
    config: AppConfig,
    project_name: str,
    *,
    deadline: datetime | None,
    max_iterations: int,
    max_rollovers: int,
) -> RunResult:
    try:
        return _run_loop_impl(config, project_name, deadline=deadline, max_iterations=max_iterations, max_rollovers=max_rollovers)
    except Exception as exc:
        with _project_lock(project_name):
            state = load_state(state_path(project_name))
            if state is not None and state.state in {RunState.WORKING, RunState.STARTING, RunState.ROLLOVER, RunState.ITERATION_SUCCEEDED}:
                state.record_failure(str(exc))
                state.consecutive_failures += 1
                state.transition(RunState.ERROR, reason=str(exc))
                save_state(state_path(project_name), state)
                return RunResult(state)
        raise
