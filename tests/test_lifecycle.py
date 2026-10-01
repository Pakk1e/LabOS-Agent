import pytest

from labos_agent.config import AppConfig, ProjectConfig
from labos_agent.supervisor import ConversationSupervisor, SupervisorError
from labos_agent.run_summary import RunTracker
from labos_agent.lifecycle import (
    LifecycleState,
    ProjectPhase,
    can_start_supervisor,
    load_lifecycle_state,
    next_phase,
    normalize_phase,
    normalize_project_mode,
    phase_instruction,
    save_lifecycle_state,
    can_advance,
)


def test_guided_lifecycle_progression():
    phase = ProjectPhase.IDEA
    phases = []
    while phase is not None:
        phases.append(phase)
        phase = next_phase(phase)
    assert phases == [
        ProjectPhase.IDEA,
        ProjectPhase.BRAINSTORM,
        ProjectPhase.DOCUMENTATION,
        ProjectPhase.PLANNING,
        ProjectPhase.DEVELOPMENT,
        ProjectPhase.VALIDATION,
        ProjectPhase.MAINTENANCE,
    ]


def test_all_lifecycle_phases_start_without_human_approval():
    for phase in ProjectPhase:
        assert can_start_supervisor(phase, False)
        assert can_start_supervisor(phase, True)


def test_phase_normalization_and_invalid_value():
    assert normalize_phase("brainstorm") is ProjectPhase.BRAINSTORM
    with pytest.raises(ValueError):
        normalize_phase("coding")


def test_phase_instructions_are_specific():
    assert "IDEA phase" in phase_instruction(ProjectPhase.IDEA)
    assert "LabOS initial idea" in phase_instruction(ProjectPhase.IDEA)
    assert "Do not begin brainstorming" in phase_instruction(ProjectPhase.IDEA)
    assert "BRAINSTORMING phase" in phase_instruction(ProjectPhase.BRAINSTORM)
    assert "docs/IDEA.md" in phase_instruction(ProjectPhase.BRAINSTORM)
    assert "report DONE" in phase_instruction(ProjectPhase.BRAINSTORM)
    assert "DOCUMENTATION phase" in phase_instruction(ProjectPhase.DOCUMENTATION)
    assert "Do not begin feature implementation" in phase_instruction(ProjectPhase.DOCUMENTATION)
    assert "DEVELOPMENT phase" in phase_instruction(ProjectPhase.DEVELOPMENT)
    assert "Run relevant tests" in phase_instruction(ProjectPhase.DEVELOPMENT)


def test_approval_is_legacy_state_not_a_runtime_gate():
    for phase in ProjectPhase:
        assert can_start_supervisor(phase, False)


def test_project_modes():
    assert normalize_project_mode("guided") == "guided"
    assert normalize_project_mode("specification") == "specification"
    assert normalize_project_mode("existing-repository") == "existing_repository"
    with pytest.raises(ValueError):
        normalize_project_mode("autonomous")


def test_lifecycle_state_round_trip(tmp_path):
    state_root = tmp_path / "state"
    state = LifecycleState(
        phase=ProjectPhase.DEVELOPMENT,
        approved=True,
        approved_at="2026-09-29T13:00:00+00:00",
    )
    save_lifecycle_state(state_root, "demo", state)
    loaded = load_lifecycle_state(state_root, "demo")
    assert loaded == state
    assert not loaded.approval_required


def test_lifecycle_state_falls_back_for_existing_projects(tmp_path):
    loaded = load_lifecycle_state(
        tmp_path / "state",
        "legacy",
        fallback_phase=ProjectPhase.PLANNING,
        fallback_approved=False,
    )
    assert loaded.phase is ProjectPhase.PLANNING
    assert not loaded.approved


def test_supervisor_allows_unapproved_development(tmp_path):
    project = ProjectConfig(
        name="demo",
        repository="example/demo",
        project_root=tmp_path,
        continuation_message="Continue demo",
        lifecycle_phase=ProjectPhase.DEVELOPMENT,
        lifecycle_approved=False,
    )
    supervisor = ConversationSupervisor(AppConfig(projects={"demo": project}), "demo")
    assert supervisor.current_phase is ProjectPhase.DEVELOPMENT


