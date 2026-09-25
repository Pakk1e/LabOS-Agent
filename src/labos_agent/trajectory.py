"""Append-only structured trajectory events for autonomous runs.

The trajectory is deliberately separate from the compact resumable state file.
It is diagnostic/audit history: events are append-only JSONL and each record
contains enough identity to correlate one iteration across reasoning, action,
execution, observation, and verification.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TrajectoryEvent:
    run_id: str
    project: str
    iteration: int
    phase: str
    event: str
    timestamp: str
    data: dict[str, Any]


def append_event(
    path: Path,
    *,
    run_id: str,
    project: str,
    iteration: int,
    phase: str,
    event: str,
    **data: Any,
) -> None:
    """Append one durable event and flush it before returning."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record = TrajectoryEvent(
        run_id=run_id,
        project=project,
        iteration=iteration,
        phase=phase,
        event=event,
        timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
        data=data,
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), ensure_ascii=False, default=str) + "\n")
        handle.flush()


def trajectory_path(state_directory: Path) -> Path:
    return state_directory / "trajectory.jsonl"
