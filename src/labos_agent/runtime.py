"""Explicit execution runtime boundary for LabOS actions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .execution import ExecutionPolicy, ExecutionResult, execute_request


@dataclass(frozen=True)
class RuntimeObservation:
    """Structured observation returned after an action is applied."""

    results: tuple[ExecutionResult, ...]
    requested_count: int

    @property
    def success(self) -> bool:
        return bool(self.results) and all(result.success for result in self.results)

    @property
    def successful_count(self) -> int:
        return sum(1 for result in self.results if result.success)

    @property
    def failed_count(self) -> int:
        return sum(1 for result in self.results if not result.success)


class ExecutionRuntime:
    """Validate and execute controller-approved agent actions.

    Keeping this boundary separate from the loop makes execution replaceable
    without changing the reasoning/controller state machine.
    """

    def __init__(self, project_root, policy: ExecutionPolicy):
        self.project_root = project_root
        self.policy = policy

    def apply(self, requests: Iterable[dict[str, Any]]) -> RuntimeObservation:
        results: list[ExecutionResult] = []
        requests = list(requests)
        for request in requests:
            try:
                results.append(execute_request(self.project_root, request, self.policy))
            except Exception as exc:
                results.append(
                    ExecutionResult(
                        action=str(request.get("action", "unknown")),
                        success=False,
                        exit_code=None,
                        stdout="",
                        stderr=f"request rejected: {type(exc).__name__}: {exc}",
                    )
                )
        return RuntimeObservation(tuple(results), len(requests))