def test_config_reads_runtime_lifecycle_state(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """projects:
  demo:
    repository: example/demo
    project_root: /tmp/demo
    continuation_message: Continue demo
    lifecycle:
      mode: guided
      phase: BRAINSTORM
""",
        encoding="utf-8",
    )
    save_lifecycle_state(
        tmp_path / "state",
        "demo",
        LifecycleState(
            phase=ProjectPhase.PLANNING,
            approved=False,
        ),
    )
    from labos_agent.config import load_config

    project = load_config(config_path).projects["demo"]
    assert project.lifecycle_phase is ProjectPhase.PLANNING
    assert project.project_mode == "guided"


def test_lifecycle_state_accepts_legacy_approval_on_any_phase():
    state = LifecycleState(
        phase=ProjectPhase.PLANNING,
        approved=True,
        approved_at="2026-09-29T13:00:00+00:00",
    )
    assert state.approved


def test_lifecycle_state_accepts_legacy_approval_file(tmp_path):
    state_root = tmp_path / "state"
    path = state_root / "demo"
    path.mkdir(parents=True)
    (path / "project_lifecycle.json").write_text(
        '{"phase":"PLANNING","approved":true,"approved_at":"2026-09-29T13:00:00+00:00"}',
        encoding="utf-8",
    )
    loaded = load_lifecycle_state(state_root, "demo")
    assert loaded.phase is ProjectPhase.PLANNING
    assert loaded.approved
    assert not loaded.approval_required


def test_lifecycle_state_rejects_missing_phase(tmp_path):
    state_root = tmp_path / "state"
    path = state_root / "demo"
    path.mkdir(parents=True)
    (path / "project_lifecycle.json").write_text(
        '{"approved":false,"approved_at":null}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid lifecycle state"):
        load_lifecycle_state(state_root, "demo")


def test_lifecycle_state_rejects_non_boolean_approval(tmp_path):
    state_root = tmp_path / "state"
    path = state_root / "demo"
    path.mkdir(parents=True)
    (path / "project_lifecycle.json").write_text(
        '{"phase":"DEVELOPMENT","approved":"false","approved_at":null}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid lifecycle state"):
        load_lifecycle_state(state_root, "demo")


def test_supervisor_prompt_includes_lifecycle_contract(tmp_path):
    project = ProjectConfig(
        name="demo",
        repository="example/demo",
        project_root=tmp_path,
        continuation_message="Continue demo",
        lifecycle_phase=ProjectPhase.DOCUMENTATION,
        project_mode="specification",
    )
    supervisor = ConversationSupervisor(AppConfig(projects={"demo": project}), "demo")
    prompt = supervisor._bootstrap_prompt()
    assert "CURRENT PHASE: DOCUMENTATION" in prompt
    assert "PROJECT MODE: specification" in prompt
    assert "PHASE WORK CONTRACT:" in prompt
    assert "Do not begin feature implementation" in prompt
    assert "PHASE TRANSITION: LabOS advances automatically" in prompt


def test_config_rejects_non_boolean_legacy_approval(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """projects:
  demo:
    repository: example/demo
    project_root: /tmp/demo
    continuation_message: Continue demo
    lifecycle:
      phase: PLANNING
      approved: "false"
""",
        encoding="utf-8",
    )
    from labos_agent.config import load_config
    with pytest.raises(ValueError, match="must be a boolean"):
        load_config(config_path)


def test_phase_evidence_requires_non_placeholder_documentation(tmp_path):
    from labos_agent.lifecycle import can_advance, phase_evidence
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    for name in ("PRODUCT.md", "REQUIREMENTS.md", "ARCHITECTURE.md", "DECISIONS.md", "ROADMAP.md", "USER_FLOWS.md"):
        (root / "docs" / name).write_text("real project content", encoding="utf-8")
    (root / "AGENTS.md").write_text("project rules", encoding="utf-8")
    (root / "docs" / "PRODUCT.md").write_text("_To be completed._", encoding="utf-8")
    ok, missing = phase_evidence(root, ProjectPhase.DOCUMENTATION)
    assert not ok
    assert "docs/PRODUCT.md" in missing
    (root / "docs" / "PRODUCT.md").write_text("Product definition", encoding="utf-8")
    ok, missing = can_advance(root, ProjectPhase.DOCUMENTATION, ProjectPhase.PLANNING)
    assert ok
    assert not missing


def test_planning_gate_requires_plan_and_acceptance_criteria(tmp_path):
    from labos_agent.lifecycle import can_advance
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "REQUIREMENTS.md").write_text("Requirements", encoding="utf-8")
    ok, missing = can_advance(root, ProjectPhase.PLANNING, ProjectPhase.DEVELOPMENT)
    assert not ok
    assert any("PLAN.md" in item for item in missing)
    (root / "PLAN.md").write_text("# Plan\n\n## Acceptance Criteria\n- AC-1", encoding="utf-8")
    ok, missing = can_advance(root, ProjectPhase.PLANNING, ProjectPhase.DEVELOPMENT)
    assert ok
    assert not missing


def test_validation_gate_requires_validation_report(tmp_path):
    from labos_agent.lifecycle import can_advance
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "REQUIREMENTS.md").write_text("Requirements", encoding="utf-8")
    (root / "PLAN.md").write_text("# Plan\n\n## Acceptance Criteria\n- AC-1 user can save", encoding="utf-8")
    ok, missing = can_advance(root, ProjectPhase.VALIDATION, ProjectPhase.MAINTENANCE)
    assert not ok
    assert any("VALIDATION.md" in item for item in missing)
    (root / "VALIDATION.md").write_text(
        "# Validation\n\n## Validation Results\nPASS\n\n## Acceptance Criteria\nAC-1 PASS",
        encoding="utf-8",
    )
    ok, missing = can_advance(root, ProjectPhase.VALIDATION, ProjectPhase.MAINTENANCE)
    assert ok
    assert not missing


