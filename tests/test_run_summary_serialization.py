from datetime import datetime, timezone

from labos_agent.run_summary import RunTracker


def test_run_tracker_serializes_operator_history():
    started = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    tracker = RunTracker("weather", 7, started)
    tracker.start_iteration(started)
    tracker.record(state="WAIT_CI", chatgpt_work=True, remote_ci_passed=True)
    tracker.finish_iteration("WAIT_CI", started)
    tracker.finish_run("DONE_VERIFIED", started)
    data = tracker.to_dict()
    assert data["project"] == "weather"
    assert data["run_number"] == 7
    assert data["result"] == "DONE_VERIFIED"
    assert data["iterations"][0]["state"] == "WAIT_CI"
    assert data["iterations"][0]["remote_ci_passed"] is True
