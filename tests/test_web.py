from pathlib import Path

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
    assert "invalid lifecycle transition" in sent["body"]["error"]
    assert load_config(config_path).projects["demo"].brainstorm_notes == ""


def test_lifecycle_requires_separate_development_transition_and_approval(tmp_path):
    handler, config_path, sent = _make_lifecycle_handler(tmp_path)
    handler._update_lifecycle("demo", {"phase": "DEVELOPMENT"})
    assert sent["status"] == 200
    assert load_config(config_path).projects["demo"].lifecycle_phase.name == "DEVELOPMENT"
    assert not load_config(config_path).projects["demo"].lifecycle_approved

    handler._update_lifecycle("demo", {"approved": True})
    assert sent["status"] == 200
    project = load_config(config_path).projects["demo"]
    assert project.lifecycle_approved
    assert project.lifecycle_approved_at


def test_existing_repository_mode_requires_local_git_repository(tmp_path):
    from labos_agent.web import Handler

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
