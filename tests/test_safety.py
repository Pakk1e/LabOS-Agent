from datetime import datetime, timedelta, timezone

from labos_agent.safety import SafetyLimits, check_limits


def test_allows_iteration_before_limits():
    now = datetime.now(timezone.utc)
    decision = check_limits(
        iteration=0,
        rollover_count=0,
        consecutive_failures=0,
        limits=SafetyLimits(deadline=now + timedelta(minutes=5)),
        now=now,
    )
    assert decision.allowed


def test_stops_at_deadline():
    now = datetime.now(timezone.utc)
    decision = check_limits(
        iteration=1,
        rollover_count=0,
        consecutive_failures=0,
        limits=SafetyLimits(deadline=now),
        now=now,
    )
    assert not decision.allowed
    assert decision.reason == "deadline reached"


def test_stops_after_failures():
    now = datetime.now(timezone.utc)
    decision = check_limits(
        iteration=1,
        rollover_count=0,
        consecutive_failures=3,
        limits=SafetyLimits(),
        now=now,
    )
    assert not decision.allowed
    assert decision.reason == "maximum consecutive failures reached"


def test_stops_after_no_progress():
    now = datetime.now(timezone.utc)
    decision = check_limits(
        iteration=2,
        rollover_count=0,
        consecutive_failures=0,
        consecutive_no_progress=3,
        limits=SafetyLimits(),
        now=now,
    )
    assert not decision.allowed
    assert decision.reason == "maximum consecutive no-progress iterations reached"


def test_iteration_limit_does_not_depend_on_no_progress():
    now = datetime.now(timezone.utc)
    decision = check_limits(
        iteration=50,
        rollover_count=0,
        consecutive_failures=0,
        consecutive_no_progress=0,
        limits=SafetyLimits(),
        now=now,
    )
    assert not decision.allowed
    assert decision.reason == "maximum iterations reached"
