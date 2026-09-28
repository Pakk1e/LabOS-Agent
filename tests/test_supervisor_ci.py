from datetime import datetime, timezone

from labos_agent.supervisor import CIRun, _runs_relevant_to_wait, wait_for_ci


def test_relevant_runs_allow_small_timestamp_race():
    started = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)
    runs = [
        CIRun(1, "abc", "completed", "success", "2026-09-28T17:57:00Z", "", "old"),
        CIRun(2, "def", "completed", "success", "2026-09-28T17:59:30Z", "", "recent"),
    ]
    assert [run.id for run in _runs_relevant_to_wait(runs, started)] == [2]


def test_wait_for_ci_passes_completed_run():
    started = datetime.now(timezone.utc)
    runs = [
        CIRun(1, "abc", "completed", "success", "2026-09-28T20:00:00Z", "", "CI"),
    ]
    result = wait_for_ci(
        "owner/repo",
        started,
        timeout_seconds=1,
        poll_seconds=0,
        request_fn=lambda repo: runs,
        sleep_fn=lambda seconds: None,
    )
    assert result[0] is True


def test_wait_for_ci_reports_failure():
    started = datetime.now(timezone.utc)
    runs = [
        CIRun(1, "abc", "completed", "failure", "2026-09-28T20:00:00Z", "", "CI"),
    ]
    result = wait_for_ci(
        "owner/repo",
        started,
        timeout_seconds=1,
        poll_seconds=0,
        request_fn=lambda repo: runs,
        sleep_fn=lambda seconds: None,
    )
    assert result[0] is False
    assert "failed" in result[1]


def test_wait_for_ci_waits_for_running_run():
    started = datetime.now(timezone.utc)
    responses = iter([
        [CIRun(1, "abc", "in_progress", None, "2026-09-28T20:00:00Z", "", "CI")],
        [CIRun(1, "abc", "completed", "success", "2026-09-28T20:00:00Z", "", "CI")],
    ])
    result = wait_for_ci(
        "owner/repo",
        started,
        timeout_seconds=1,
        poll_seconds=0,
        request_fn=lambda repo: next(responses),
        sleep_fn=lambda seconds: None,
    )
    assert result[0] is True


def test_wait_for_ci_ignores_baseline_run():
    started = datetime.now(timezone.utc)
    runs = [
        CIRun(1, "abc", "completed", "success", "2026-09-28T20:00:00Z", "", "old"),
    ]
    result = wait_for_ci(
        "owner/repo", started, baseline_run_ids={1}, timeout_seconds=0,
        poll_seconds=0, request_fn=lambda repo: runs, sleep_fn=lambda seconds: None,
    )
    assert result[0] is False
    assert "waiting" in result[1]
