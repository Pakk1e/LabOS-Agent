"""Small ChatGPT response-state protocol used by the supervisor loop."""
from __future__ import annotations

from enum import StrEnum
import re


class ChatState(StrEnum):
    CONTINUE = "CONTINUE"
    WAIT_CI = "WAIT_CI"
    FIX_CI = "FIX_CI"
    DONE = "DONE"


_STATE_RE = re.compile(r"\(STATE (CONTINUE|WAIT_CI|FIX_CI|DONE) STATE\)")


def parse_state(response: str) -> ChatState | None:
    """Parse the last valid state marker from an extracted assistant response.

    ChatGPT's rendered DOM can append browser/UI artifacts after the visible
    final line, so requiring the marker to be the literal final extracted line
    is too brittle. Only exact valid markers are accepted, and the last one wins.
    """
    matches = list(_STATE_RE.finditer(response))
    if not matches:
        return None
    return ChatState(matches[-1].group(1))


STATE_INSTRUCTION = """Always finish every response with a state marker on the final non-empty line.
The only valid markers are:
(STATE CONTINUE STATE)
(STATE WAIT_CI STATE)
(STATE FIX_CI STATE)
(STATE DONE STATE)
Do not omit the marker, even when you are only reporting progress."""
