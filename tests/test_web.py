from pathlib import Path
from threading import Thread

from labos_agent.config import load_config
from labos_agent.web import _config_payload, _write_config, _project_view


def test_web_project_view_reads_supervisor_memory(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """projects:
  weather:
    repository: Pakk1e/VilaPro-Weather
    project_root: /home/park-pro/VilaPro-Weather
    continuation_message: Continue weather
""",
        encoding="utf-8",
    )
    state = tmp_path / "state" / "weather"
    state.mkdir(parents=True)
    (state / "supervisor_state.json").write_text(
        '{"project":"weather","last_observed_commit":"abc123","last_observed_branch":"main",'
        '"last_observed_ci_run":204,"last_observed_ci_status":"QUEUED",'
        '"last_analysis":{"state":"WAIT_CI","task_status":"IN_PROGRESS","next_action":"WAIT_FOR_CI"}}',
        encoding="utf-8",
    )
    config = load_config(config_path)
    view = _project_view(config_path, "weather", config.projects["weather"])
    assert view["repository"] == "Pakk1e/VilaPro-Weather"
    assert view["commit"] == "abc123"
    assert view["ci_run"] == 204
    assert view["state"] == "WAIT_CI"
    assert view["next_action"] == "WAIT_FOR_CI"


def test_web_config_write_round_trip(tmp_path):
    config_path = tmp_path / "config.yaml"
    payload = {"browser": {"cdp_url": "http://127.0.0.1:9222"}, "projects": {}}
    _write_config(config_path, payload)
    loaded = _config_payload(config_path)
    assert loaded["browser"]["cdp_url"] == "http://127.0.0.1:9222"
    assert loaded["projects"] == {}


def test_run_history_and_process_status(tmp_path):
    from labos_agent.web import _run_history, _process_status

    root = tmp_path / "state" / "weather" / "runs"
    root.mkdir(parents=True)
    (root / "000007.json").write_text(
        '{"project":"weather","run_number":7,"result":"DONE_VERIFIED","iterations":[]}',
        encoding="utf-8",
    )
    assert _run_history(tmp_path / "config.yaml", "weather")[0]["run_number"] == 7
    status = _process_status(tmp_path / "config.yaml", "weather")
    assert status["running"] is False


def test_project_repository_validation_accepts_normal_github_names():
    from labos_agent.web import _REPO_RE

    assert _REPO_RE.fullmatch("Pakk1e/VilaPro-Weather")
    assert _REPO_RE.fullmatch("org/repo-with-s")
    assert not _REPO_RE.fullmatch("org/repo name")
    assert not _REPO_RE.fullmatch("/repo")


def test_run_events_reads_persisted_timeline(tmp_path):
    from labos_agent.web import _run_events

    root = tmp_path / "state" / "weather" / "runs"
    root.mkdir(parents=True)
    (root / "000007.events.jsonl").write_text(
        '{"ts":"2026-09-29T10:00:00Z","event":"run.start","state":"WORKING"}\n'
        '{"ts":"2026-09-29T10:01:00Z","event":"iteration.complete","state":"WAITING_CI"}\n',
        encoding="utf-8",
    )
    events = _run_events(tmp_path / "config.yaml", "weather", 7)
    assert [event["event"] for event in events] == ["run.start", "iteration.complete"]


def test_run_events_missing_timeline_is_empty(tmp_path):
    from labos_agent.web import _run_events

    assert _run_events(tmp_path / "config.yaml", "weather", 7) == []

def test_process_record_is_cleared_when_pid_is_stale(tmp_path):
    from labos_agent.web import _process_record, _process_status

    record = _process_record(tmp_path / "config.yaml", "weather")
    record.parent.mkdir(parents=True)
    record.write_text(
        '{"pid": 999999999, "project": "weather", "config": "' + str(tmp_path / "config.yaml") + '"}',
        encoding="utf-8",
    )
    assert _process_status(tmp_path / "config.yaml", "weather")["running"] is False
    assert not record.exists()


def test_persist_process_writes_project_identity(tmp_path):
    from labos_agent.web import _persist_process, _process_record
    class FakeProcess:
        pid = 12345

    config = tmp_path / "config.yaml"
    _persist_process(config, "weather", FakeProcess())
    record = _process_record(config, "weather")
    data = __import__("json").loads(record.read_text(encoding="utf-8"))
    assert data == {"pid": 12345, "project": "weather", "config": str(config)}


def test_terminate_recovered_process_uses_pid(monkeypatch):
    from labos_agent.web import _terminate_process

    calls = []
    monkeypatch.setattr("labos_agent.web.os.kill", lambda pid, sig: calls.append((pid, sig)))
    _terminate_process("weather", 4321)
    assert calls == [(4321, 15)]


