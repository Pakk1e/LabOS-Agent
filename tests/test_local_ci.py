from pathlib import Path

import pytest

from labos_agent.ci.local import LocalCI


def test_local_ci_runs_in_project_root(tmp_path: Path):
    result = LocalCI().run(
        project="demo",
        stage="test",
        project_root=tmp_path,
        commands=[["python3", "-c", "from pathlib import Path; print(Path.cwd())"]],
        timeout_seconds=5,
    )
    assert result.success
    assert result.commands[0].returncode == 0
    assert str(tmp_path) in result.commands[0].stdout


def test_local_ci_stops_after_failed_command(tmp_path: Path):
    result = LocalCI().run(
        project="demo",
        stage="test",
        project_root=tmp_path,
        commands=[
            ["python3", "-c", "raise SystemExit(7)"],
            ["python3", "-c", "raise SystemExit(9)"],
        ],
        timeout_seconds=5,
    )
    assert not result.success
    assert len(result.commands) == 1
    assert result.commands[0].returncode == 7


def test_local_ci_rejects_missing_project_root(tmp_path: Path):
    with pytest.raises(ValueError, match="project root"):
        LocalCI().run(
            project="demo",
            stage="test",
            project_root=tmp_path / "missing",
            commands=[["python3", "-c", "pass"]],
        )


def test_local_ci_rejects_empty_commands(tmp_path: Path):
    with pytest.raises(ValueError, match="no CI commands"):
        LocalCI().run(
            project="demo",
            stage="test",
            project_root=tmp_path,
            commands=[],
        )


def test_local_ci_enforces_timeout(tmp_path: Path):
    with pytest.raises(TimeoutError, match="timed out"):
        LocalCI().run(
            project="demo",
            stage="test",
            project_root=tmp_path,
            commands=[["python3", "-c", "import time; time.sleep(2)" ]],
            timeout_seconds=0.1,
        )
