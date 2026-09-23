from pathlib import Path

import labos_agent.loop as loop
from labos_agent.state import AgentState, RunState, save_state


def test_prepare_state_preserves_ci_failure_for_recovery(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(loop, "state_path", lambda project: tmp_path / project / "current.json")
    path = tmp_path / "agent" / "current.json"
    state = AgentState(
        project="agent",
        run_id="old",
        state=RunState.ERROR,
        iteration=2,
        consecutive_failures=1,
        last_ci_result="stage=test success=False\nexit_code=7",
    )
    save_state(path, state)

    recovered = loop._prepare_state("agent")

    assert recovered.state == RunState.IDLE
    assert recovered.run_id != "old"
    assert recovered.last_ci_result == "stage=test success=False\nexit_code=7"
    assert recovered.consecutive_failures == 1
    assert recovered.iteration == 2
    assert recovered.stopped_at is None
    assert recovered.reason is None


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