def test_idea_gate_requires_recorded_initial_idea(tmp_path):
    from labos_agent.lifecycle import can_advance
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    idea = root / "docs" / "IDEA.md"
    idea.write_text("# Project Idea\n\nInitial idea", encoding="utf-8")

    ok, missing = can_advance(root, ProjectPhase.IDEA, ProjectPhase.BRAINSTORM)
    assert not ok
    assert any("LabOS initial idea" in item for item in missing)

    idea.write_text(
        "# Project Idea\n\nInitial idea\n\n## LabOS initial idea\n\nGoals and scope",
        encoding="utf-8",
    )
    ok, missing = can_advance(root, ProjectPhase.IDEA, ProjectPhase.BRAINSTORM)
    assert ok
    assert not missing


def test_brainstorm_gate_requires_brainstorm_notes(tmp_path):
    from labos_agent.lifecycle import can_advance
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    idea = root / "docs" / "IDEA.md"
    idea.write_text("# Project Idea\n\nInitial idea", encoding="utf-8")
    ok, missing = can_advance(root, ProjectPhase.BRAINSTORM, ProjectPhase.DOCUMENTATION)
    assert not ok
    assert any("LabOS brainstorming notes" in item for item in missing)
    idea.write_text("# Project Idea\n\nInitial idea\n\n## LabOS brainstorming notes\n\nGoals and scope", encoding="utf-8")
    ok, missing = can_advance(root, ProjectPhase.BRAINSTORM, ProjectPhase.DOCUMENTATION)
    assert ok
    assert not missing


def test_maintenance_gate_validates_each_acceptance_criterion(tmp_path):
    from labos_agent.lifecycle import can_advance
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "REQUIREMENTS.md").write_text("Requirements", encoding="utf-8")
    (root / "PLAN.md").write_text(
        "# Plan\n\n## Acceptance Criteria\n- AC-1 user can save\n- AC-2 user can export",
        encoding="utf-8",
    )
    (root / "VALIDATION.md").write_text(
        "# Validation\n\n## Validation Results\nPASS\n\n## Acceptance Criteria\nAC-1 PASS",
        encoding="utf-8",
    )
    ok, missing = can_advance(root, ProjectPhase.VALIDATION, ProjectPhase.MAINTENANCE)
    assert not ok
    assert any("AC-2" in item for item in missing)
    (root / "VALIDATION.md").write_text(
        "# Validation\n\n## Validation Results\nPASS\n\n## Acceptance Criteria\nAC-1 PASS\nAC-2 PASS",
        encoding="utf-8",
    )
    ok, missing = can_advance(root, ProjectPhase.VALIDATION, ProjectPhase.MAINTENANCE)
    assert ok
    assert not missing


