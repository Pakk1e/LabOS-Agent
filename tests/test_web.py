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
    assert calls[0][0][0:4] == (["gh", "run", "rerun", "123"],)
