from pathlib import Path

import labos_agent.loop as loop
from labos_agent.state import AgentState, RunState, save_state


def test_prepare_state_resets_execution_counters_but_preserves_failure_history(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(loop, "state_path", lambda project: tmp_path / project / "current.json")
    path = tmp_path / "weather" / "current.json"
    state = AgentState(
        project="weather",
        run_id="old",
        state=RunState.STOPPED,
        iteration=23,
        rollover_count=1,
        consecutive_failures=3,
        iteration_at_last_rollover=20,
        failure_history=[
            {
                "timestamp": "2026-09-23T20:02:29+00:00",
                "iteration": 23,
                "reason": "old fetch failure",
            }
        ],
        last_ci_result="stage=test success=False",
    )
    save_state(path, state)

    fresh = loop._prepare_state("weather")

    assert fresh.state == RunState.IDLE
    assert fresh.run_id != "old"
    assert fresh.iteration == 0
    assert fresh.rollover_count == 0
    assert fresh.consecutive_failures == 0
    assert fresh.iteration_at_last_rollover == 0
    assert fresh.failure_history == state.failure_history
    assert fresh.last_ci_result is None
    assert fresh.started_at is None
    assert fresh.stopped_at is None
    assert fresh.last_action is None
    assert fresh.reason is None


def test_prepare_state_preserves_active_run_state(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(loop, "state_path", lambda project: tmp_path / project / "current.json")
    path = tmp_path / "weather" / "current.json"
    state = AgentState(
        project="weather",
        run_id="active",
        state=RunState.WORKING,
        iteration=4,
        rollover_count=1,
        consecutive_failures=1,
        iteration_at_last_rollover=3,
        last_ci_result="stage=test success=False",
    )
    save_state(path, state)

    prepared = loop._prepare_state("weather")

    assert prepared.run_id == "active"
    assert prepared.state == RunState.WORKING
    assert prepared.iteration == 4
    assert prepared.rollover_count == 1
    assert prepared.consecutive_failures == 1
    assert prepared.iteration_at_last_rollover == 3
    assert prepared.last_ci_result == "stage=test success=False"

def test_run_loop_selects_project_page_and_stops_at_iteration_limit(monkeypatch,tmp_path):
    project=ProjectConfig(name="test",repository="x",project_root=tmp_path,continuation_message="continue",project_name="Test",execution_enabled=False)
    config=AppConfig(browser=BrowserConfig(profile_dir=tmp_path),projects={"test":project})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("labos_agent.loop.BrowserSession",FakeSession)
    monkeypatch.setattr("labos_agent.loop.ChatGPTPage",FakeChat)
    monkeypatch.setattr("labos_agent.loop.prepare_repository",lambda *args,**kwargs: None)
    monkeypatch.setattr("labos_agent.loop.inspect_project",lambda *args,**kwargs: "snapshot")
    monkeypatch.setattr("labos_agent.loop.git_snapshot",lambda *args,**kwargs: type("S",(),{"head":"h","upstream":"u","status":""})())
    monkeypatch.setattr("labos_agent.loop.build_continuation_prompt",lambda *args,**kwargs: "prompt")
    result=run_loop(config,"test",deadline=None,max_iterations=1,max_rollovers=1)
    assert result.state.iteration == 1
    assert result.state.reason == "maximum iterations reached"
