"""Single-iteration and autonomous project loops."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import time
import uuid

from .browser.chatgpt import ChatGPTPage
from .browser.session import BrowserSession
from .config import AppConfig
from .ci.local import LocalCI
from .git_gate import snapshot as git_snapshot, assert_unchanged_before_ci, commit_and_push, prepare_repository
from .execution import ExecutionPolicy, ExecutionResult, execute_request, format_execution_results, parse_execution_requests
from .controller import Controller
from .project import build_continuation_prompt,inspect_project
from .rollover import rollover
from .safety import SafetyLimits
from .state import AgentState,RunState,load_state,save_state

@dataclass(frozen=True)
class RunResult:
    state: AgentState
    response: str|None=None

def state_dir(project:str)->Path: return Path("state")/project
def state_path(project:str)->Path: return state_dir(project)/"current.json"

def save_response(project:str,response:str)->None:
    d=state_dir(project); d.mkdir(parents=True,exist_ok=True)
    (d/"last_response.md").write_text(response.rstrip()+"\n",encoding="utf-8")

def _run_local_ci(project, state: AgentState) -> bool:
    stage = project.ci_stage
    if not stage:
        state.last_ci_result = None
        return True
    commands = project.ci_stages.get(stage)
    if commands is None:
        raise RuntimeError(f"configured CI stage is not defined: {stage}")
    result = LocalCI().run(project=project.name, stage=stage, project_root=project.project_root, commands=commands, timeout_seconds=project.ci_timeout_seconds)
    lines = [f"stage={stage} success={result.success}"]
    for command in result.commands:
        lines.append(f"$ {' '.join(command.command)}")
        if command.stdout: lines.append(command.stdout.rstrip())
        if command.stderr: lines.append(command.stderr.rstrip())
        lines.append(f"exit_code={command.returncode} duration={command.duration_seconds:.2f}s")
    state.last_ci_result = "\n".join(lines)[-12000:]
    return result.success

def _execute_agent_requests(response: str, project) -> tuple[str, bool]:
    if not project.execution_enabled:
        return response, False
    try:
        requests = parse_execution_requests(response)
    except Exception as exc:
        return f"LabOS server execution results:\n[1] action=protocol success=False exit_code=None\nstdout=\nstderr=invalid execution request: {exc}", True
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
            results.append(ExecutionResult(
                action=str(request.get("action", "unknown")),
                success=False,
                exit_code=None,
                stdout="",
                stderr=f"request rejected: {type(exc).__name__}: {exc}",
            ))
    return format_execution_results(results), True

def _select_chat_page(context, project):
    return ChatGPTPage.select_page(
        context,
        project_name=project.project_name,
        project_url=project.project_url,
    )

def _prepare_state(project_name:str)->AgentState:
    state=load_state(state_path(project_name))
    if state is None:
        run_id=uuid.uuid4().hex
        return AgentState(project=project_name,run_id=run_id,branch_name=f"agent/{run_id[:12]}")
    if state.state in {RunState.STOPPED,RunState.COMPLETED,RunState.ERROR,RunState.BLOCKED}:
        # A new run is a new execution, not a recovery of the previous run.
        # Keep failure_history for diagnostics, but reset execution counters and
        # per-run results so stale failures cannot immediately stop the new run.
        state.run_id=uuid.uuid4().hex
        state.branch_name=f"agent/{state.run_id[:12]}"
        state.state=RunState.IDLE
        state.iteration=0
        state.rollover_count=0
        state.consecutive_failures=0
        state.consecutive_no_progress=0
        state.iteration_at_last_rollover=0
        state.started_at=None
        state.stopped_at=None
        state.last_action=None
        state.reason=None
        state.last_ci_result=None
        state.last_progress_result=None
    if state.branch_name == "main":
        state.branch_name=f"agent/{state.run_id[:12]}"
    return state

def _handle_no_progress(controller:Controller, state:AgentState) -> bool:
    controller.mark_no_progress(
        "assistant response completed but repository working tree did not change"
    )
    if state.consecutive_no_progress >= controller.limits.max_consecutive_no_progress:
        controller.stop("maximum consecutive no-progress iterations reached")
        return True
    return False

def _resolve_execution(chat, response: str, project, project_name: str, config: AppConfig) -> str:
    for attempt in range(4):
        execution_feedback, requested_execution = _execute_agent_requests(response, project)
        if requested_execution:
            response = chat.send_and_wait_for_response(
                "The LabOS controller executed your requested server operations. Use these real results and continue the implementation; do not claim execution that is not shown here.\n\n" + execution_feedback,
                timeout_seconds=config.browser.response_timeout_seconds,
                quiet_seconds=config.browser.quiet_seconds,
                require_input_available=False,
            )
            save_response(project_name, response)
            continue
        if attempt == 0 and project.execution_enabled:
            tick=chr(96)
            response = chat.send_and_wait_for_response(
                "STOP. You have not produced a server execution request. You do not have direct access to the LabOS project filesystem. Before doing anything else, issue at least one controlled server operation using a fenced " + tick + tick + tick + "labos-exec JSON block. Start with read_file on the relevant source file, or run_command with git status. Wait for the real execution result and then continue the implementation. Do not answer with prose only.",
                timeout_seconds=config.browser.response_timeout_seconds,
                quiet_seconds=config.browser.quiet_seconds,
            )
            save_response(project_name, response)
            continue
        return response
    return response

def run_once(config:AppConfig,project_name:str)->RunResult:
    project=config.projects.get(project_name)
    if project is None: raise RuntimeError(f"unknown project: {project_name}")
    state=_prepare_state(project_name)
    controller=Controller(state=state,limits=SafetyLimits(max_iterations=state.iteration+1))
    controller.start(); controller.begin_iteration(datetime.now().astimezone())
    save_state(state_path(project_name),state)
    prepare_repository(project.project_root, branch_name=state.branch_name, allow_dirty=state.pending_ci_fix)
    snapshot=inspect_project(project.project_root,project.repository,project.state_files)
    before_git = git_snapshot(project.project_root)
    with BrowserSession(config.browser.profile_dir,cdp_url=config.browser.cdp_url) as session:
        context=session.context
        chat=ChatGPTPage(_select_chat_page(context, project)); chat.assert_ready()
        if project.project_name and not chat.project_context_present(project.project_name):
            controller.block(f"ChatGPT Project context not detected: {project.project_name}")
            save_state(state_path(project_name),state); return RunResult(state)
        prompt=build_continuation_prompt(snapshot,project.continuation_message,state.last_ci_result,state.last_progress_result,project.execution_enabled)
        try:
            response=chat.send_and_wait_for_response(prompt,timeout_seconds=config.browser.response_timeout_seconds,quiet_seconds=config.browser.quiet_seconds)
        except Exception as exc:
            controller.mark_failure(str(exc)); save_state(state_path(project_name),state); return RunResult(state)
        save_response(project_name,response)
        try:
            response = _resolve_execution(chat, response, project, project_name, config)
            save_response(project_name,response)
            assert_unchanged_before_ci(project.project_root, before_git)
            after_git=git_snapshot(project.project_root)
            if after_git.worktree_fingerprint == before_git.worktree_fingerprint:
                _handle_no_progress(controller,state)
                save_state(state_path(project_name),state)
                return RunResult(state,response)
            ci_ok = _run_local_ci(project, state)
            state.pending_ci_fix = not ci_ok
        except Exception as exc:
            controller.mark_failure(str(exc))
            save_state(state_path(project_name),state)
            return RunResult(state,response)
        if not ci_ok:
            controller.mark_failure("local CI failed")
            state.pending_ci_fix = True
            save_state(state_path(project_name),state)
            return RunResult(state,response)
        try:
            committed_sha = commit_and_push(project.project_root, before_git, f"lab-agent: iteration {state.iteration}", branch_name=state.branch_name, allow_preexisting_tracked_changes=bool(state.last_ci_result and "success=False" in state.last_ci_result))
            if committed_sha:
                state.last_action=f"committed and pushed {committed_sha}"
        except Exception as exc:
            controller.mark_failure(f"validated changes could not be committed/pushed: {exc}")
            save_state(state_path(project_name),state)
            return RunResult(state,response)
        controller.mark_success()
        state.pending_ci_fix = False
        save_state(state_path(project_name),state)
        return RunResult(state,response)

def run_loop(config:AppConfig,project_name:str,*,deadline:datetime|None,max_iterations:int,max_rollovers:int)->RunResult:
    project=config.projects.get(project_name)
    if project is None: raise RuntimeError(f"unknown project: {project_name}")
    state=_prepare_state(project_name)
    controller=Controller(state=state,limits=SafetyLimits(deadline=deadline,max_iterations=max_iterations,max_rollovers=max_rollovers))
    controller.start(); save_state(state_path(project_name),state)
    with BrowserSession(config.browser.profile_dir,cdp_url=config.browser.cdp_url) as session:
        context=session.context
        chat=ChatGPTPage(_select_chat_page(context, project)); chat.assert_ready()
        if project.project_name and not chat.project_context_present(project.project_name):
            controller.block(f"ChatGPT Project context not detected: {project.project_name}")
            save_state(state_path(project_name),state); return RunResult(state)
        continuation=project.continuation_message
        while True:
            controller.begin_iteration(datetime.now().astimezone())
            if state.state==RunState.STOPPED:
                save_state(state_path(project_name),state); return RunResult(state)
            try:
                prepare_repository(project.project_root, branch_name=state.branch_name, allow_dirty=state.pending_ci_fix)
                snapshot=inspect_project(project.project_root,project.repository,project.state_files)
                prompt=build_continuation_prompt(snapshot,continuation,state.last_ci_result,state.last_progress_result,project.execution_enabled)
                before_git = git_snapshot(project.project_root)
                response=chat.send_and_wait_for_response(prompt,timeout_seconds=config.browser.response_timeout_seconds,quiet_seconds=config.browser.quiet_seconds)
                save_response(project_name,response)
                response = _resolve_execution(chat, response, project, project_name, config)
                assert_unchanged_before_ci(project.project_root, before_git)
                after_git=git_snapshot(project.project_root)
                if after_git.worktree_fingerprint == before_git.worktree_fingerprint:
                    should_stop=_handle_no_progress(controller,state)
                    save_state(state_path(project_name),state)
                    if should_stop:
                        return RunResult(state,response)
                    time.sleep(2)
                    continue
                if deadline is not None and datetime.now().astimezone() >= deadline:
                    controller.stop("deadline reached before LocalCI")
                    save_state(state_path(project_name),state)
                    return RunResult(state,response)
                ci_ok = _run_local_ci(project, state)
                if not ci_ok:
                    controller.mark_failure("local CI failed")
                    save_state(state_path(project_name),state)
                    if state.consecutive_failures>=controller.limits.max_consecutive_failures:
                        controller.stop("maximum consecutive failures reached")
                        save_state(state_path(project_name),state); return RunResult(state)
                    time.sleep(2); continue
                if deadline is not None and datetime.now().astimezone() >= deadline:
                    controller.stop("deadline reached before commit")
                    save_state(state_path(project_name),state)
                    return RunResult(state,response)
                committed_sha = commit_and_push(project.project_root, before_git, f"lab-agent: iteration {state.iteration}", branch_name=state.branch_name, allow_preexisting_tracked_changes=state.pending_ci_fix)
                if committed_sha:
                    state.last_action=f"committed and pushed {committed_sha}"
                controller.mark_success()
                state.pending_ci_fix = False
            except Exception as exc:
                controller.mark_failure(str(exc))
                save_state(state_path(project_name),state)
                if state.consecutive_failures>=controller.limits.max_consecutive_failures:
                    controller.stop("maximum consecutive failures reached")
                    save_state(state_path(project_name),state); return RunResult(state)
                time.sleep(2); continue
            save_state(state_path(project_name),state)
            if (state.iteration-state.iteration_at_last_rollover)>=project.rollover_after_iterations or len(response)>=project.rollover_after_response_chars:
                controller.rollover(); save_state(state_path(project_name),state)
                try:
                    continuation, _, resume_response=rollover(
                        chat,
                        project,
                        state_dir(project_name),
                        timeout_seconds=config.browser.response_timeout_seconds,
                        quiet_seconds=config.browser.quiet_seconds,
                    )
                    save_response(project_name,resume_response)
                    state.reason="conversation rolled over and resumed"
                    save_state(state_path(project_name),state)
                except Exception as exc:
                    controller.block(f"conversation rollover failed: {exc}")
                    save_state(state_path(project_name),state); return RunResult(state)
