from datetime import datetime, timedelta, timezone
from pathlib import Path

from labos_agent.config import AppConfig, BrowserConfig, ProjectConfig
from labos_agent.github_observer import GitHubObservation
from labos_agent.lifecycle import ProjectPhase, phase_instruction
from labos_agent.response_protocol import parse_labos_response
from labos_agent.supervisor import ConversationSupervisor
from labos_agent.supervisor_memory import SupervisorMemory, save_memory


def _project(**overrides):
    values = dict(
        name="test",
        repository="owner/test",
        project_root=Path("/tmp/test"),
        continuation_message="Continue test",
        project_name="Test",
        initial_idea="Build a useful test project.",
        brainstorm_notes="Explore the product direction.",
    )
    values.update(overrides)
    return ProjectConfig(**values)


def _supervisor(project=None):
    project = project or _project()
    config = AppConfig(browser=BrowserConfig(response_timeout_seconds=1200), projects={"test": project})
    return ConversationSupervisor(config, "test")


def test_phase_instructions_are_direct_and_explicit():
    assert "BRAINSTORMING phase" in phase_instruction(ProjectPhase.BRAINSTORM)
    assert "docs/IDEA.md" in phase_instruction(ProjectPhase.BRAINSTORM)
    assert "report DONE" in phase_instruction(ProjectPhase.BRAINSTORM)
    assert "DOCUMENTATION phase" in phase_instruction(ProjectPhase.DOCUMENTATION)
    assert "Do not begin feature implementation" in phase_instruction(ProjectPhase.DOCUMENTATION)


def test_bootstrap_contains_idea_notes_and_current_phase():
    supervisor = _supervisor(_project(lifecycle_phase=ProjectPhase.BRAINSTORM))
    prompt = supervisor._bootstrap_prompt()
    assert "CURRENT PHASE: BRAINSTORM" in prompt
    assert "INITIAL IDEA:" in prompt
    assert "Build a useful test project." in prompt
    assert "EXISTING BRAINSTORMING NOTES:" in prompt
    assert "Explore the product direction." in prompt
    assert "do not artificially limit the work" in prompt


def test_no_progress_requires_repeated_unchanged_continue():
    supervisor = _supervisor()
    response = """<LABOS_STATE>
STATE => CONTINUE
TASK_STATUS => IN_PROGRESS
CURRENT_COMMIT => UNKNOWN
COMMIT_STATUS => NONE
REPOSITORY_CHANGED => NO
LOCAL_TESTS => NOT_RUN
CI_RUN => NONE
CI_RUN_ID => NONE
CI_WORKFLOW => NONE
CI_STATUS => NONE
NEXT_ACTION => CONTINUE_WORK
</LABOS_STATE>
(STATE CONTINUE STATE)"""
    analysis = parse_labos_response(response)
    observed = GitHubObservation(
        branch="main",
        commit_sha="abc123",
        ci_run_id=None,
        ci_status=None,
        ci_conclusion=None,
        ci_sha=None,
        ci_name=None,
        ci_created_at=None,
        ci_url=None,
    )
    assert not supervisor._is_no_progress(analysis, observed)
    supervisor._last_progress_commit = observed.commit_sha
    assert supervisor._is_no_progress(analysis, observed)
    assert supervisor.project.max_no_progress_iterations == 5


def test_fresh_chat_cooldown_survives_supervisor_restart(tmp_path, monkeypatch):
    project = _project(min_fresh_chat_delay_seconds=420)
    config = AppConfig(
        browser=BrowserConfig(response_timeout_seconds=1200),
        projects={"test": project},
        state_root=tmp_path / "state",
    )
    memory_file = tmp_path / "state" / "test" / "supervisor_state.json"
    started_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    save_memory(
        memory_file,
        SupervisorMemory(project="test", last_fresh_chat_at=started_at.isoformat()),
    )

    supervisor = ConversationSupervisor(config, "test")
    memory = SupervisorMemory(project="test", last_fresh_chat_at=started_at.isoformat())
    supervisor._load_persisted_fresh_chat_time(memory)

    sleeps = []
    monkeypatch.setattr("labos_agent.supervisor.time.sleep", sleeps.append)
    supervisor._wait_before_fresh_chat()

    assert sleeps
    assert 380 <= sleeps[0] <= 400


def test_fresh_chat_start_persists_timestamp(tmp_path, monkeypatch):
    project = _project(min_fresh_chat_delay_seconds=0)
    config = AppConfig(
        browser=BrowserConfig(response_timeout_seconds=1200),
        projects={"test": project},
        state_root=tmp_path / "state",
    )
    supervisor = ConversationSupervisor(config, "test")
    memory = SupervisorMemory(project="test")
    memory_file = tmp_path / "state" / "test" / "supervisor_state.json"

    supervisor._mark_fresh_chat_started(memory, memory_file)

    persisted = SupervisorMemory(**__import__("json").loads(memory_file.read_text(encoding="utf-8")))
    assert persisted.last_fresh_chat_at is not None


def test_no_progress_stops_on_exact_configured_iteration(tmp_path):
    supervisor = _supervisor(_project(max_no_progress_iterations=5))
    response = """<LABOS_STATE>
STATE => CONTINUE
TASK_STATUS => IN_PROGRESS
CURRENT_COMMIT => UNKNOWN
COMMIT_STATUS => NONE
REPOSITORY_CHANGED => NO
LOCAL_TESTS => NOT_RUN
CI_RUN => NONE
CI_RUN_ID => NONE
CI_WORKFLOW => NONE
CI_STATUS => NONE
NEXT_ACTION => CONTINUE_WORK
</LABOS_STATE>
(STATE CONTINUE STATE)"""
    analysis = parse_labos_response(response)
    observed = GitHubObservation(
        branch="main",
        commit_sha="abc123",
        ci_run_id=None,
        ci_status=None,
        ci_conclusion=None,
        ci_sha=None,
        ci_name=None,
        ci_created_at=None,
        ci_url=None,
    )
    # Mirror the baseline captured by _run_locked before the first response.
    supervisor._last_progress_commit = observed.commit_sha
    assert supervisor._record_no_progress(analysis, observed) is False
    for _ in range(3):
        assert supervisor._record_no_progress(analysis, observed) is False
    assert supervisor._record_no_progress(analysis, observed) is True
    assert supervisor._no_progress_iterations == 5


def test_runtime_defaults_match_operator_constraints():
    project = _project()
    assert project.min_fresh_chat_delay_seconds == 420
    assert project.response_to_next_message_delay_seconds == 45
    assert project.max_no_progress_iterations == 5
