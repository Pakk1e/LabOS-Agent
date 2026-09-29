import pytest

from labos_agent.config import AppConfig, ProjectConfig
from labos_agent.supervisor import ConversationSupervisor, SupervisorError
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


def test_development_requires_human_approval():
    assert can_start_supervisor(ProjectPhase.PLANNING, False)
    assert not can_start_supervisor(ProjectPhase.DEVELOPMENT, False)
    assert can_start_supervisor(ProjectPhase.DEVELOPMENT, True)


def test_phase_normalization_and_invalid_value():
    assert normalize_phase("brainstorm") is ProjectPhase.BRAINSTORM
    with pytest.raises(ValueError):
        normalize_phase("coding")


def test_phase_instructions_are_specific():
    assert "Do not implement product features" in phase_instruction(ProjectPhase.BRAINSTORM)
    assert "documentation" in phase_instruction(ProjectPhase.DOCUMENTATION).lower()
    assert "approved plan" in phase_instruction(ProjectPhase.DEVELOPMENT).lower()


def test_only_development_requires_approval():
    for phase in ProjectPhase:
        if phase is ProjectPhase.DEVELOPMENT:
            assert not can_start_supervisor(phase, False)
        else:
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
    assert loaded.approval_required


def test_lifecycle_state_falls_back_for_existing_projects(tmp_path):
    loaded = load_lifecycle_state(
        tmp_path / "state",
        "legacy",
        fallback_phase=ProjectPhase.PLANNING,
        fallback_approved=False,
    )
    assert loaded.phase is ProjectPhase.PLANNING
    assert not loaded.approved


def test_supervisor_refuses_unapproved_development(tmp_path):
    project = ProjectConfig(
        name="demo",
        repository="example/demo",
        project_root=tmp_path,
        continuation_message="Continue demo",
        lifecycle_phase=ProjectPhase.DEVELOPMENT,
        lifecycle_approved=False,
    )
    supervisor = ConversationSupervisor(AppConfig(projects={"demo": project}), "demo")
    with pytest.raises(SupervisorError, match="human approval"):
        supervisor.run()


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


def test_lifecycle_state_rejects_approval_outside_development():
    with pytest.raises(ValueError, match="only valid in the DEVELOPMENT"):
        LifecycleState(
            phase=ProjectPhase.PLANNING,
            approved=True,
            approved_at="2026-09-29T13:00:00+00:00",
        )


def test_lifecycle_state_rejects_corrupt_approved_file(tmp_path):
    state_root = tmp_path / "state"
    path = state_root / "demo"
    path.mkdir(parents=True)
    (path / "project_lifecycle.json").write_text(
        '{"phase":"PLANNING","approved":true,"approved_at":"2026-09-29T13:00:00+00:00"}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid lifecycle state"):
        load_lifecycle_state(state_root, "demo")


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
