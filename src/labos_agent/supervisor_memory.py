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
        return SupervisorMemory(project=project)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("project", project)
    return SupervisorMemory(**data)

def save_memory(path: Path, memory: SupervisorMemory) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(memory.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)

def memory_path(state_root: Path, project: str) -> Path:
    return state_root / project / "supervisor_state.json"
