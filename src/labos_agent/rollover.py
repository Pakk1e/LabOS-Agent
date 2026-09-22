"""Conversation rollover and handoff handling."""
from __future__ import annotations
from pathlib import Path
from .browser.chatgpt import ChatGPTPage
from .config import ProjectConfig

def handoff_prompt(project: ProjectConfig) -> str:
    return (
        "We are approaching the conversation context limit. Prepare a compact, self-contained "
        "handoff for a NEW chat in the SAME ChatGPT Project. Include current objective, completed "
        "work, exact files/commits changed, tests/results, blockers, decisions, and the precise next "
        "action. Do not start new work in this response; only produce the handoff."
    )

def persist_handoff(state_dir: Path, handoff: str) -> Path:
    state_dir.mkdir(parents=True,exist_ok=True)
    path=state_dir/"handoff.md"
    path.write_text(handoff.rstrip()+"\n",encoding="utf-8")
    return path

def resume_prompt(handoff: str) -> str:
    return (
        "Continue the project from this handoff. Treat repository state as authoritative, "
        "verify it before making changes, and continue with the smallest useful next action.\n\n"
        + handoff
    )

def rollover(chat: ChatGPTPage, project: ProjectConfig, state_dir: Path, *,
             timeout_seconds: float, quiet_seconds: float) -> tuple[str,Path,str]:
    """Generate, persist, and resume from a handoff in a fresh Project chat."""
    handoff=chat.send_and_wait_for_response(
        handoff_prompt(project),
        timeout_seconds=timeout_seconds,
        quiet_seconds=quiet_seconds,
    )
    path=persist_handoff(state_dir,handoff)
    before_url=chat.page.url
    chat.start_new_project_chat(
        project_name=project.project_name,
        project_url=project.project_url,
        selector=project.new_chat_selector,
    )
    continuation=resume_prompt(handoff)
    response=chat.send_project_message_and_wait_for_response(
        project.project_name or "",
        continuation,
        timeout_seconds=timeout_seconds,
        quiet_seconds=quiet_seconds,
    )
    if chat.page.url == before_url:
        raise RuntimeError("Project rollover did not create a new conversation")
    return continuation,path,response