def test_lifecycle_state_path_rejects_traversal(tmp_path):
    from labos_agent.lifecycle import lifecycle_state_path
    with pytest.raises(ValueError, match="invalid lifecycle project name"):
        lifecycle_state_path(tmp_path, "../escape")
    with pytest.raises(ValueError, match="invalid lifecycle project name"):
        lifecycle_state_path(tmp_path, "a/b")


def test_legacy_approved_development_without_timestamp_still_loads(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """projects:
  demo:
    repository: example/demo
    project_root: /tmp/demo
    continuation_message: Continue demo
    lifecycle:
      phase: DEVELOPMENT
      approved: true
""",
        encoding="utf-8",
    )
    from labos_agent.config import load_config
    project = load_config(config_path).projects["demo"]
    assert project.lifecycle_phase is ProjectPhase.DEVELOPMENT
    assert project.lifecycle_approved is True
    assert project.lifecycle_approved_at is None


def test_runtime_lifecycle_state_survives_legacy_config_change(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """projects:
  demo:
    repository: example/demo
    project_root: /tmp/demo
    continuation_message: Continue demo
    lifecycle:
      phase: BRAINSTORM
""",
        encoding="utf-8",
    )
    from labos_agent.config import load_config
    save_lifecycle_state(
        tmp_path / "state",
        "demo",
        LifecycleState(phase=ProjectPhase.DOCUMENTATION),
    )
    assert load_config(config_path).projects["demo"].lifecycle_phase is ProjectPhase.DOCUMENTATION


def test_corrupt_runtime_lifecycle_state_fails_closed(tmp_path):
    state = tmp_path / "state" / "demo"
    state.mkdir(parents=True)
    (state / "project_lifecycle.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid lifecycle state"):
        load_lifecycle_state(tmp_path / "state", "demo")


def test_lifecycle_state_ignores_interrupted_temp_file(tmp_path):
    state = tmp_path / "state" / "demo"
    state.mkdir(parents=True)
    (state / "project_lifecycle.json.tmp").write_text(
        '{"phase":"DEVELOPMENT","approved":true}',
        encoding="utf-8",
    )
    loaded = load_lifecycle_state(
        tmp_path / "state",
        "demo",
        fallback_phase=ProjectPhase.PLANNING,
    )
    assert loaded.phase is ProjectPhase.PLANNING
    assert not loaded.approved


def test_corrupt_lifecycle_state_recovers_from_backup(tmp_path):
    state_root = tmp_path / "state"
    save_lifecycle_state(state_root, "demo", LifecycleState(phase=ProjectPhase.PLANNING))
    save_lifecycle_state(state_root, "demo", LifecycleState(phase=ProjectPhase.DEVELOPMENT, approved=True, approved_at="2026-09-29T00:00:00+00:00"))
    (state_root / "demo" / "project_lifecycle.json").write_text("{broken", encoding="utf-8")
    loaded = load_lifecycle_state(state_root, "demo")
    assert loaded.phase is ProjectPhase.PLANNING
    assert not loaded.approved


def test_acceptance_validation_does_not_match_pass_from_next_criterion(tmp_path):
    from labos_agent.lifecycle import validate_acceptance_criteria
    root = tmp_path / "repo"
    root.mkdir()
    (root / "PLAN.md").write_text("# Plan\n\n## Acceptance Criteria\n- AC-1 first\n- AC-2 second", encoding="utf-8")
    (root / "VALIDATION.md").write_text("# Validation\n\n## Validation Results\nAC-1 NOT PASS\nAC-2 PASS", encoding="utf-8")
    ok, missing = validate_acceptance_criteria(root)
    assert not ok
    assert "AC-1 is not marked PASS" in missing


