from pathlib import Path

import yaml

from labos_agent.config import load_config
from labos_agent.web import Handler


def _write_config(tmp_path: Path, project: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"projects": {"demo": project}}, sort_keys=False),
        encoding="utf-8",
    )
    return path


def test_project_supervisor_settings_load_with_defaults(tmp_path):
    config = load_config(_write_config(tmp_path, {
        "repository": "Pakk1e/demo",
        "project_root": str(tmp_path / "repo"),
        "continuation_message": "Continue",
    }))
    project = config.projects["demo"]
    assert project.max_turns == 0
    assert project.response_timeout_seconds == 1200
    assert project.quiet_seconds == 3
    assert project.min_fresh_chat_delay_seconds == 420
    assert project.response_to_next_message_delay_seconds == 45
    assert project.max_no_progress_iterations == 5
    assert project.ci_timeout_seconds == 1800
    assert project.remote_ci_poll_seconds == 5


def test_apply_optional_accepts_supervisor_timing_and_limits():
    project = {"lifecycle": {}}
    Handler._apply_optional(project, {
        "max_turns": "25",
        "response_timeout_seconds": "900",
        "quiet_seconds": "2.5",
        "min_fresh_chat_delay_seconds": "120",
        "response_to_next_message_delay_seconds": "15",
        "max_no_progress_iterations": "7",
        "ci_timeout_seconds": "2400",
        "ci_poll_seconds": "10",
    })
    assert project["max_turns"] == 25
    assert project["response_timeout_seconds"] == 900.0
    assert project["quiet_seconds"] == 2.5
    assert project["min_fresh_chat_delay_seconds"] == 120.0
    assert project["response_to_next_message_delay_seconds"] == 15.0
    assert project["max_no_progress_iterations"] == 7
    assert project["ci_timeout_seconds"] == 2400.0
    assert project["ci_poll_seconds"] == 10.0


def test_apply_optional_rejects_unsafe_values():
    project = {"lifecycle": {}}
    for key, value in (
        ("max_turns", 1001),
        ("response_timeout_seconds", 29),
        ("quiet_seconds", 31),
        ("min_fresh_chat_delay_seconds", -1),
        ("response_to_next_message_delay_seconds", 601),
        ("max_no_progress_iterations", 0),
        ("ci_timeout_seconds", 59),
        ("ci_poll_seconds", 0),
    ):
        try:
            Handler._apply_optional(project, {key: value})
        except ValueError:
            continue
        raise AssertionError(f"{key} accepted invalid value {value}")
