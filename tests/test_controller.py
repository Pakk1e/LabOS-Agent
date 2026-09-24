from labos_agent.controller import Controller
from labos_agent.safety import SafetyLimits
from labos_agent.state import AgentState,IterationStage,RunState


def verified_state():
    state = AgentState(project="weather", run_id="run", state=RunState.WORKING, iteration=1)
    state.files_changed = True
    state.meaningful_progress = True
    state.local_ci_ran = True
    state.local_ci_passed = True
    state.commit_created = True
    state.push_verified = True
    state.github_ci_verified = True
    state.iteration_stage = IterationStage.ITERATION_SUCCEEDED
    return state


def test_successful_one_shot_iteration_is_completed():
    state = verified_state()
    Controller(state=state, limits=SafetyLimits()).mark_success(continue_running=False)
    assert state.state == RunState.COMPLETED
    assert state.reason == "iteration committed, pushed, and exact-SHA CI verified"
    assert state.last_progress_result == "progress confirmed"


def test_successful_autonomous_iteration_reports_success_not_waiting():
    state = verified_state()
    Controller(state=state, limits=SafetyLimits()).mark_success(continue_running=True)
    assert state.state == RunState.ITERATION_SUCCEEDED
    assert state.iteration_stage == IterationStage.ITERATION_SUCCEEDED


def test_success_requires_all_evidence_gates():
    state = AgentState(project="weather", run_id="run", state=RunState.WORKING, iteration=1)
    try:
        Controller(state=state, limits=SafetyLimits()).mark_success(continue_running=False)
    except RuntimeError as exc:
        assert "all verification gates" in str(exc)
    else:
        raise AssertionError("incomplete evidence was accepted")
