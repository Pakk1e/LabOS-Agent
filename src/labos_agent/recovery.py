"""Event-driven recovery coordinator for interrupted and validated work."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from .git_gate import (
    GitSnapshot,
    can_clear_legacy_dirty_recovery,
    is_ancestor,
    snapshot,
)
from .trace import trace


@dataclass(frozen=True)
class RecoveryEvent:
    kind: str
    reason: str


class RecoveryManager:
    """Own recovery evidence and transitions instead of scattering them in the loop."""

    def __init__(self, state, project, *, snapshot_fn=snapshot, ancestor_fn=is_ancestor, remote_sha_fn=None, clear_legacy_fn=can_clear_legacy_dirty_recovery):
        self.state = state
        self.project = project
        self._snapshot = snapshot_fn
        self._is_ancestor = ancestor_fn
        self._remote_sha = remote_sha_fn
        self._clear_legacy = clear_legacy_fn

    def record_dirty(self, before: GitSnapshot, current: GitSnapshot | None = None, *, reason: str = "dirty worktree") -> RecoveryEvent:
        current = current or self._snapshot(self.project.project_root)
        self.state.pending_ci_fix = True
        self.state.pending_ci_baseline_untracked = list(before.untracked_paths)
        self.state.pending_ci_worktree_fingerprint = current.worktree_fingerprint
        trace("recovery.recorded", kind="dirty_worktree", reason=reason,
              fingerprint=current.worktree_fingerprint)
        return RecoveryEvent("dirty_worktree", reason)

    def record_push_failure(self, before: GitSnapshot, current: GitSnapshot | None = None, *, reason: str = "push failure") -> RecoveryEvent:
        return self.record_dirty(before, current, reason=reason)

    def migrate_legacy(self) -> RecoveryEvent | None:
        if not self.state.pending_ci_fix or self.state.pending_ci_worktree_fingerprint is not None:
            return None
        root = self.project.project_root
        baseline = set(self.state.pending_ci_baseline_untracked)
        if self._clear_legacy(root, baseline):
            self.clear("migrated stale recovery metadata with verified clean worktree")
            return RecoveryEvent("legacy_cleared", "verified clean worktree")

        current = self._snapshot(root)
        if (
            "success=True" in (self.state.last_ci_result or "")
            and set(current.untracked_paths).issubset(baseline)
            and current.status
        ):
            self.state.pending_ci_worktree_fingerprint = current.worktree_fingerprint
            self.state.reason = "adopted legacy validated worktree using prior CI evidence"
            trace("recovery.migrated", fingerprint=current.worktree_fingerprint)
            return RecoveryEvent("legacy_adopted", "prior LocalCI evidence")
        return None

    def reconcile_committed(self, *, revalidate=None) -> RecoveryEvent | None:
        if not self.state.pending_ci_fix or not self.state.last_commit_sha:
            return None
        root = self.project.project_root
        current = self._snapshot(root)
        if not self._is_ancestor(root, self.state.last_commit_sha, current.head):
            return None

        if current.head == self.state.last_commit_sha:
            remote_sha = current.upstream
            if not remote_sha or remote_sha != current.head:
                return None
        else:
            remote_main_sha = self._remote_branch_sha("main")
            if remote_main_sha != self.state.last_commit_sha:
                return None

        if any(
            record and len(record) >= 3 and record[2] == " " and not record.startswith("?? ")
            for record in current.status.split("\0") if record
        ):
            return None

        if revalidate is not None and not revalidate():
            return None

        self.state.pending_ci_fix = False
        self.state.pending_ci_baseline_untracked = []
        self.state.pending_ci_worktree_fingerprint = None
        self.state.pending_remote_ci_fix = False
        self.state.pending_remote_ci_sha = None
        self.state.pending_remote_ci_result = None
        self.state.github_ci_verified = False
        self.state.reason = "reconciled pushed recovery descendant; awaiting normal verification"
        trace("recovery.reconciled", commit=self.state.last_commit_sha)
        return RecoveryEvent("committed_reconciled", self.state.last_commit_sha)

    def clear(self, reason: str) -> None:
        self.state.pending_ci_fix = False
        self.state.pending_ci_baseline_untracked = []
        self.state.pending_ci_worktree_fingerprint = None
        self.state.reason = reason
        trace("recovery.cleared", reason=reason)

    def baseline(self) -> set[str] | None:
        if not self.state.pending_ci_fix:
            return None
        return set(self.state.pending_ci_baseline_untracked)

    def _remote_branch_sha(self, branch: str) -> str:
        if self._remote_sha is not None:
            return self._remote_sha(self.project.project_root, branch)
        result = subprocess.run(
            ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
            cwd=self.project.project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.split()[0] if result.returncode == 0 and result.stdout.strip() else ""
