"""Single-iteration and autonomous project loops."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import fcntl
import time
import uuid

from .browser.chatgpt import ChatGPTPage
from .browser.session import BrowserSession
from playwright.sync_api import Error as PlaywrightError
from .config import AppConfig
from .ci.local import LocalCI
from .git_gate import GitPushError, snapshot as git_snapshot, assert_unchanged_before_ci, commit_and_push, prepare_repository
from .execution import ExecutionPolicy, ExecutionResult, execute_request, format_execution_results, parse_execution_requests
from .controller import Controller
from .project import build_continuation_prompt, inspect_project
from .rollover import rollover, rollover_from_max_length
from .safety import SafetyLimits
from .state import AgentState, RunState, load_state, save_state


@dataclass(frozen=True)
class RunResult:
    state: AgentState
    response: str | None = None


class DeadlineReached(RuntimeError):
    """The configured hard stop was reached while an operation was in flight."""


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
    return result.success


def _execute_agent_requests(response: str, project) -> tuple[str, bool]:
    if not project.execution_enabled:
        return response, False
    try:
        requests = parse_execution_requests(response)
    except Exception as exc:
        return (
            "LabOS server execution results:\n"
            "[1] action=protocol success=False exit_code=None\n"
            "stdout=\n"
            f"stderr=invalid execution request: {exc}"
        ), True
    if not requests:
        return response, False
    policy = ExecutionPolicy(
        allowed_roots=tuple(path.resolve() for path in project.execution_allowed_roots),
        command_timeout_seconds=project.execution_command_timeout_seconds,
    )
    results = []
    for request in requests:
        try:
            results.append(execute_request(project.project_root, request, policy))
        except Exception as exc:
            results.append(
                ExecutionResult(
                    action=str(request.get("action", "unknown")),
                    success=False,
                    exit_code=None,
                    stdout="",
                    stderr=f"request rejected: {type(exc).__name__}: {exc}",
                )
            )
    return format_execution_results(results), True


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

    if recover and state.state in {RunState.WORKING, RunState.STARTING, RunState.ROLLOVER, RunState.WAITING}:
        state.failure_history.append({
            "timestamp": datetime.now().astimezone().isoformat(),
            "iteration": state.iteration,
            "reason": "recovered stale WORKING state after controller restart",
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
    if state.branch_name == "main":
        state.branch_name = f"agent/{state.run_id[:12]}"
    return state


def _handle_no_progress(controller: Controller, state: AgentState) -> bool:
    controller.mark_no_progress("assistant response completed but repository working tree did not change")
    if state.consecutive_no_progress >= controller.limits.max_consecutive_no_progress:
        controller.stop("maximum consecutive no-progress iterations reached")
        return True
    return False


def _resolve_execution(chat, response: str, project, project_name: str, config: AppConfig, deadline: datetime | None = None) -> str:
    had_execution = False
    for attempt in range(4):
        if deadline is not None and datetime.now().astimezone() >= deadline:
            raise DeadlineReached("deadline reached before server execution")
        execution_feedback, requested_execution = _execute_agent_requests(response, project)
        if requested_execution:
            had_execution = True
            response = chat.send_and_wait_for_response(
                "The LabOS controller executed your requested server operations. Use these real results and continue the implementation; do not claim execution that is not shown here.\n\n"
                + execution_feedback,
                timeout_seconds=_remaining_timeout(deadline, config.browser.response_timeout_seconds),
                quiet_seconds=config.browser.quiet_seconds,
                require_input_available=False,
            )
            save_response(project_name, response)
            continue
        if attempt == 0 and project.execution_enabled:
            tick = chr(96)
            response = chat.send_and_wait_for_response(
                "STOP. You have not produced a server execution request. You do not have direct access to the LabOS project filesystem. "
                "Before doing anything else, issue at least one controlled server operation using a fenced "
                + tick * 3
                + "labos-exec JSON block. Start with read_file on the relevant source file, or run_command with git status. "
                "Wait for the real execution result and then continue the implementation. Do not answer with prose only.",
                timeout_seconds=config.browser.response_timeout_seconds,
                quiet_seconds=config.browser.quiet_seconds,
            )
            save_response(project_name, response)
            continue
        if project.execution_enabled and not had_execution:
            raise RuntimeError("server execution is required but the assistant produced no executable request after the enforcement re-prompt")
        return response
    return response


def _mark_dirty_recovery(state: AgentState, before_git) -> None:
    """Preserve dirty worktree changes for the next recovery iteration."""
    state.pending_ci_fix = True
    state.pending_ci_baseline_untracked = list(before_git.untracked_paths)


def _mark_push_failure_recovery(state: AgentState, before_git) -> None:
    """Preserve rolled-back validated changes for the next recovery iteration."""
    _mark_dirty_recovery(state, before_git)


def _commit_recovery_baseline(state: AgentState) -> set[str] | None:
    if not state.pending_ci_fix:
        return None
    return set(state.pending_ci_baseline_untracked)


def _run_once_impl(config: AppConfig, project_name: str) -> RunResult:
    project = config.projects.get(project_name)
    if project is None:
        raise RuntimeError(f"unknown project: {project_name}")

    with _project_lock(project_name):
        state = _prepare_state(project_name, recover=True)
        controller = Controller(state=state, limits=SafetyLimits(max_iterations=state.iteration + 1))
        controller.start()
        controller.begin_iteration(datetime.now().astimezone())
        save_state(state_path(project_name), state)

        prepare_repository(project.project_root, branch_name=state.branch_name, allow_dirty=state.pending_ci_fix)
        snapshot = inspect_project(project.project_root, project.repository, project.state_files)
        before_git = git_snapshot(project.project_root)

        with BrowserSession(config.browser.profile_dir, cdp_url=config.browser.cdp_url) as session:
            context = session.context
            chat = ChatGPTPage(_select_chat_page(context, project))
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
            try:
                response = chat.send_and_wait_for_response(
                    prompt,
                    timeout_seconds=config.browser.response_timeout_seconds,
                    quiet_seconds=config.browser.quiet_seconds,
                )
            except Exception as exc:
                controller.mark_failure(str(exc))
                save_state(state_path(project_name), state)
                return RunResult(state)

            save_response(project_name, response)
            try:
                response = _resolve_execution(chat, response, project, project_name, config, deadline=None)
                save_response(project_name, response)
                assert_unchanged_before_ci(project.project_root, before_git)
                after_git = git_snapshot(project.project_root)
                if after_git.worktree_fingerprint == before_git.worktree_fingerprint:
                    _handle_no_progress(controller, state)
                    save_state(state_path(project_name), state)
                    return RunResult(state, response)
                ci_ok = _run_local_ci(project, state, deadline=deadline)
            except Exception as exc:
                try:
                    after_git = git_snapshot(project.project_root)
                    if after_git.worktree_fingerprint != before_git.worktree_fingerprint:
                        _mark_dirty_recovery(state, before_git)
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
                controller.mark_failure("local CI failed")
                save_state(state_path(project_name), state)
                return RunResult(state, response)

            try:
                committed_sha = commit_and_push(
                    project.project_root,
                    before_git,
                    f"lab-agent: iteration {state.iteration}",
                    branch_name=state.branch_name,
                    allow_preexisting_tracked_changes=state.pending_ci_fix,
                    baseline_untracked=_commit_recovery_baseline(state),
                )
                if committed_sha:
                    state.last_action = f"committed and pushed {committed_sha}"
                    state.last_commit_sha = committed_sha
            except GitPushError as exc:
                _mark_push_failure_recovery(state, before_git)
                controller.mark_failure(f"validated changes could not be pushed; recovery is pending: {exc}")
                save_state(state_path(project_name), state)
                return RunResult(state, response)
            except Exception as exc:
                _mark_dirty_recovery(state, before_git)
                controller.mark_failure(f"validated changes could not be committed/pushed: {exc}")
                save_state(state_path(project_name), state)
                return RunResult(state, response)

            controller.mark_success()
            state.pending_ci_fix = False
            state.pending_ci_baseline_untracked = []
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
                controller.rollover()
                continuation, _, resume_response = rollover_from_max_length(
                    chat,
                    project,
                    state_dir(project_name),
                    timeout_seconds=_remaining_timeout(deadline, config.browser.response_timeout_seconds),
                    quiet_seconds=config.browser.quiet_seconds,
                )
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
                    prepare_repository(
                        project.project_root,
                        branch_name=state.branch_name,
                        allow_dirty=state.pending_ci_fix,
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
                    if chat.status().rollover_required:
                        controller.rollover()
                        save_state(state_path(project_name), state)
                        continuation, _, response = rollover_from_max_length(
                            chat,
                            project,
                            state_dir(project_name),
                            timeout_seconds=_remaining_timeout(deadline, config.browser.response_timeout_seconds),
                            quiet_seconds=config.browser.quiet_seconds,
                        )
                        save_response(project_name, response)
                        state.reason = "conversation reached hard maximum; rolled over and resumed"
                        save_state(state_path(project_name), state)
                        continue
                    if deadline is not None and datetime.now().astimezone() >= deadline:
                        raise DeadlineReached("deadline reached before ChatGPT request")
                    response = chat.send_and_wait_for_response(
                        prompt,
                        timeout_seconds=config.browser.response_timeout_seconds,
                        quiet_seconds=config.browser.quiet_seconds,
                    )
                    save_response(project_name, response)
                    response = _resolve_execution(chat, response, project, project_name, config, deadline=deadline)
                    assert_unchanged_before_ci(project.project_root, before_git)
                    after_git = git_snapshot(project.project_root)
                    if after_git.worktree_fingerprint == before_git.worktree_fingerprint:
                        should_stop = _handle_no_progress(controller, state)
                        save_state(state_path(project_name), state)
                        if should_stop:
                            return RunResult(state, response)
                        time.sleep(2)
                        continue

                    if deadline is not None and datetime.now().astimezone() >= deadline:
                        _mark_dirty_recovery(state, before_git)
                        controller.stop("deadline reached before LocalCI")
                        save_state(state_path(project_name), state)
                        return RunResult(state, response)

                    ci_ok = _run_local_ci(project, state)
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
                        committed_sha = commit_and_push(
                            project.project_root,
                            before_git,
                            f"lab-agent: iteration {state.iteration}",
                            branch_name=state.branch_name,
                            allow_preexisting_tracked_changes=state.pending_ci_fix,
                            baseline_untracked=_commit_recovery_baseline(state),
                        )
                    except GitPushError as exc:
                        _mark_push_failure_recovery(state, before_git)
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

                    if committed_sha:
                        state.last_action = f"committed and pushed {committed_sha}"
                        state.last_commit_sha = committed_sha
                    controller.mark_success()
                    state.pending_ci_fix = False
                    state.pending_ci_baseline_untracked = []
                except Exception as exc:
                    if before_git is not None:
                        try:
                            after_git = git_snapshot(project.project_root)
                            if after_git.worktree_fingerprint != before_git.worktree_fingerprint:
                                _mark_dirty_recovery(state, before_git)
                        except Exception:
                            pass
                    if _is_max_length_error(exc) and chat.status().rollover_required:
                        try:
                            controller.rollover()
                            continuation, _, response = rollover_from_max_length(
                                chat,
                                project,
                                state_dir(project_name),
                                timeout_seconds=_remaining_timeout(deadline, config.browser.response_timeout_seconds),
                                quiet_seconds=config.browser.quiet_seconds,
                            )
                            save_response(project_name, response)
                            state.reason = "conversation reached hard maximum; rolled over and resumed"
                            save_state(state_path(project_name), state)
                            continue
                        except Exception as rollover_exc:
                            exc = rollover_exc
                    if _is_browser_connection_error(exc):
                        try:
                            context = session.reconnect()
                            chat = ChatGPTPage(_select_chat_page(context, project))
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
                    controller.rollover()
                    save_state(state_path(project_name), state)
                    try:
                        continuation, _, resume_response = rollover(
                            chat,
                            project,
                            state_dir(project_name),
                            timeout_seconds=config.browser.response_timeout_seconds,
                            quiet_seconds=config.browser.quiet_seconds,
                        )
                        save_response(project_name, resume_response)
                        state.reason = "conversation rolled over and resumed"
                        save_state(state_path(project_name), state)
                    except Exception as exc:
                        controller.block(f"conversation rollover failed: {exc}")
                        save_state(state_path(project_name), state)
                        return RunResult(state)

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
            if state is not None and state.state in {RunState.WORKING, RunState.STARTING, RunState.ROLLOVER, RunState.WAITING}:
                state.record_failure(str(exc))
                state.consecutive_failures += 1
                state.transition(RunState.ERROR, reason=str(exc))
                save_state(state_path(project_name), state)
                return RunResult(state)
        raise
