"""Local CI execution for registered project workspaces."""
from __future__ import annotations
from pathlib import Path
import subprocess
import time
from typing import Sequence

from .base import CICommandResult, CIResult

class LocalCI:
    """Execute explicitly configured commands on the Lab OS host.

    Commands are argv sequences, not shell strings. This deliberately avoids
    invoking a shell and keeps command selection in trusted project config.
    """
    def run(self, *, project: str, stage: str, project_root: Path, commands: Sequence[Sequence[str]], timeout_seconds: float = 1800) -> CIResult:
        root = project_root.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"project root does not exist or is not a directory: {root}")
        if not commands:
            raise ValueError(f"no CI commands configured for {project}:{stage}")
        results: list[CICommandResult] = []
        deadline = time.monotonic() + timeout_seconds
        for raw_command in commands:
            command = tuple(str(part) for part in raw_command)
            if not command or not command[0]:
                raise ValueError(f"invalid empty CI command for {project}:{stage}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"CI stage timed out before command: {' '.join(command)}")
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    command,
                    cwd=root,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=remaining,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                elapsed = time.monotonic() - started
                stdout = exc.stdout or ""
                stderr = exc.stderr or ""
                results.append(CICommandResult(command, -1, stdout, stderr, elapsed))
                raise TimeoutError(f"CI stage timed out: {' '.join(command)}") from exc
            result = CICommandResult(command, completed.returncode, completed.stdout, completed.stderr, time.monotonic() - started)
            results.append(result)
            if completed.returncode != 0:
                return CIResult(project, stage, False, tuple(results))
        return CIResult(project, stage, True, tuple(results))
