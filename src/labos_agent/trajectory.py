"""Durable, append-only trajectory for autonomous agent runs."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
from threading import Lock
from typing import Any
import uuid


@dataclass(frozen=True)
class TrajectoryEvent:
    sequence: int
    event_id: str
    run_id: str
    project: str
    iteration: int
    phase: str
    event: str
    timestamp: str
    data: dict[str, Any]


class Trajectory:
    """Append-only event journal with process-local serialization.

    The journal is intentionally independent of resumable RunState. State answers
    "where are we?"; trajectory answers "how did we get here?".
    """

    def __init__(self, path: Path, *, run_id: str, project: str):
        self.path = path
        self.run_id = run_id
        self.project = project
        self._lock = Lock()
        self._sequence = self._load_last_sequence()

    def _load_last_sequence(self) -> int:
        try:
            with self.path.open("rb") as handle:
                last = b""
                for chunk in iter(lambda: handle.read(65536), b""):
                    if chunk:
                        last = chunk
                if not last:
                    return 0
                record = json.loads(last.splitlines()[-1])
                return int(record.get("sequence", 0))
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError, IndexError):
            return 0

    def append(self, *, iteration: int, phase: str, event: str, **data: Any) -> TrajectoryEvent:
        with self._lock:
            self._sequence += 1
            record = TrajectoryEvent(
                sequence=self._sequence,
                event_id=uuid.uuid4().hex,
                run_id=self.run_id,
                project=self.project,
                iteration=iteration,
                phase=phase,
                event=event,
                timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
                data=data,
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(record), ensure_ascii=False, default=str) + "\n")
                handle.flush()
            return record


def trajectory_path(state_directory: Path) -> Path:
    return state_directory / "trajectory.jsonl"


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
    """Compatibility helper for callers that do not retain a Trajectory object."""
    Trajectory(path, run_id=run_id, project=project).append(
        iteration=iteration, phase=phase, event=event, **data
    )
