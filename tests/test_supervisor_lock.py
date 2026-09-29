import fcntl
from pathlib import Path

import pytest

from labos_agent.supervisor import SupervisorError, _project_execution_lock


def test_project_lock_allows_different_projects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _project_execution_lock("weather"):
        with _project_execution_lock("worlds"):
            pass


def test_project_lock_rejects_second_owner(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _project_execution_lock("weather"):
        with pytest.raises(SupervisorError, match="supervisor already running for project: weather"):
            with _project_execution_lock("weather"):
                pass


def test_project_lock_is_released_after_owner_exits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _project_execution_lock("weather"):
        pass
    with _project_execution_lock("weather"):
        pass


def test_project_lock_uses_per_project_state_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with _project_execution_lock("weather"):
        assert (Path("state") / "weather" / "supervisor.lock").exists()
        assert not (Path("state") / "worlds" / "supervisor.lock").exists()