def test_latest_run_events_reads_newest_event_stream(tmp_path):
    from labos_agent.web import _latest_run_events

    root = tmp_path / "state" / "weather" / "runs"
    root.mkdir(parents=True)
    (root / "000002.events.jsonl").write_text(
        '{"event":"old"}\n',
        encoding="utf-8",
    )
    (root / "000003.events.jsonl").write_text(
        '{"event":"new1"}\n{"event":"new2"}\n',
        encoding="utf-8",
    )
    events = _latest_run_events(tmp_path / "config.yaml", "weather")
    assert [event["event"] for event in events] == ["new1", "new2"]


def test_ci_action_rejects_unknown_action():
    from labos_agent.web import _ci_action
    import pytest
    with pytest.raises(ValueError, match="unsupported CI action"):
        _ci_action("Pakk1e/test", 123, "delete")


def test_ci_action_invokes_gh(monkeypatch):
    from labos_agent.web import _ci_action
    calls = []
    monkeypatch.setattr("labos_agent.web.subprocess.run", lambda *args, **kwargs: calls.append((args, kwargs)))
    _ci_action("Pakk1e/test", 123, "rerun")
    assert calls[0][0][0] == ["gh", "run", "rerun", "123", "--repo", "Pakk1e/test"]


def test_event_snapshot_changes_when_run_event_stream_changes(tmp_path):
    from labos_agent.web import _event_snapshot

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """projects:
  weather:
    repository: Pakk1e/VilaPro-Weather
    project_root: /home/park-pro/VilaPro-Weather
    continuation_message: Continue weather
""",
        encoding="utf-8",
    )
    runs = tmp_path / "state" / "weather" / "runs"
    runs.mkdir(parents=True)
    before = _event_snapshot(config_path)
    event_file = runs / "000001.events.jsonl"
    event_file.write_text('{"event":"run.start"}\n', encoding="utf-8")
    after = _event_snapshot(config_path)
    assert before["weather"] != after["weather"]


def test_sse_event_uses_event_stream_format():
    from labos_agent.web import _sse_event

    payload = _sse_event("project_changed", {"projects": ["weather"]}, retry=5000).decode("utf-8")
    assert payload == (
        'retry: 5000\n'
        'event: project_changed\n'
        'data: {"projects":["weather"]}\n\n'
    )



def test_initialize_new_repository_commits_and_pushes_bootstrap(tmp_path, monkeypatch):
    from labos_agent.web import _initialize_new_repository

    root = tmp_path / "repo"
    root.mkdir()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[-2:] == ["rev-parse", "HEAD"]:
            return type("Result", (), {"stdout": "abc123\n"})()
        return type("Result", (), {"stdout": "", "stderr": ""})()
    monkeypatch.setattr("labos_agent.web.subprocess.run", fake_run)
    commit = _initialize_new_repository(str(root), project_name="Demo")
    assert commit == "abc123"
    assert [call[3:] for call in calls[:-1]] == [
        ["config", "user.name", "LabOS-Agent"],
        ["config", "user.email", "labos-agent@localhost"],
        ["add", "docs", "AGENTS.md"],
        ["commit", "-m", "Initialize Demo with LabOS project structure"],
        ["branch", "-M", "main"],
        ["push", "-u", "origin", "main"],
    ]


