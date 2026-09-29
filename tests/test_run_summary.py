from datetime import datetime, timezone

from labos_agent.run_summary import RunTracker


def _time(second: int) -> datetime:
    return datetime(2026, 9, 29, 10, 0, second, tzinfo=timezone.utc)


def test_tracker_records_iteration_lifecycle():
    tracker = RunTracker("weather", 42, _time(0))
    tracker.start_iteration(_time(0))
    tracker.record(
        state="WAIT_CI",
        chatgpt_work=True,
        repository_changed=True,
        local_tests_passed=True,
        commit_pushed=True,
        remote_ci_passed=True,
    )
    tracker.finish_iteration("WAIT_CI", _time(7))
    tracker.finish_run("DONE", _time(8))

    assert tracker.summary.result == "DONE"
    assert tracker.summary.iterations[0].elapsed_seconds() == 7
    assert tracker.one_line(now=_time(8)) == "iteration=1 state=WAIT_CI elapsed=7s checks=✓✓✓✓✓"


def test_tracker_renders_operator_box():
    tracker = RunTracker("weather", 42, _time(0))
    tracker.start_iteration(_time(0))
    tracker.record(
        state="CONTINUE",
        chatgpt_work=True,
        repository_changed=True,
        local_tests_passed=True,
        commit_pushed=True,
    )
    tracker.finish_iteration("CONTINUE", _time(5))
    tracker.finish_run("DONE", _time(6))

    output = tracker.box()

    assert "LabOS Run #42" in output
    assert "Project       weather" in output
    assert "Iteration 1" in output
    assert "✓ ChatGPT work" in output
    assert "✓ Repository changes" in output
    assert "✓ Local tests" in output
    assert "✓ Commit pushed" in output
    assert "· GitHub Actions" in output
    assert "Result        DONE" in output


def test_tracker_renders_ci_failure():
    tracker = RunTracker("weather", 7, _time(0))
    tracker.start_iteration(_time(0))
    tracker.record(state="WAIT_CI", remote_ci_failed=True, reason="test=failure")
    tracker.finish_iteration("WAIT_CI", _time(10), "test=failure")
    tracker.finish_run("FIX_CI", _time(11), "test=failure")

    output = tracker.box()

    assert "✗ GitHub Actions" in output
    assert "Result        FIX_CI" in output
    assert "Reason        test=failure" in output


def test_tracker_shows_active_state_before_run_finishes():
    tracker = RunTracker("weather", 8, _time(0))
    tracker.start_iteration(_time(0))
    tracker.record(state="WAIT_CI", chatgpt_work=True, remote_ci_passed=True)
    tracker.finish_iteration("WAIT_CI", _time(5))

    output = tracker.box()

    assert "Result        WAIT_CI" in output
