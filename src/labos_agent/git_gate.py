"""Controller-owned Git gate for validated autonomous iterations."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import subprocess


@dataclass(frozen=True)
class GitSnapshot:
    head: str
    upstream: str
    status: str
    worktree_fingerprint: str


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": "/dev/null",
        "GIT_CONFIG_KEY_1": "core.fsmonitor",
        "GIT_CONFIG_VALUE_1": "false",
    })
    return env


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=120,
        env=_env(),
    )
    if not check and result.returncode:
        return ""
    return result.stdout.strip()


def snapshot(root: Path) -> GitSnapshot:
    status = _git(root, "status", "--porcelain=v1", "-uall")
    diff = _git(root, "diff", "HEAD", "--binary", "--")
    return GitSnapshot(
        head=_git(root, "rev-parse", "HEAD"),
        upstream=_git(root, "rev-parse", "--verify", "@{upstream}"),
        status=status,
        worktree_fingerprint=hashlib.sha256((status + "\0" + diff).encode()).hexdigest(),
    )


def assert_unchanged_before_ci(root: Path, before: GitSnapshot) -> None:
    after = snapshot(root)
    if after.head != before.head:
        raise RuntimeError("LocalCI gate refused: HEAD changed before LocalCI ran")
    if after.upstream != before.upstream:
        raise RuntimeError("LocalCI gate refused: upstream changed before LocalCI ran")


def _validate_stage_paths(paths: str) -> None:
    for raw in paths.splitlines():
        path = raw.strip()
        if not path:
            continue
        parts = Path(path).parts
        if any(part == ".git" or part == ".github" or part.startswith(".env") for part in parts):
            raise RuntimeError(f"Git gate refused sensitive path: {path}")


def prepare_repository(root: Path, *, branch_name: str = "main", allow_dirty: bool = False) -> None:
    status = _git(root, "status", "--porcelain=v1", "-uall")
    if not allow_dirty and any(line and not line.startswith("?? ") for line in status.splitlines()):
        raise RuntimeError("Git preflight refused: tracked working-tree changes exist before the iteration")
    _git(root, "fetch", "origin", "main")
    current_branch = _git(root, "branch", "--show-current")
    if branch_name != "main":
        if current_branch != branch_name:
            exists = subprocess.run(
                ["git", "-C", str(root), "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
                env=_env(), check=False,
            ).returncode == 0
            if exists:
                _git(root, "switch", branch_name)
            else:
                _git(root, "switch", "-c", branch_name, "origin/main")
        _git(root, "fetch", "origin", branch_name, check=False)
        remote = _git(root, "rev-parse", f"origin/{branch_name}", check=False)
        if remote:
            head = _git(root, "rev-parse", "HEAD")
            merge_base = _git(root, "merge-base", "HEAD", f"origin/{branch_name}")
            if merge_base == head and head != remote:
                _git(root, "merge", "--ff-only", f"origin/{branch_name}")
            elif merge_base != remote and merge_base != head:
                raise RuntimeError(f"Git preflight refused: local branch {branch_name} diverged from its remote")
        return
    head = _git(root, "rev-parse", "HEAD")
    remote = _git(root, "rev-parse", "origin/main")
    if head == remote:
        return
    merge_base = _git(root, "merge-base", "HEAD", "origin/main")
    if merge_base == head:
        _git(root, "merge", "--ff-only", "origin/main")
        return
    if merge_base == remote:
        raise RuntimeError("Git preflight refused: local repository is ahead of origin/main")
    raise RuntimeError("Git preflight refused: local repository has diverged from origin/main")


def _parse_untracked(status: str) -> set[str]:
    return {line[3:] for line in status.splitlines() if line.startswith("?? ")}


def commit_and_push(
    root: Path,
    before: GitSnapshot,
    message: str,
    *,
    branch_name: str = "main",
    allow_preexisting_tracked_changes: bool = False,
) -> str | None:
    assert_unchanged_before_ci(root, before)
    current = snapshot(root)
    if not allow_preexisting_tracked_changes and any(line and not line.startswith("?? ") for line in before.status.splitlines()):
        raise RuntimeError("Git gate refused: tracked working-tree changes existed before the iteration")
    baseline_untracked = _parse_untracked(before.status)
    new_untracked = _parse_untracked(current.status) - baseline_untracked
    if new_untracked:
        _git(root, "add", "--", *sorted(new_untracked))
    _git(root, "add", "-u")
    staged = _git(root, "diff", "--cached", "--name-only")
    _validate_stage_paths(staged)
    if not staged:
        return None
    _git(root, "commit", "-m", message)
    sha = _git(root, "rev-parse", "HEAD")
    _git(root, "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch_name}")
    return sha
