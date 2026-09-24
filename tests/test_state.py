from pathlib import Path
from labos_agent.state import AgentState,RunState,load_state,save_state

def test_state_round_trip(tmp_path:Path):
    path=tmp_path/"state.json"
    state=AgentState(project="weather",run_id="abc"); state.transition(RunState.STARTING)
    save_state(path,state); restored=load_state(path)
    assert restored is not None and restored.project=="weather" and restored.state==RunState.STARTING

def test_terminal_transition_records_reason():
    state=AgentState(project="weather",run_id="abc")
    state.transition(RunState.BLOCKED,reason="human decision required")
    assert state.state==RunState.BLOCKED and state.reason=="human decision required" and state.stopped_at is not None

def test_rollover_position_round_trip(tmp_path:Path):
    state=AgentState(project="weather",run_id="x",iteration=20,rollover_count=1,iteration_at_last_rollover=20)
    state.transition(RunState.ITERATION_SUCCEEDED)
    path=tmp_path/"state.json"; save_state(path,state); restored=load_state(path)
    assert restored is not None and restored.iteration_at_last_rollover==20

def test_old_state_defaults_rollover_position(tmp_path:Path):
    path=tmp_path/"state.json"
    path.write_text('{"project":"weather","run_id":"x","state":"WAITING","iteration":3,"rollover_count":1,"consecutive_failures":0}',encoding="utf-8")
    state=load_state(path)
    assert state is not None and state.iteration_at_last_rollover==0

def test_failure_history_round_trip(tmp_path:Path):
    path=tmp_path/"state.json"
    state=AgentState(project="weather",run_id="x",iteration=4)
    state.record_failure("browser timeout")
    save_state(path,state); restored=load_state(path)
    assert restored is not None and restored.failure_history[0]["iteration"]==4
    assert restored.failure_history[0]["reason"]=="browser timeout"

def test_old_state_defaults_failure_history(tmp_path:Path):
    path=tmp_path/"state.json"
    path.write_text('{"project":"weather","run_id":"x","state":"WAITING","iteration":3,"rollover_count":1,"consecutive_failures":0}',encoding="utf-8")
    state=load_state(path)
    assert state is not None and state.failure_history==[]

def test_failure_history_is_bounded():
    state=AgentState(project="weather",run_id="x")
    for i in range(25):
        state.iteration=i
        state.record_failure(f"failure {i}")
    assert len(state.failure_history)==20
    assert state.failure_history[0]["reason"]=="failure 5"
    assert state.failure_history[-1]["reason"]=="failure 24"


def test_state_loads_without_local_ci_result(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"project":"demo","run_id":"1","state":"IDLE"}\n', encoding="utf-8")
    state = load_state(path)
    assert state is not None
    assert state.last_ci_result is None


def test_pending_recovery_fingerprint_round_trip(tmp_path:Path):
    state=AgentState(
        project="weather",
        run_id="x",
        pending_ci_fix=True,
        pending_ci_worktree_fingerprint="abc123",
    )
    path=tmp_path/"state.json"
    save_state(path,state)
    restored=load_state(path)
    assert restored is not None
    assert restored.pending_ci_worktree_fingerprint=="abc123"
