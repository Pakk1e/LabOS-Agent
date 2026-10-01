"""Supervisor conversation lifecycle regression tests."""
from pathlib import Path

from labos_agent.config import AppConfig, ProjectConfig
from labos_agent.supervisor import ConversationSupervisor
from labos_agent.supervisor_memory import SupervisorMemory


class _FakeChat:
    def __init__(self):
        self.resumed = []
        self.page = type("Page", (), {"url": ""})()

    def resume_conversation(self, url, *, project_name=None):
        self.resumed.append((url, project_name))
        self.page.url = url


def test_existing_supervisor_run_resumes_persisted_conversation(tmp_path):
    config = AppConfig(
        state_root=tmp_path / "state",
        projects={
            "demo": ProjectConfig(
                name="demo",
                repository="example/demo",
                project_root=tmp_path / "repo",
                continuation_message="Continue demo",
                project_name="Demo Project",
                project_url="https://chatgpt.com/g/g-p-demo/project",
            )
        },
    )
    supervisor = ConversationSupervisor(config, "demo")
    chat = _FakeChat()
    memory = SupervisorMemory(
        project="demo",
        conversation_url="https://chatgpt.com/g/g-p-demo/c/persisted",
    )
    memory_file = tmp_path / "state" / "demo" / "supervisor_state.json"

    fresh = supervisor._start_or_resume_chat(chat, memory, memory_file)

    assert fresh is False
    assert chat.resumed == [
        ("https://chatgpt.com/g/g-p-demo/c/persisted", "Demo Project")
    ]
    assert not memory_file.exists()


def test_supervisor_creates_fresh_chat_when_no_conversation_exists(tmp_path, monkeypatch):
    config = AppConfig(
        state_root=tmp_path / "state",
        projects={
            "demo": ProjectConfig(
                name="demo",
                repository="example/demo",
                project_root=tmp_path / "repo",
                continuation_message="Continue demo",
                project_name="Demo Project",
                project_url="https://chatgpt.com/g/g-p-demo/project",
                min_fresh_chat_delay_seconds=0,
            )
        },
    )
    supervisor = ConversationSupervisor(config, "demo")
    chat = _FakeChat()
    memory = SupervisorMemory(project="demo")
    memory_file = tmp_path / "state" / "demo" / "supervisor_state.json"
    started = []

    monkeypatch.setattr(
        "labos_agent.supervisor._start_fresh_chat",
        lambda chat, project: started.append((chat, project.name)),
    )

    fresh = supervisor._start_or_resume_chat(chat, memory, memory_file)

    assert fresh is True
    assert started == [(chat, "demo")]
    assert memory.last_fresh_chat_at is not None
