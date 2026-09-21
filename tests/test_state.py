from pathlib import Path

from labos_agent.state import AgentState, RunState, load_state, save_state


def test_state_round_trip(tmp_path: Path):
    path = tmp_path / "state.json"
    state = AgentState(project="weather", run_id="abc")
    state.transition(RunState.STARTING)

    save_state(path, state)
    restored = load_state(path)

    assert restored is not None
    assert restored.project == "weather"
    assert restored.run_id == "abc"
    assert restored.state == RunState.STARTING


def test_terminal_transition_records_reason():
    state = AgentState(project="weather", run_id="abc")
    state.transition(RunState.BLOCKED, reason="human decision required")

    assert state.state == RunState.BLOCKED
    assert state.reason == "human decision required"
    assert state.stopped_at is not None
