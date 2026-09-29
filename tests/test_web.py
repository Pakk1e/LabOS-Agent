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
