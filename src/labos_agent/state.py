"""Persistent controller state and explicit state transitions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path


class RunState(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    WAITING = "WAITING"
    WORKING = "WORKING"
    ROLLOVER = "ROLLOVER"
    BLOCKED = "BLOCKED"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


@dataclass
class AgentState:
    project: str
    run_id: str
    state: RunState = RunState.IDLE
    iteration: int = 0
    rollover_count: int = 0
    consecutive_failures: int = 0
    started_at: str | None = None
    stopped_at: str | None = None
    last_action: str | None = None
    reason: str | None = None

    def transition(self, state: RunState, *, reason: str | None = None) -> None:
        self.state = state
        self.reason = reason
        self.last_action = state.value
        if state in {RunState.STOPPED, RunState.COMPLETED, RunState.ERROR, RunState.BLOCKED}:
            self.stopped_at = utc_now()
        if self.started_at is None and state != RunState.IDLE:
            self.started_at = utc_now()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["state"] = self.state.value
        return data


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state(path: Path) -> AgentState | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    data["state"] = RunState(data["state"])
    return AgentState(**data)


def save_state(path: Path, state: AgentState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