def test_create_project_publishes_bootstrap_commit(tmp_path, monkeypatch):
    from labos_agent.web import Handler

    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects: {}\n", encoding="utf-8")
    root = tmp_path / "repo"
    handler = object.__new__(Handler)
    handler.server = type("Server", (), {"config_path": config_path})()
    sent = {}
    handler._send = lambda status, body, content_type="application/json": sent.update(status=status, body=body)

    monkeypatch.setattr("labos_agent.web._create_github_repository", lambda repository, visibility: None)
    monkeypatch.setattr("labos_agent.web._clone_github_repository", lambda repository, root_path: Path(root_path).mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr("labos_agent.web._initialize_new_repository", lambda root_path, project_name: "abc123")

    handler._create_project({
        "name": "demo",
        "repository": "example/demo",
        "project_root": str(root),
        "project_mode": "guided",
        "initial_idea": "Build an engineering workspace",
        "create_repository": True,
    })

    assert sent["status"] == 201
    assert sent["body"]["bootstrap_commit"] == "abc123"
    assert (root / "docs" / "IDEA.md").exists()
    assert (root / "AGENTS.md").exists()

def test_create_github_repository_uses_requested_visibility(monkeypatch):
    from labos_agent.web import _create_github_repository
    calls = []
    monkeypatch.setattr(
        "labos_agent.web.subprocess.run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    _create_github_repository("Pakk1e/NewProject", "private")
    assert calls[0][0][0] == ["gh", "repo", "create", "Pakk1e/NewProject", "--private"]


def test_clone_github_repository_requires_empty_target(tmp_path):
    from labos_agent.web import _clone_github_repository
    target = tmp_path / "repo"
    target.mkdir()
    (target / "existing.txt").write_text("keep", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="project_root must be empty"):
        _clone_github_repository("Pakk1e/NewProject", str(target))


def test_clone_github_repository_uses_gh_clone(monkeypatch, tmp_path):
    from labos_agent.web import _clone_github_repository
    calls = []
    monkeypatch.setattr(
        "labos_agent.web.subprocess.run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    target = tmp_path / "repo"
    _clone_github_repository("Pakk1e/NewProject", str(target))
    assert calls[0][0][0] == ["gh", "repo", "clone", "Pakk1e/NewProject", str(target)]


def test_existing_repository_creation_does_not_bootstrap_files(tmp_path):
    from labos_agent.web import Handler

    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects: {}\n", encoding="utf-8")
    root = tmp_path / "existing"
    root.mkdir()
    (root / ".git").mkdir()
    marker = root / "README.md"
    marker.write_text("# Existing repository\\n", encoding="utf-8")

    handler = object.__new__(Handler)
    handler.server = type("Server", (), {"config_path": config_path})()

    sent = {}
    handler._send = lambda status, body, content_type="application/json": sent.update(
        status=status, body=body
    )

    result = handler._create_project({
        "name": "existing",
        "repository": "Pakk1e/existing",
        "project_root": str(root),
        "project_mode": "existing_repository",
        "initial_idea": "Inspect this repository first",
    })
    assert sent["status"] == 201
    assert result is None
    assert marker.read_text(encoding="utf-8") == "# Existing repository\\n"
    assert not (root / "docs").exists()
    assert not (root / "AGENTS.md").exists()

    config = load_config(config_path)
    assert config.projects["existing"].lifecycle_phase.name == "DOCUMENTATION"


def test_existing_repository_mode_rejects_repository_creation(tmp_path):
    from labos_agent.web import Handler

    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects: {}\n", encoding="utf-8")
    root = tmp_path / "existing"
    root.mkdir()

    handler = object.__new__(Handler)
    handler.server = type("Server", (), {"config_path": config_path})()

    sent = {}
    handler._send = lambda status, body, content_type="application/json": sent.update(
        status=status, body=body
    )

    handler._create_project({
        "name": "existing",
        "repository": "Pakk1e/existing",
        "project_root": str(root),
        "project_mode": "existing_repository",
        "create_repository": True,
    })

    assert sent["status"] == 400
    assert "cannot create or clone" in sent["body"]["error"]


def _make_lifecycle_handler(tmp_path, phase="PLANNING"):
    from labos_agent.web import Handler
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""projects:
  demo:
    repository: example/demo
    project_root: {tmp_path / "repo"}
    continuation_message: Continue demo
    lifecycle:
      mode: guided
      phase: {phase}
""",
        encoding="utf-8",
    )
    handler = object.__new__(Handler)
    handler.server = type("Server", (), {"config_path": config_path})()
    sent = {}
    handler._send = lambda status, body, content_type="application/json": sent.update(
        status=status, body=body
    )
    return handler, config_path, sent


def test_lifecycle_rejects_combined_transition_and_development_approval(tmp_path):
    handler, config_path, sent = _make_lifecycle_handler(tmp_path)
    handler._update_lifecycle("demo", {"phase": "DEVELOPMENT", "approved": True})
    assert sent["status"] == 409
    assert "advance to DEVELOPMENT" in sent["body"]["error"]
    assert load_config(config_path).projects["demo"].lifecycle_phase.name == "PLANNING"


def test_lifecycle_rejects_invalid_transition_without_saving_notes(tmp_path):
    handler, config_path, sent = _make_lifecycle_handler(tmp_path)
    handler._update_lifecycle(
        "demo",
        {"phase": "VALIDATION", "brainstorm_notes": "must not be saved"},
    )
    assert sent["status"] == 409
    assert "lifecycle evidence gate" in sent["body"]["error"]
    assert load_config(config_path).projects["demo"].brainstorm_notes == ""


def test_lifecycle_requires_separate_development_transition_and_approval(tmp_path):
    handler, config_path, sent = _make_lifecycle_handler(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    (repo / "docs" / "REQUIREMENTS.md").write_text("Requirements", encoding="utf-8")
    (repo / "PLAN.md").write_text("# Plan\n\n## Acceptance Criteria\n- AC-1", encoding="utf-8")
    handler._update_lifecycle("demo", {"phase": "DEVELOPMENT"})
    assert sent["status"] == 200
    assert load_config(config_path).projects["demo"].lifecycle_phase.name == "DEVELOPMENT"
    assert not load_config(config_path).projects["demo"].lifecycle_approved

    handler._update_lifecycle("demo", {"approved": True})
    assert sent["status"] == 200
    project = load_config(config_path).projects["demo"]
    assert project.lifecycle_approved
    assert project.lifecycle_approved_at


def test_existing_repository_mode_requires_local_git_repository(tmp_path):    from labos_agent.web import Handler

    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects: {}\n", encoding="utf-8")
    root = tmp_path / "not-a-repo"
    root.mkdir()

    handler = object.__new__(Handler)
    handler.server = type("Server", (), {"config_path": config_path})()
    sent = {}
    handler._send = lambda status, body, content_type="application/json": sent.update(
        status=status, body=body
    )

    handler._create_project({
        "name": "existing",
        "repository": "Pakk1e/existing",
        "project_root": str(root),
        "project_mode": "existing_repository",
    })

    assert sent["status"] == 400
    assert "local Git repository" in sent["body"]["error"]


def test_lifecycle_records_transition_and_approval_history(tmp_path):
    handler, config_path, sent = _make_lifecycle_handler(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    (repo / "docs" / "REQUIREMENTS.md").write_text("Requirements", encoding="utf-8")
    (repo / "PLAN.md").write_text("# Plan\n\n## Acceptance Criteria\n- AC-1", encoding="utf-8")
    handler._update_lifecycle("demo", {"phase": "DEVELOPMENT"})
    handler._update_lifecycle("demo", {"approved": True})
    history = (tmp_path / "state" / "demo" / "lifecycle_history.jsonl").read_text(encoding="utf-8").splitlines()
    assert '"event": "phase_transition"' in history[0]
    assert '"event": "approval_granted"' in history[1]


def test_project_view_exposes_lifecycle_gate(tmp_path):
    config_path = tmp_path / "config.yaml"
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "REQUIREMENTS.md").write_text("Requirements", encoding="utf-8")
    (repo / "PLAN.md").write_text("# Plan\n\n## Acceptance Criteria\n- AC-1", encoding="utf-8")
    config_path.write_text(
        f"""projects:
  demo:
    repository: example/demo
    project_root: {repo}
    continuation_message: Continue demo
    lifecycle:
      phase: PLANNING
""",
        encoding="utf-8",
    )
    project = load_config(config_path).projects["demo"]
    view = _project_view(config_path, "demo", project)
    assert view["lifecycle_gate"]["target"] == "DEVELOPMENT"
    assert view["lifecycle_gate"]["satisfied"] is True


def test_lifecycle_rejects_non_boolean_approval_payload(tmp_path):
    handler, config_path, sent = _make_lifecycle_handler(tmp_path)
    handler._update_lifecycle("demo", {"approved": "false"})
    assert sent["status"] == 400
    assert "boolean" in sent["body"]["error"]


def test_full_guided_lifecycle_end_to_end(tmp_path):
    from labos_agent.web import Handler
    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects: {}\n", encoding="utf-8")
    root = tmp_path / "guided"
    handler = object.__new__(Handler)
    handler.server = type("Server", (), {"config_path": config_path})()
    sent = {}
    handler._send = lambda status, body, content_type="application/json": sent.update(status=status, body=body)

    handler._create_project({
        "name": "guided",
        "repository": "example/guided",
        "project_root": str(root),
        "project_mode": "guided",
        "initial_idea": "Build an engineering workspace",
    })
    assert sent["status"] == 201
    assert load_config(config_path).projects["guided"].lifecycle_phase.name == "BRAINSTORM"

    handler._update_lifecycle("guided", {"brainstorm_notes": "Goals, users, constraints, alternatives"})
    handler._update_lifecycle("guided", {"phase": "DOCUMENTATION"})
    assert sent["status"] == 200

    docs = root / "docs"
    for name in ("PRODUCT.md", "REQUIREMENTS.md", "ARCHITECTURE.md", "DECISIONS.md", "ROADMAP.md", "USER_FLOWS.md"):
        (docs / name).write_text(f"# {name}\n\nApproved project content", encoding="utf-8")    (root / "AGENTS.md").write_text("# Guided\n\nProject rules", encoding="utf-8")
    handler._update_lifecycle("guided", {"phase": "PLANNING"})
    (root / "PLAN.md").write_text("# Plan\n\n## Acceptance Criteria\n- AC-1 core workflow works", encoding="utf-8")
    handler._update_lifecycle("guided", {"phase": "DEVELOPMENT"})
    handler._update_lifecycle("guided", {"approved": True})    assert load_config(config_path).projects["guided"].lifecycle_approved

    handler._update_lifecycle("guided", {"phase": "VALIDATION"})
    (root / "VALIDATION.md").write_text(
        "# Validation\n\n## Validation Results\nPASS\n\n## Acceptance Criteria\nAC-1 PASS",
        encoding="utf-8",
    )
    handler._update_lifecycle("guided", {"phase": "MAINTENANCE"})
    project = load_config(config_path).projects["guided"]
    assert project.lifecycle_phase.name == "MAINTENANCE"
    assert not project.lifecycle_approved


def test_existing_repository_persists_git_assessment(tmp_path):
    from labos_agent.web import Handler
    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects: {}\n", encoding="utf-8")
    root = tmp_path / "existing"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "README.md").write_text("existing", encoding="utf-8")
    handler = object.__new__(Handler)
    handler.server = type("Server", (), {"config_path": config_path})()
    sent = {}
    handler._send = lambda status, body, content_type="application/json": sent.update(status=status, body=body)
    handler._create_project({
        "name": "existing",
        "repository": "example/existing",
        "project_root": str(root),
        "project_mode": "existing_repository",
    })
    assessment = __import__("json").loads(
        (tmp_path / "state" / "existing" / "repository_assessment.json").read_text(encoding="utf-8")
    )
    assert assessment["git_repository"] is True
    assert assessment["head"] is None
    assert (root / "README.md").read_text(encoding="utf-8") == "existing"
    assert not (root / "docs").exists()


def test_project_name_security_rejects_path_traversal(tmp_path):
    handler, config_path, sent = _make_lifecycle_handler(tmp_path)
    handler._update_lifecycle("../escape", {"approved": True})
    assert sent["status"] == 404


def test_lifecycle_update_serialization_lock_exists():
    import labos_agent.web as web
    assert web._lifecycle_lock is not None


def test_ui_contains_lifecycle_gate_and_approval_controls():
    from pathlib import Path
    html = Path(__file__).resolve().parents[1] / "src" / "labos_agent" / "web" / "index.html"
    text = html.read_text(encoding="utf-8")
    assert "lifecycle_gate" in text
    assert "Approve development" in text
    assert "advanceLifecycle" in text
    assert "/lifecycle" in text


def test_concurrent_lifecycle_transition_allows_only_one_winner(tmp_path):
    import threading
    from labos_agent.web import Handler
    config_path = tmp_path / "config.yaml"
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "REQUIREMENTS.md").write_text("Requirements", encoding="utf-8")
    (repo / "PLAN.md").write_text("# Plan\n\n## Acceptance Criteria\n- AC-1 works", encoding="utf-8")
    config_path.write_text(
        f"""projects:
  demo:
    repository: example/demo
    project_root: {repo}
    continuation_message: Continue demo
    lifecycle:
      phase: PLANNING
""",
        encoding="utf-8",
    )
    results = []
    def worker():
        handler = object.__new__(Handler)
        handler.server = type("Server", (), {"config_path": config_path})()
        sent = {}
        handler._send = lambda status, body, content_type="application/json": sent.update(status=status, body=body)
        handler._update_lifecycle("demo", {"phase": "DEVELOPMENT"})
        results.append(sent["status"])
    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sorted(results) == [200, 400]
    assert load_config(config_path).projects["demo"].lifecycle_phase.name == "DEVELOPMENT"

def test_create_project_rejects_false_string_as_true_boolean(tmp_path):
    from labos_agent.web import Handler
    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects: {}\n", encoding="utf-8")
    root = tmp_path / "repo"
    handler = object.__new__(Handler)    handler.server = type("Server", (), {"config_path": config_path})()
    sent = {}
    handler._send = lambda status, body, content_type="application/json": sent.update(status=status, body=body)
    handler._create_project({
        "name": "demo",
        "repository": "example/demo",
        "project_root": str(root),
        "project_mode": "guided",
        "initial_idea": "Idea",
        "create_repository": "false",
    })
    assert sent["status"] == 201
    assert not (root / ".git").exists()


def test_create_project_rejects_malformed_boolean(tmp_path):
    from labos_agent.web import Handler
    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects: {}\n", encoding="utf-8")
    handler = object.__new__(Handler)
    handler.server = type("Server", (), {"config_path": config_path})()
    sent = {}
    handler._send = lambda status, body, content_type="application/json": sent.update(status=status, body=body)
    handler._create_project({
        "name": "demo",
        "repository": "example/demo",
        "project_root": str(tmp_path / "repo"),
        "project_mode": "guided",
        "initial_idea": "Idea",
        "create_repository": "yes",
    })
    assert sent["status"] == 400
    assert "boolean" in sent["body"]["error"]


def test_existing_repository_project_root_update_requires_git(tmp_path):
    from labos_agent.web import Handler
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""projects:
  demo:
    repository: example/demo
    project_root: {tmp_path / "repo"}
    continuation_message: Continue demo
    lifecycle:
      mode: existing_repository
      phase: DOCUMENTATION
""",
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    handler = object.__new__(Handler)
    handler.server = type("Server", (), {"config_path": config_path})()
    sent = {}
    handler._send = lambda status, body, content_type="application/json": sent.update(status=status, body=body)
    handler._update_project("demo", {"project_root": str(tmp_path / "not-git")})
    assert sent["status"] == 400


def test_new_project_bootstrap_matches_lifecycle_documentation_gate(tmp_path):
    from labos_agent.web import Handler
    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects: {}\n", encoding="utf-8")
    root = tmp_path / "repo"
    handler = object.__new__(Handler)
    handler.server = type("Server", (), {"config_path": config_path})()
    sent = {}
    handler._send = lambda status, body, content_type="application/json": sent.update(status=status, body=body)
    handler._create_project({
        "name": "demo",
        "repository": "example/demo",
        "project_root": str(root),
        "project_mode": "guided",
        "initial_idea": "Initial idea",
    })
    expected = {"IDEA.md", "PRODUCT.md", "REQUIREMENTS.md", "ARCHITECTURE.md", "DECISIONS.md", "ROADMAP.md", "USER_FLOWS.md"}
    assert {p.name for p in (root / "docs").iterdir()} == expected
    assert (root / "AGENTS.md").is_file()
    project = load_config(config_path).projects["demo"]
    assert project.lifecycle_phase.name == "BRAINSTORM"


def test_lifecycle_change_is_blocked_while_supervisor_running(tmp_path, monkeypatch):
    handler, config_path, sent = _make_lifecycle_handler(tmp_path)
    monkeypatch.setattr("labos_agent.web._process_status", lambda *args: {"running": True, "pid": 123})
    handler._update_lifecycle("demo", {"phase": "DOCUMENTATION"})
    assert sent["status"] == 409
    assert "stop the supervisor" in sent["body"]["error"]


def test_lifecycle_ui_browser_flow(tmp_path):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from playwright.sync_api import sync_playwright
    from labos_agent.lifecycle import LifecycleState, ProjectPhase, save_lifecycle_state
    from labos_agent.web import Handler

    config_path = tmp_path / "config.yaml"
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    for name in ("PRODUCT.md", "REQUIREMENTS.md", "ARCHITECTURE.md", "DECISIONS.md", "ROADMAP.md", "USER_FLOWS.md"):
        (root / "docs" / name).write_text("Approved content", encoding="utf-8")
    (root / "AGENTS.md").write_text("Rules", encoding="utf-8")
    (root / "PLAN.md").write_text("# Plan\n\n## Acceptance Criteria\n- AC-1 works", encoding="utf-8")
    config_path.write_text(
        f"""projects:
  demo:
    repository: example/demo
    project_root: {root}
    continuation_message: Continue demo
    lifecycle:
      mode: guided
      phase: DEVELOPMENT
""",
        encoding="utf-8",
    )
    save_lifecycle_state(tmp_path / "state", "demo", LifecycleState(phase=ProjectPhase.DEVELOPMENT))

    from labos_agent.web import EventHub
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.config_path = config_path
    server.event_hub = EventHub(config_path, interval=0.05)
    server.event_hub.start()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
            assert page.evaluate("typeof refresh") == "function", errors
            page.wait_for_function("document.querySelector('#projectList')?.innerText.includes('demo')", timeout=10000)
            page.locator("#projectList .project", has_text="demo").click()
            page.locator("#content").wait_for()
            assert not errors, errors
            page.get_by_role("button", name="Lifecycle", exact=True).click()
            page.locator(".phase", has_text="DEVELOPMENT").wait_for()
            page.get_by_role("button", name="Approve Development").click()
            page.get_by_text("Approved", exact=True).wait_for()
            assert "Approved" in page.locator("#content").inner_text()
            browser.close()
    finally:
        server.shutdown()
        server.event_hub.stop()
        thread.join(timeout=2)
        thread.join(timeout=2)


def test_full_lifecycle_browser_api_flow(tmp_path):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from playwright.sync_api import sync_playwright
    from labos_agent.web import Handler, EventHub

    config_path = tmp_path / "config.yaml"
    root = tmp_path / "repo"
    config_path.write_text(
        f"""projects:
  demo:
    repository: example/demo
    project_root: {root}
    continuation_message: Continue demo
    initial_idea: Build demo
    lifecycle:
      mode: guided
      phase: BRAINSTORM
""",
        encoding="utf-8",
    )
    root.mkdir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.config_path = config_path
    server.event_hub = EventHub(config_path, interval=0.02)
    server.event_hub.start()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
            page.wait_for_function("document.querySelector('#projectList')?.innerText.includes('demo')")
            page.locator("#projectList .project", has_text="demo").click()
            page.get_by_role("button", name="Lifecycle", exact=True).click()
            page.locator("#brainstormNotes").fill("Goals, users, constraints and alternatives")
            with page.expect_response(lambda response: response.url.endswith("/api/projects/demo/lifecycle") and response.request.method == "POST"):
                page.get_by_role("button", name="Save notes").click()
            docs = root / "docs"
            docs.mkdir(exist_ok=True)
            (docs / "IDEA.md").write_text("# Project Idea\n\n## LabOS brainstorming notes\n\nGoals, users, constraints and alternatives", encoding="utf-8")
            for name in ("PRODUCT.md", "REQUIREMENTS.md", "ARCHITECTURE.md", "DECISIONS.md", "ROADMAP.md", "USER_FLOWS.md"):
                (docs / name).write_text("Approved content", encoding="utf-8")
            (root / "AGENTS.md").write_text("Rules", encoding="utf-8")
            from labos_agent.lifecycle import phase_evidence, ProjectPhase
            assert phase_evidence(root, ProjectPhase.BRAINSTORM)[0]
            assert page.evaluate("""async () => (await fetch('/api/projects/demo/lifecycle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phase:'DOCUMENTATION'})})).status""") == 200
            page.evaluate("refresh()")
            page.wait_for_function("document.body.innerText.includes('DOCUMENTATION')", timeout=10000)
            page.evaluate("fetch('/api/projects/demo/lifecycle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phase:'PLANNING'})})")
            (root / "PLAN.md").write_text("# Plan\n\n## Acceptance Criteria\n- AC-1 works", encoding="utf-8")
            page.evaluate("fetch('/api/projects/demo/lifecycle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phase:'DEVELOPMENT'})})")
            page.evaluate("fetch('/api/projects/demo/lifecycle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({approved:true})})")
            page.evaluate("fetch('/api/projects/demo/lifecycle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phase:'VALIDATION'})})")
            (root / "VALIDATION.md").write_text("# Validation Results\n\n## Acceptance Criteria\nAC-1 PASS", encoding="utf-8")
            page.evaluate("fetch('/api/projects/demo/lifecycle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phase:'MAINTENANCE'})})")
            page.evaluate("refresh()")
            page.get_by_text("MAINTENANCE", exact=True).first.wait_for()
            page.get_by_role("button", name="Lifecycle", exact=True).click()
            page.get_by_text("phase_transition", exact=True).first.wait_for()
            browser.close()
    finally:
        server.shutdown()
        server.event_hub.stop()
        thread.join(timeout=2)
        thread.join(timeout=2)


def test_ui_has_accessible_shell_controls():
    from pathlib import Path
    html = Path(__file__).resolve().parents[1] / "src" / "labos_agent" / "web" / "index.html"
    text = html.read_text(encoding="utf-8")
    assert 'aria-label="Refresh project"' in text
    assert 'aria-label="Project views"' in text
    assert 'aria-label="Create new project"' in text
    assert "updateCreateMode()" in text


def test_workspace_helpers_expose_documentation_validation_and_assessment(tmp_path):
    from labos_agent.web import _documentation_inventory, _validation_summary, _repository_assessment
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "REQUIREMENTS.md").write_text("# Requirements\n", encoding="utf-8")
    (root / "docs" / "PLAN.md").write_text("# Plan\n\n## Acceptance Criteria\nAC-1", encoding="utf-8")
    (root / "docs" / "VALIDATION.md").write_text("# Validation Results\nAC-1 PASS", encoding="utf-8")
    assert any(x["name"] == "REQUIREMENTS.md" and x["exists"] for x in _documentation_inventory(str(root)))
    summary = _validation_summary(str(root))
    assert summary["passed"] == 1
    assert summary["total"] == 1
    config = tmp_path / "config.yaml"
    config.write_text("projects: {}\n", encoding="utf-8")
    assessment = _repository_assessment(config, "missing")
    assert assessment == {}


def test_web_json_rejects_oversized_body():
    from labos_agent.web import Handler
    handler = object.__new__(Handler)
    handler.headers = {"Content-Length": str(2 * 1024 * 1024), "Content-Type": "application/json"}
    handler.rfile = __import__("io").BytesIO(b"{}")
    try:
        handler._json()
    except ValueError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("oversized JSON body was accepted")


def test_ui_exposes_engineering_workspaces_and_toasts():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "src" / "labos_agent" / "web" / "index.html").read_text(encoding="utf-8")
    for value in ("lifecycle", "documentation", "planning", "validation", "repository", "runs", "ci"):
        assert value in html
    assert "toastStack" in html
    assert "aria-modal" in html


def test_web_server_is_local_only_by_default(monkeypatch, tmp_path):
    import os
    from labos_agent.web import _is_loopback_host, serve
    assert _is_loopback_host("127.0.0.1")
    assert _is_loopback_host("::1")
    assert not _is_loopback_host("0.0.0.0")
    monkeypatch.delenv("LABOS_ALLOW_REMOTE", raising=False)
    try:
        serve(tmp_path / "missing.yaml", "0.0.0.0", 0)
    except RuntimeError as exc:
        assert "local-only" in str(exc)
    else:
        raise AssertionError("remote binding was allowed without explicit opt-in")


def test_web_input_validation_boundaries():
    from labos_agent.web import _parse_bool, _is_loopback_host, _REPO_RE, _URL_RE
    assert _parse_bool(True, "x") is True
    assert _parse_bool("false", "x") is False
    with __import__("pytest").raises(ValueError):
        _parse_bool("yes", "x")
    assert _URL_RE.fullmatch("https://example.com")
    assert not _URL_RE.fullmatch("javascript:alert(1)")
    assert _is_loopback_host("localhost")
    assert not _is_loopback_host("0.0.0.0")
    assert _REPO_RE.fullmatch("owner/repo")
    assert not _REPO_RE.fullmatch("../repo")


def test_run_history_skips_corrupt_records(tmp_path):
    from labos_agent.web import _run_history
    root = tmp_path / "state" / "demo" / "runs"
    root.mkdir(parents=True)
    (root / "000001.json").write_text("{broken", encoding="utf-8")
    (root / "000002.json").write_text('{"run_number":2,"result":"DONE_VERIFIED"}', encoding="utf-8")
    records = _run_history(tmp_path / "config.yaml", "demo")
    assert [x["run_number"] for x in records] == [2]


def test_run_events_skip_corrupt_lines(tmp_path):
    from labos_agent.web import _run_events
    root = tmp_path / "state" / "demo" / "runs"
    root.mkdir(parents=True)
    (root / "000001.events.jsonl").write_text('{"event":"good"}\n{broken\n{"event":"also-good"}\n', encoding="utf-8")
    events = _run_events(tmp_path / "config.yaml", "demo", 1)
    assert events == [{"event": "good"}, {"event": "also-good"}]


def test_documentation_inventory_marks_placeholders(tmp_path):
    from labos_agent.web import _documentation_inventory
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "PRODUCT.md").write_text("_To be completed during documentation._", encoding="utf-8")
    rows = _documentation_inventory(str(tmp_path))
    product = next(row for row in rows if row["name"] == "PRODUCT.md")
    assert product["exists"] is True
    assert product["placeholder"] is True


