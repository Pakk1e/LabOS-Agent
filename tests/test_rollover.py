from pathlib import Path

from labos_agent.config import ProjectConfig
from labos_agent.rollover import handoff_prompt, persist_handoff, persistent_resume_context, resume_prompt, rollover
from labos_agent.supervisor import _start_fresh_chat


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
    (tmp_path / "state" / "supervisor_state.json").parent.mkdir(parents=True)
    (tmp_path / "state" / "supervisor_state.json").write_text(
        '{"last_analysis":{"state":"WAIT_CI","current_commit":"abc123","ci_run":42,"ci_status":"QUEUED"},'
        '"last_observed_branch":"main","last_observed_commit":"abc123",'
        '"last_observed_ci_run":42,"last_observed_ci_status":"completed",'
        '"last_observed_ci_conclusion":"success"}',
        encoding="utf-8",
    )
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
    assert continuation == resume_prompt(project, "HANDOFF CONTENT", persistent_resume_context(tmp_path / "state"))
    assert "last reported state: WAIT_CI" in continuation
    assert "observed GitHub HEAD: abc123" in continuation
    assert "AUTHORITATIVE HANDOFF:" in continuation
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


def test_persistent_resume_context_handles_missing_state(tmp_path):
    context = persistent_resume_context(tmp_path / "state")
    assert "unavailable" in context.lower()

class FakeStartupChat:
    def __init__(self):
        self.new_chat_called = False
        self.project_context = True
        self.ready_called = False

    def start_new_project_chat(self, **kwargs):
        self.new_chat_called = True
        assert kwargs["project_name"] == "Testing"
        assert kwargs["project_url"] is None
        assert kwargs["selector"] is None

    def project_context_present(self, project_name):
        return self.project_context and project_name == "Testing"

    def assert_ready(self):
        self.ready_called = True


def test_new_supervisor_run_starts_fresh_project_chat(tmp_path):
    project = ProjectConfig(
        name="testing",
        repository="Pakk1e/testing",
        project_root=tmp_path,
        continuation_message="Continue Testing",
        project_name="Testing",
    )
    chat = FakeStartupChat()

    _start_fresh_chat(chat, project)

    assert chat.new_chat_called
    assert chat.ready_called


def test_supervisor_without_project_name_only_checks_chat_ready(tmp_path):
    project = ProjectConfig(
        name="testing",
        repository="Pakk1e/testing",
        project_root=tmp_path,
        continuation_message="Continue Testing",
    )
    chat = FakeStartupChat()

    _start_fresh_chat(chat, project)

    assert not chat.new_chat_called
    assert chat.ready_called

