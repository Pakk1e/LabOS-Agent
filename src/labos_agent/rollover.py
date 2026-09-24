"""Conversation rollover and handoff handling."""
from __future__ import annotations
from pathlib import Path
from .browser.chatgpt import ChatGPTPage
from .config import ProjectConfig


def handoff_prompt(project: ProjectConfig) -> str:
    project_name = project.project_name or project.name
    return (
        f"We are approaching the conversation context limit for the {project.name} project. "
        f"Prepare a compact, self-contained handoff for a NEW chat in the SAME ChatGPT Project "
        f"({project_name}). This is a {project.name}-only handoff. Do not carry over or start "
        "work from unrelated projects, repositories, or topics such as Worlds, LabOS-Agent, "
        "Calendar, or other applications unless the current project's repository explicitly "
        "requires it. Include current objective, completed work, exact files/commits changed, "
        "tests/results, blockers, decisions, and the precise next action. Treat the project's "
        "repository and persistent project state as authoritative. Do not start new work in "
        "this response; only produce the handoff."
    )


def persist_handoff(state_dir: Path, handoff: str) -> Path:
    state_dir.mkdir(parents=True,exist_ok=True)
    path=state_dir/"handoff.md"
    path.write_text(handoff.rstrip()+"\n",encoding="utf-8")
    return path


def resume_prompt(project: ProjectConfig, handoff: str) -> str:
    project_name = project.project_name or project.name
    return (
        f"Continue the {project.name} project from this handoff. You are in the ChatGPT Project "
        f"{project_name}. This is a {project.name}-only continuation. Ignore unrelated "
        "conversation context and do not work on Worlds, LabOS-Agent, Calendar, or another "
        "repository unless explicitly required by this project. Treat the repository and "
        "persistent project state as authoritative, verify them before making changes, and "
        "continue with the smallest useful next action.\n\n"
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
    chat.start_new_project_chat(
        project_name=project.project_name,
        project_url=project.project_url,
        selector=project.new_chat_selector,
    )
    continuation=resume_prompt(project,handoff)
    response=chat.send_project_message_and_wait_for_response(
        project.project_name or "",
        continuation,
        timeout_seconds=timeout_seconds,
        quiet_seconds=quiet_seconds,
    )
    return continuation,path,response

def rollover_from_max_length(
    chat: ChatGPTPage,
    project: ProjectConfig,
    state_dir: Path,
    *,
    timeout_seconds: float,
    quiet_seconds: float,
) -> tuple[str, Path, str]:
    """Recover from ChatGPT's hard maximum by starting a fresh Project chat.

    The old conversation cannot accept a handoff once the hard limit UI is
    active, so persist an explicit machine-generated handoff and continue from
    repository state rather than pretending a summary was generated.
    """
    project_name = project.project_name or project.name
    handoff = (
        f"Automatic rollover for {project.name}: the previous ChatGPT conversation "
        "reached the hard maximum-length UI before a handoff could be generated. "
        "No summary of unavailable conversation content is claimed here. Treat the "
        "repository and persistent LabOS state as authoritative, inspect the current "
        "working tree, and continue the existing task from there."
    )
    path = persist_handoff(state_dir, handoff)
    chat.start_new_project_chat(
        project_name=project.project_name,
        project_url=project.project_url,
        selector=project.new_chat_selector,
    )
    continuation = resume_prompt(project, handoff)
    response = chat.send_project_message_and_wait_for_response(
        project_name,
        continuation,
        timeout_seconds=timeout_seconds,
        quiet_seconds=quiet_seconds,
    )
    return continuation, path, response
