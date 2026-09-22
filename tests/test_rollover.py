from pathlib import Path

from labos_agent.config import ProjectConfig
from labos_agent.rollover import handoff_prompt, persist_handoff, resume_prompt, rollover


class FakePage:
    url = "https://chatgpt.com/old"


class FakeChat:
    def __init__(self):
        self.page = FakePage()
        self.messages = []
        self.new_chat_called = False

    def send_and_wait_for_response(self, message, **kwargs):
        self.messages.append(message)
        return "HANDOFF CONTENT"

    def start_new_project_chat(self, **kwargs):
        self.new_chat_called = True
        self.page.url = "https://chatgpt.com/new"

    def send_project_message_and_wait_for_response(self, project_name, message, **kwargs):
        self.messages.append(message)
        return "RESUME RESPONSE"


def test_rollover_persists_and_resumes(tmp_path):
    project = ProjectConfig(
        name="weather",
        repository="Pakk1e/VilaPro-Weather",
        project_root=tmp_path,
        continuation_message="Continue Weather",
        project_name="Vadovsky Tech — Weather",
        project_url="https://chatgpt.com/g/g-p-weather/project",
    )
    chat = FakeChat()
    continuation, path, response = rollover(
        chat,
        project,
        tmp_path / "state",
        timeout_seconds=10,
        quiet_seconds=0,
    )

    assert chat.new_chat_called
    assert chat.page.url.endswith("/new")
    assert path.read_text(encoding="utf-8") == "HANDOFF CONTENT\n"
    assert handoff_prompt(project) in chat.messages
    assert continuation == resume_prompt(project, "HANDOFF CONTENT")
    assert continuation in chat.messages
    assert response == "RESUME RESPONSE"


def test_handoff_is_project_scoped():
    project = ProjectConfig(
        name="weather",
        repository="Pakk1e/VilaPro-Weather",
        project_root=Path("."),
        continuation_message="Continue Weather",
        project_name="Vadovsky Tech — Weather",
    )
    handoff = handoff_prompt(project)
    resume = resume_prompt(project, "HANDOFF CONTENT")
    for text in (handoff, resume):
        assert "weather project" in text.lower()
        assert "Vadovsky Tech — Weather" in text
        assert "Worlds" in text
        assert "LabOS-Agent" in text
        assert "repository" in text.lower()


def test_persist_handoff_is_newline_terminated(tmp_path):
    path = persist_handoff(tmp_path, "handoff")
    assert path.read_text(encoding="utf-8") == "handoff\n"
