"""Single-iteration and autonomous project loops."""
from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
import time
import uuid

from .browser.chatgpt import ChatGPTPage
from .browser.session import BrowserSession
from .config import AppConfig,ProjectConfig
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

def _select_chat_page(context):
    pages=[p for p in context.pages if p.url.startswith("https://chatgpt.com/")]
    if not pages: raise RuntimeError("No ChatGPT page is attached")
    return pages[0]

def _prepare_state(project_name:str)->AgentState:
    state=load_state(state_path(project_name))
    if state is None or state.state in {RunState.STOPPED,RunState.COMPLETED,RunState.ERROR,RunState.BLOCKED}:
        state=AgentState(project=project_name,run_id=uuid.uuid4().hex)
    return state

def run_once(config:AppConfig,project_name:str)->RunResult:
    project=config.projects.get(project_name)
    if project is None: raise RuntimeError(f"unknown project: {project_name}")
    state=_prepare_state(project_name)
    controller=Controller(state=state,limits=SafetyLimits(max_iterations=state.iteration+1))
    controller.start(); controller.begin_iteration(datetime.now().astimezone())
    save_state(state_path(project_name),state)
    snapshot=inspect_project(project.project_root,project.repository,project.state_files)
    with BrowserSession(config.browser.profile_dir) as session:
        context=session.connect_over_cdp(config.browser.cdp_url)
        chat=ChatGPTPage(_select_chat_page(context)); chat.assert_ready()
        if project.project_name and not chat.project_context_present(project.project_name):
            controller.block(f"ChatGPT Project context not detected: {project.project_name}")
            save_state(state_path(project_name),state); return RunResult(state)
        prompt=build_continuation_prompt(snapshot,project.continuation_message)
        try:
            response=chat.send_and_wait_for_response(prompt,timeout_seconds=config.browser.response_timeout_seconds,quiet_seconds=config.browser.quiet_seconds)
        except Exception as exc:
            controller.mark_failure(str(exc)); save_state(state_path(project_name),state); return RunResult(state)
        save_response(project_name,response); controller.mark_success()
        save_state(state_path(project_name),state)
        return RunResult(state,response)

def run_loop(config:AppConfig,project_name:str,*,deadline:datetime|None,max_iterations:int,max_rollovers:int)->RunResult:
    project=config.projects.get(project_name)
    if project is None: raise RuntimeError(f"unknown project: {project_name}")
    state=_prepare_state(project_name)
    controller=Controller(state=state,limits=SafetyLimits(deadline=deadline,max_iterations=max_iterations,max_rollovers=max_rollovers))
    controller.start(); save_state(state_path(project_name),state)
    with BrowserSession(config.browser.profile_dir) as session:
        context=session.connect_over_cdp(config.browser.cdp_url)
        chat=ChatGPTPage(_select_chat_page(context)); chat.assert_ready()
        if project.project_name and not chat.project_context_present(project.project_name):
            controller.block(f"ChatGPT Project context not detected: {project.project_name}")
            save_state(state_path(project_name),state); return RunResult(state)
        continuation=project.continuation_message
        while True:
            controller.begin_iteration(datetime.now().astimezone())
            if state.state==RunState.STOPPED:
                save_state(state_path(project_name),state); return RunResult(state)
            try:
                snapshot=inspect_project(project.project_root,project.repository,project.state_files)
                prompt=build_continuation_prompt(snapshot,continuation)
                response=chat.send_and_wait_for_response(prompt,timeout_seconds=config.browser.response_timeout_seconds,quiet_seconds=config.browser.quiet_seconds)
                save_response(project_name,response); controller.mark_success()
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
                    continuation, _=rollover(chat,project,state_dir(project_name),
                        timeout_seconds=config.browser.response_timeout_seconds,quiet_seconds=config.browser.quiet_seconds)
                    state.reason="conversation rolled over"; save_state(state_path(project_name),state)
                except Exception as exc:
                    controller.block(f"conversation rollover failed: {exc}")
                    save_state(state_path(project_name),state); return RunResult(state)
