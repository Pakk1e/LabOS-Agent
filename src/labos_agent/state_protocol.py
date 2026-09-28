"""Small ChatGPT response-state protocol used by the supervisor loop."""
from __future__ import annotations

from enum import StrEnum
import re


class ChatState(StrEnum):
    CONTINUE = "CONTINUE"
    WAIT_CI = "WAIT_CI"
    FIX_CI = "FIX_CI"
    DONE = "DONE"


_STATE_RE = re.compile(r"^\(STATE (CONTINUE|WAIT_CI|FIX_CI|DONE) STATE\)$")


def parse_state(response: str) -> ChatState | None:
    """Parse only the final non-empty response line."""
    for line in reversed(response.splitlines()):
        line = line.strip()
        if not line:
            continue
        match = _STATE_RE.fullmatch(line)
        return ChatState(match.group(1)) if match else None
    return None


STATE_INSTRUCTION = """Always finish every response with a state marker on the final non-empty line.
The only valid markers are:
(STATE CONTINUE STATE)
(STATE WAIT_CI STATE)
(STATE FIX_CI STATE)
(STATE DONE STATE)
Do not omit the marker, even when you are only reporting progress."""
