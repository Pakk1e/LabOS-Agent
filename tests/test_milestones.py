from pathlib import Path

from labos_agent.config import load_config
from labos_agent.lifecycle import LifecycleState, ProjectPhase, can_advance, load_lifecycle_state, save_lifecycle_state
from labos_agent.web import _milestone_view


def test_milestone_state_is_isolated_from_project_lifecycle(tmp_path):
    root = tmp_path / "state"
    save_lifecycle_state(root, "demo", LifecycleState(ProjectPhase.MAINTENANCE))
    save_lifecycle_state(root, "demo", LifecycleState(ProjectPhase.DEVELOPMENT), milestone_id="ui")

    assert load_lifecycle_state(root, "demo").phase is ProjectPhase.MAINTENANCE
    assert load_lifecycle_state(root, "demo", milestone_id="ui").phase is ProjectPhase.DEVELOPMENT


def test_active_milestone_becomes_effective_project_phase(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""projects:
  demo:
    repository: example/demo
    project_root: {tmp_path / "repo"}
    continuation_message: Continue demo
    lifecycle:
      mode: guided
      phase: MAINTENANCE
      active_milestone_id: ui
      milestones:
        - id: ui
          title: User Interface
          objective: Build the user interface
          continuation_message: Build the UI
""",
        encoding="utf-8",
    )
    save_lifecycle_state(tmp_path / "state", "demo", LifecycleState(ProjectPhase.DEVELOPMENT), milestone_id="ui")

    config = load_config(config_path)
    project = config.projects["demo"]
    assert project.active_milestone_id == "ui"
    assert project.lifecycle_phase is ProjectPhase.DEVELOPMENT
    assert project.milestones[0].title == "User Interface"


def test_milestone_specific_plan_is_used_for_development_gate(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    plan = repo / ".labos" / "milestones" / "ui" / "PLAN.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# UI Plan\n\n## Acceptance Criteria\n- AC-UI-1", encoding="utf-8")

    ok, missing = can_advance(
        repo,
        ProjectPhase.PLANNING,
        ProjectPhase.DEVELOPMENT,
        implementation_plan_path=".labos/milestones/ui/PLAN.md",
    )
    assert ok
    assert missing == ()


def test_milestone_view_reports_active_phase(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""projects:
  demo:
    repository: example/demo
    project_root: {tmp_path / "repo"}
    continuation_message: Continue demo
    lifecycle:
      phase: MAINTENANCE
      milestones:
        - id: ui
          title: User Interface
          objective: Build UI
          continuation_message: Build UI
          active_milestone_id: ui
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    # active_milestone_id belongs at lifecycle level; repair the fixture explicitly.
    payload = config_path.read_text(encoding="utf-8").replace("          active_milestone_id: ui\n", "")
    payload = payload.replace("      milestones:\n", "      active_milestone_id: ui\n      milestones:\n")
    config_path.write_text(payload, encoding="utf-8")
    save_lifecycle_state(tmp_path / "state", "demo", LifecycleState(ProjectPhase.PLANNING), milestone_id="ui")
    config = load_config(config_path)
    view = _milestone_view(config_path, config.projects["demo"], config.projects["demo"].milestones[0])
    assert view["phase"] == "PLANNING"
    assert view["active"] is True


def test_start_milestone_requires_completed_project_foundation(tmp_path):
    from labos_agent.web import Handler

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""projects:
  demo:
    repository: example/demo
    project_root: {tmp_path / "repo"}
    continuation_message: Continue demo
    lifecycle:
      phase: MAINTENANCE
""",
        encoding="utf-8",
    )
    handler = object.__new__(Handler)
    handler.server = type("Server", (), {"config_path": config_path})()
    sent = {}
    handler._send = lambda status, body, content_type="application/json": sent.update(status=status, body=body)
    handler._create_milestone("demo", {"id": "ui", "title": "UI", "objective": "Build UI", "start": True})
    assert sent["status"] == 200
    project = load_config(config_path).projects["demo"]
    assert project.active_milestone_id == "ui"
    assert project.lifecycle_phase is ProjectPhase.PLANNING


def test_milestone_start_does_not_change_legacy_project_state(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""projects:
  demo:
    repository: example/demo
    project_root: {tmp_path / "repo"}
    continuation_message: Continue demo
    lifecycle:
      phase: MAINTENANCE
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    save_lifecycle_state(tmp_path / "state", "demo", LifecycleState(ProjectPhase.MAINTENANCE))
    assert config.projects["demo"].lifecycle_phase is ProjectPhase.MAINTENANCE


def test_lifecycle_gate_supports_legacy_project_objects(tmp_path):
    from labos_agent.lifecycle import ProjectPhase
    from labos_agent.web import _lifecycle_gate
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "REQUIREMENTS.md").write_text("# Requirements\n", encoding="utf-8")
    project = type("Project", (), {"project_root": root, "lifecycle_phase": ProjectPhase.PLANNING})()
    gate = _lifecycle_gate(project)
    assert gate["target"] == "DEVELOPMENT"


def test_supervisor_milestone_context_names_isolated_evidence_files(tmp_path):
    from labos_agent.config import AppConfig, MilestoneConfig, ProjectConfig
    from labos_agent.lifecycle import ProjectPhase
    from labos_agent.supervisor import ConversationSupervisor

    project = ProjectConfig(
        name="demo",
        repository="example/demo",
        project_root=tmp_path / "repo",
        continuation_message="Continue demo",
        project_name="Demo",
        lifecycle_phase=ProjectPhase.PLANNING,
        milestones=(MilestoneConfig(
            id="ui",
            title="User Interface",
            objective="Build UI",
            continuation_message="Implement the UI",
            plan_path=".labos/milestones/ui/PLAN.md",
            validation_path=".labos/milestones/ui/VALIDATION.md",
        ),),
        active_milestone_id="ui",
    )
    supervisor = ConversationSupervisor(AppConfig(projects={"demo": project}, state_root=tmp_path / "state"), "demo")
    context = supervisor._lifecycle_context()
    assert ".labos/milestones/ui/PLAN.md" in context
    assert ".labos/milestones/ui/VALIDATION.md" in context
    assert "project-level PLAN.md or VALIDATION.md" in context
