"""Browser transport/session protocol boundary.

The adapter owns ChatGPT semantics; the transport owns connection lifecycle.
This small interface makes reconnect/retry policy explicit without coupling
the autonomous controller to Playwright internals.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class TransportAttempt:
    request_id: str
    attempt: int


class BrowserTransport:
    """Retry a browser operation only when the caller classifies it as transient."""

    def __init__(self, *, retry_limit: int = 2):
        if retry_limit < 0:
            raise ValueError("retry_limit must be non-negative")
        self.retry_limit = retry_limit

    def request(
        self,
        request_id: str,
        operation: Callable[[], T],
        *,
        is_transient: Callable[[Exception], bool],
    ) -> T:
        last_error: Exception | None = None
        for attempt in range(1, self.retry_limit + 2):
            try:
                return operation()
            except Exception as exc:
                last_error = exc
                if attempt > self.retry_limit or not is_transient(exc):
                    raise
        assert last_error is not None
        raise last_error