def test_repository_assessment_missing_is_empty(tmp_path):
    from labos_agent.web import _repository_assessment
    assert _repository_assessment(tmp_path / "config.yaml", "demo") == {}


def test_lifecycle_transition_contract_is_sequential():
    from labos_agent.lifecycle import can_advance, ProjectPhase
    ok, missing = can_advance(Path("."), ProjectPhase.IDEA, ProjectPhase.DEVELOPMENT)
    assert not ok
    assert "invalid sequential lifecycle transition" in missing


def test_lifecycle_state_path_rejects_absolute_project_names(tmp_path):
    from labos_agent.lifecycle import lifecycle_state_path
    with __import__("pytest").raises(ValueError):
        lifecycle_state_path(tmp_path, "/tmp/escape")


def test_project_creation_modes_api(tmp_path, monkeypatch):
    from http.client import HTTPConnection
    from http.server import ThreadingHTTPServer
    import subprocess
    import yaml
    from labos_agent.web import Handler, EventHub

    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects: {}\n", encoding="utf-8")
    existing = tmp_path / "existing"
    existing.mkdir()
    subprocess.run(["git", "-C", str(existing), "init"], check=True, capture_output=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.config_path = config_path
    server.event_hub = EventHub(config_path, interval=0.01)
    server.event_hub.start()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port)
        body = '{"name":"spec","repository":"example/spec","project_root":"' + str(tmp_path / "spec") + '","project_mode":"specification","create_repository":false}'
        conn.request("POST", "/api/projects", body=body, headers={"Content-Type":"application/json"})
        response = conn.getresponse()
        assert response.status == 201
        data = __import__("json").loads(response.read())
        assert data["lifecycle_phase"] == "DOCUMENTATION"

        conn = HTTPConnection("127.0.0.1", server.server_port)
        body = '{"name":"existing","repository":"example/existing","project_root":"' + str(existing) + '","project_mode":"existing_repository","create_repository":false}'
        conn.request("POST", "/api/projects", body=body, headers={"Content-Type":"application/json"})
        response = conn.getresponse()
        assert response.status == 201
        data = __import__("json").loads(response.read())
        assert data["project_mode"] == "existing_repository"
        assert data["lifecycle_phase"] == "DOCUMENTATION"

        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert set(saved["projects"]) == {"spec", "existing"}
        assert saved["projects"]["existing"]["lifecycle"]["mode"] == "existing_repository"
    finally:
        server.shutdown()
        server.event_hub.stop()
        thread.join(timeout=2)
        thread.join(timeout=2)


def test_project_creation_rejects_existing_repository_without_git(tmp_path):
    from http.server import ThreadingHTTPServer
    from labos_agent.web import Handler, EventHub
    from http.client import HTTPConnection
    root = tmp_path / "not-git"
    root.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text("projects: {}\n", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.config_path = config_path
    server.event_hub = EventHub(config_path, interval=0.01)
    server.event_hub.start()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port)
        body = '{"name":"bad","repository":"example/bad","project_root":"' + str(root) + '","project_mode":"existing_repository","create_repository":false}'
        conn.request("POST", "/api/projects", body=body, headers={"Content-Type":"application/json"})
        response = conn.getresponse()
        assert response.status == 400
        assert "local Git repository" in response.read().decode()
    finally:
        server.shutdown()
        server.event_hub.stop()
        thread.join(timeout=2)
