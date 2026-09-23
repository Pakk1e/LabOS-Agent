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
