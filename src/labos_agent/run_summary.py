"""Operator-facing lifecycle summaries for LabOS runs.

The tracker keeps lifecycle state separate from the low-level trace logger.
It is intentionally deterministic so summaries can be tested without a
browser, GitHub, or network connection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable


@dataclass
class IterationSummary:
    number: int
    started_at: datetime
    state: str = "STARTING"
    chatgpt_work: bool = False
    repository_changed: bool = False
    local_tests_passed: bool = False
    commit_pushed: bool = False
    remote_ci_passed: bool = False
    remote_ci_failed: bool = False
    reason: str | None = None
    finished_at: datetime | None = None

    def elapsed_seconds(self, now: datetime | None = None) -> float:
        end = self.finished_at or now
        if end is None:
            return 0.0
        return max(0.0, (end - self.started_at).total_seconds())


@dataclass
class RunSummary:
    project: str
    run_number: int
    started_at: datetime
    iterations: list[IterationSummary] = field(default_factory=list)
    result: str = "STARTING"
    finished_at: datetime | None = None
    reason: str | None = None

    @property
    def current_iteration(self) -> IterationSummary | None:
        return self.iterations[-1] if self.iterations else None

    def elapsed_seconds(self, now: datetime | None = None) -> float:
        end = self.finished_at or now
        if end is None:
            return 0.0
        return max(0.0, (end - self.started_at).total_seconds())


class RunTracker:
    """Track a LabOS run without owning logging or I/O."""

    def __init__(self, project: str, run_number: int, started_at: datetime):
        self.summary = RunSummary(project, run_number, started_at)

    def start_iteration(self, started_at: datetime) -> IterationSummary:
        iteration = IterationSummary(len(self.summary.iterations) + 1, started_at)
        self.summary.iterations.append(iteration)
        return iteration

    @property
    def iteration(self) -> IterationSummary:
        current = self.summary.current_iteration
        if current is None:
            raise RuntimeError("no active iteration")
        return current

    def record(
        self,
        *,
        state: str | None = None,
        chatgpt_work: bool | None = None,
        repository_changed: bool | None = None,
        local_tests_passed: bool | None = None,
        commit_pushed: bool | None = None,
        remote_ci_passed: bool | None = None,
        remote_ci_failed: bool | None = None,
        reason: str | None = None,
    ) -> None:
        iteration = self.iteration
        if state is not None:
            iteration.state = state
        for name, value in (
            ("chatgpt_work", chatgpt_work),
            ("repository_changed", repository_changed),
            ("local_tests_passed", local_tests_passed),
            ("commit_pushed", commit_pushed),
            ("remote_ci_passed", remote_ci_passed),
            ("remote_ci_failed", remote_ci_failed),
        ):
            if value is not None:
                setattr(iteration, name, value)
        if reason is not None:
            iteration.reason = reason

    def finish_iteration(self, state: str, finished_at: datetime, reason: str | None = None) -> None:
        iteration = self.iteration
        iteration.state = state
        iteration.finished_at = finished_at
        if reason is not None:
            iteration.reason = reason

    def finish_run(self, result: str, finished_at: datetime, reason: str | None = None) -> None:
        self.summary.result = result
        self.summary.finished_at = finished_at
        self.summary.reason = reason

    def one_line(self, *, now: datetime | None = None) -> str:
        iteration = self.iteration
        return (
            f"iteration={iteration.number} state={iteration.state} "
            f"elapsed={_duration(iteration.elapsed_seconds(now))} "
            f"checks={_checks(iteration)}"
        )

    def box(self, *, now: datetime | None = None) -> str:
        summary = self.summary
        lines = [
            f"┌─ LabOS Run #{summary.run_number} {'─' * 42}",
            f"│ Project       {summary.project}",
            f"│ Started       {summary.started_at.astimezone().strftime('%H:%M:%S')}",
            f"│ Duration      {_duration(summary.elapsed_seconds(now))}",
            f"│ Iterations    {len(summary.iterations)}",
        ]
        if summary.current_iteration is not None:
            iteration = summary.current_iteration
            lines.extend([
                "│",
                f"│ Iteration {iteration.number}",
                f"│ {_check(iteration.chatgpt_work)} ChatGPT work",
                f"│ {_check(iteration.repository_changed)} Repository changes",
                f"│ {_check(iteration.local_tests_passed)} Local tests",
                f"│ {_check(iteration.commit_pushed)} Commit pushed",
                f"│ {_remote_check(iteration)} GitHub Actions",
            ])
        displayed_result = summary.result
        if displayed_result == "STARTING" and summary.current_iteration is not None:
            displayed_result = summary.current_iteration.state
        lines.extend([
            "│",
            f"│ Result        {displayed_result}",
        ])
        if summary.reason:
            lines.append(f"│ Reason        {summary.reason}")
        lines.append("└" + "─" * 56)
        return "\n".join(lines)


def _check(value: bool) -> str:
    return "✓" if value else "·"


def _remote_check(iteration: IterationSummary) -> str:
    if iteration.remote_ci_failed:
        return "✗"
    if iteration.remote_ci_passed:
        return "✓"
    return "·"


def _checks(iteration: IterationSummary) -> str:
    return "".join(
        (
            "✓" if iteration.chatgpt_work else "·",
            "✓" if iteration.repository_changed else "·",
            "✓" if iteration.local_tests_passed else "·",
            "✓" if iteration.commit_pushed else "·",
            "✓" if iteration.remote_ci_passed else ("✗" if iteration.remote_ci_failed else "·"),
        )
    )


def _duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"
