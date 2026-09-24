from labos_agent.controller import Controller
from labos_agent.safety import SafetyLimits
from labos_agent.state import AgentState, RunState


def test_successful_one_shot_iteration_is_completed():
    state = AgentState(project="weather", run_id="run", state=RunState.WORKING, iteration=1)
    Controller(state=state, limits=SafetyLimits()).mark_success(continue_running=False)
    assert state.state == RunState.COMPLETED
    assert state.reason == "iteration committed and pushed successfully"
    assert state.last_progress_result == "progress confirmed"


def test_successful_autonomous_iteration_waits_for_next_iteration():
    state = AgentState(project="weather", run_id="run", state=RunState.WORKING, iteration=1)
    Controller(state=state, limits=SafetyLimits()).mark_success(continue_running=True)
    assert state.state == RunState.ITERATION_SUCCEEDED
    assert state.reason == "iteration committed and pushed successfully"