def test_autonomous_phase_transition_persists_and_starts_fresh_project_chat(tmp_path):
    class FakeTransitionChat:
        def __init__(self):
            self.started = False
            self.project_messages = []

        def start_new_project_chat(self, **kwargs):
            self.started = True

        def project_context_present(self, project_name):
            return project_name == "Testing"

        def assert_ready(self):
            pass

        def send_project_message_and_wait_for_response(self, project_name, message, **kwargs):
            self.project_messages.append((project_name, message))
            return "next phase response"

    project = ProjectConfig(
        name="testing",
        repository="Pakk1e/testing",
        project_root=tmp_path / "repo",
        continuation_message="Continue Testing",
        project_name="Testing",
        lifecycle_phase=ProjectPhase.BRAINSTORM,
    )
    config = AppConfig(
        projects={"testing": project},
        state_root=tmp_path / "state",
    )
    supervisor = ConversationSupervisor(config, "testing")
    chat = FakeTransitionChat()

    response = supervisor._advance_lifecycle_phase(
        chat,
        ProjectPhase.BRAINSTORM,
        ProjectPhase.DOCUMENTATION,
    )

    state = load_lifecycle_state(tmp_path / "state", "testing")
    assert supervisor.current_phase is ProjectPhase.DOCUMENTATION
    assert state.phase is ProjectPhase.DOCUMENTATION
    assert chat.started
    assert chat.project_messages
    assert chat.project_messages[0][0] == "Testing"
    assert "CURRENT PHASE: DOCUMENTATION" in chat.project_messages[0][1]
    assert "PHASE WORK CONTRACT:" in chat.project_messages[0][1]
    assert "PHASE TRANSITION: LabOS advances automatically" in chat.project_messages[0][1]
    assert response == "next phase response"


def test_autonomous_transition_recovery_uses_persisted_phase(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """projects:
  testing:
    repository: Pakk1e/testing
    project_root: /tmp/testing
    continuation_message: Continue Testing
    project_name: Testing
    lifecycle:
      phase: BRAINSTORM
""",
        encoding="utf-8",
    )
    save_lifecycle_state(
        tmp_path / "state",
        "testing",
        LifecycleState(phase=ProjectPhase.DOCUMENTATION),
    )
    from labos_agent.config import load_config
    loaded = load_config(config_path).projects["testing"]
    assert loaded.lifecycle_phase is ProjectPhase.DOCUMENTATION


def test_phase_transition_journals_before_fresh_chat(tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    project_root = tmp_path / "repo"
    project_root.mkdir()
    project = ProjectConfig(
        name="demo",
        repository="example/demo",
        project_root=project_root,
        continuation_message="Continue demo",
        project_name="Demo Project",
        lifecycle_phase=ProjectPhase.DEVELOPMENT,
    )
    config = AppConfig(projects={"demo": project}, state_root=state_root)
    supervisor = ConversationSupervisor(config, "demo")
    monkeypatch.setattr(ConversationSupervisor, "_state_root", state_root)
    from datetime import datetime, timezone
    tracker = RunTracker("demo", 1, datetime.now(timezone.utc))

    def crash_before_chat(*args, **kwargs):
        raise RuntimeError("browser crashed before fresh chat")

    monkeypatch.setattr("labos_agent.supervisor._start_fresh_chat", crash_before_chat)

    with pytest.raises(RuntimeError, match="browser crashed"):
        supervisor._advance_lifecycle_phase(
            object(),
            ProjectPhase.DEVELOPMENT,
            ProjectPhase.VALIDATION,
            tracker=tracker,
            iteration=7,
        )

    lifecycle = load_lifecycle_state(state_root, "demo")
    assert lifecycle.phase is ProjectPhase.VALIDATION
    events = (state_root / "demo" / "runs" / "000001.events.jsonl").read_text(encoding="utf-8")
    assert "phase.transition.prepared" in events
    assert "phase.transition.completed" not in events
