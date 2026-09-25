"""First-class repository verification pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .git_gate import GitSnapshot, snapshot
from .trace import trace


@dataclass(frozen=True)
class VerificationResult:
    clean_relative_to_baseline: bool
    meaningful_change: bool
    before: GitSnapshot
    after: GitSnapshot


class RepositoryVerifier:
    """Capture repository observations without mutating the worktree."""

    def __init__(self, root: Path):
        self.root = root

    def capture(self) -> GitSnapshot:
        return snapshot(self.root)

    def compare(
        self,
        before: GitSnapshot,
        *,
        meaningful_change_fn: Callable[[GitSnapshot, GitSnapshot], bool],
    ) -> VerificationResult:
        after = self.capture()
        meaningful = meaningful_change_fn(before, after)
        result = VerificationResult(
            clean_relative_to_baseline=after.worktree_fingerprint == before.worktree_fingerprint,
            meaningful_change=meaningful,
            before=before,
            after=after,
        )
        trace(
            "verification.repository",
            changed=not result.clean_relative_to_baseline,
            meaningful=result.meaningful_change,
            head=after.head,
        )
        return result
