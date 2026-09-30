"""Persistent memory for the experimental ChatGPT supervisor."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

@dataclass
class SupervisorMemory:
    project: str
    conversation_url: str | None = None
    last_response: str | None = None
    last_analysis: dict[str, Any] = field(default_factory=dict)
    last_observed_commit: str | None = None
    last_observed_branch: str | None = None
    last_observed_ci_run: int | None = None
    last_observed_ci_status: str | None = None
    last_observed_ci_conclusion: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def load_memory(path: Path, project: str) -> SupervisorMemory:
    if not path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        if not backup.exists():
            return SupervisorMemory(project=project)
        path = backup
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        backup = path.with_suffix(path.suffix + ".bak")
        if path != backup and backup.exists():
            data = json.loads(backup.read_text(encoding="utf-8"))
        else:
            raise ValueError(f"invalid supervisor memory for project {project}") from exc
    if not isinstance(data, dict):
        raise ValueError("supervisor memory must be a JSON object")
    data.setdefault("project", project)
    memory = SupervisorMemory(**data)
    if memory.project != project:
        raise ValueError("supervisor memory project mismatch")
    return memory

def save_memory(path: Path, memory: SupervisorMemory) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(memory.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    backup = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        path.replace(backup)
    tmp.replace(path)

def memory_path(state_root: Path, project: str) -> Path:
    return state_root / project / "supervisor_state.json"
