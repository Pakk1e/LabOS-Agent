"""Provider-independent CI contracts."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

@dataclass(frozen=True)
class CICommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float

@dataclass(frozen=True)
class CIResult:
    project: str
    stage: str
    success: bool
    commands: tuple[CICommandResult, ...]

class CIProvider(Protocol):
    def run(self, *, project: str, stage: str, project_root: Path, commands: Sequence[Sequence[str]], timeout_seconds: float) -> CIResult:
        """Run a configured CI stage in a project workspace."""
