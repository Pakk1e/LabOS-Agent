from datetime import datetime

from labos_agent.github_observer import GitHubObservation
from labos_agent.supervisor_memory import SupervisorMemory, load_memory, save_memory


def test_supervisor_memory_round_trip(tmp_path):
    path = tmp_path / "state" / "weather" / "supervisor_state.json"
    memory = SupervisorMemory(
        project="weather",
        last_analysis={"state": "WAIT_CI", "current_commit": "abc1234"},
        last_observed_commit="abc1234",
        last_observed_ci_run=166,
        last_observed_ci_status="completed",
        last_observed_ci_conclusion="success",
        updated_at=datetime.now().isoformat(),
    )
    save_memory(path, memory)

    loaded = load_memory(path, "weather")
    assert loaded.project == "weather"
    assert loaded.last_analysis["state"] == "WAIT_CI"
    assert loaded.last_observed_commit == "abc1234"
    assert loaded.last_observed_ci_run == 166


def test_github_observation_is_structured():
    observation = GitHubObservation(
        branch="main",
        commit_sha="abc1234",
        ci_run_id=166,
        ci_status="completed",
        ci_conclusion="success",
        ci_sha="abc1234",
        ci_name="Weather CI",
        ci_created_at="2026-09-29T08:00:00Z",
        ci_url="https://github.com/example",
    )
    assert observation.commit_sha == "abc1234"
    assert observation.ci_sha == observation.commit_sha


def test_supervisor_memory_recovers_from_backup(tmp_path):
    path = tmp_path / "state" / "weather" / "supervisor_state.json"
    save_memory(path, SupervisorMemory(project="weather", last_analysis={"state": "CONTINUE"}))
    save_memory(path, SupervisorMemory(project="weather", last_analysis={"state": "DONE"}))
    path.write_text("{broken", encoding="utf-8")
    loaded = load_memory(path, "weather")
    assert loaded.last_analysis["state"] == "CONTINUE"
