"""Safety gates for autonomous runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SafetyLimits:
    deadline: datetime | None = None
    max_iterations: int = 50
    max_rollovers: int = 10
    max_consecutive_failures: int = 3
    max_consecutive_no_progress: int = 3


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason: str | None = None


def check_limits(
    *,
    iteration: int,
    rollover_count: int,
    consecutive_failures: int,
    consecutive_no_progress: int = 0,
    limits: SafetyLimits,
    now: datetime,
) -> SafetyDecision:
    if limits.deadline is not None and now >= limits.deadline:
        return SafetyDecision(False, "deadline reached")
    if iteration >= limits.max_iterations:
        return SafetyDecision(False, "maximum iterations reached")
    if rollover_count >= limits.max_rollovers:
        return SafetyDecision(False, "maximum rollovers reached")
    if consecutive_failures >= limits.max_consecutive_failures:
        return SafetyDecision(False, "maximum consecutive failures reached")
    if consecutive_no_progress >= limits.max_consecutive_no_progress:
        return SafetyDecision(False, "maximum consecutive no-progress iterations reached")
    return SafetyDecision(True)
