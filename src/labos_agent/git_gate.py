"""Controller-owned Git gate for validated autonomous iterations."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class GitSnapshot:
    head: str
    upstream: str
    status: str


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if not check and result.returncode:
        return ""
    return result.stdout.strip()


def snapshot(root: Path) -> GitSnapshot:
    return GitSnapshot(
        head=_git(root, "rev-parse", "HEAD"),
        upstream=_git(root, "rev-parse", "--verify", "@{upstream}"),
        status=_git(root, "status", "--porcelain=v1", "-uall"),
    )


def assert_unchanged_before_ci(root: Path, before: GitSnapshot) -> None:
    after = snapshot(root)
    if after.head != before.head:
        raise RuntimeError(
            "LocalCI gate refused: HEAD changed before LocalCI ran; "
            "the implementation iteration committed changes early"
        )
    if after.upstream != before.upstream:
        raise RuntimeError(
            "LocalCI gate refused: upstream changed before LocalCI ran; "
            "the implementation iteration pushed changes early"
        )

def prepare_repository(root: Path, *, branch_name: str = "main", allow_dirty: bool = False) -> None:
    """Synchronize the controller's working branch and reject unsafe history."""
    status = _git(root, "status", "--porcelain=v1", "-uall")
    if not allow_dirty and any(line and not line.startswith("?? ") for line in status.splitlines()):
        raise RuntimeError("Git preflight refused: tracked working-tree changes exist before the iteration")

    subprocess.run(["git", "-C", str(root), "fetch", "origin", "main"], check=True, timeout=120)

    current_branch = _git(root, "branch", "--show-current")
    if branch_name != "main":
        if current_branch != branch_name:
            local_exists = subprocess.run(["git", "-C", str(root), "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"], check=False).returncode == 0
            if local_exists:
                subprocess.run(["git", "-C", str(root), "switch", branch_name], check=True, timeout=60)
            else:
                subprocess.run(["git", "-C", str(root), "switch", "-c", branch_name, "origin/main"], check=True, timeout=60)
        subprocess.run(["git", "-C", str(root), "fetch", "origin", branch_name], check=False, timeout=120)
        remote = _git(root, "rev-parse", f"origin/{branch_name}", check=False)
        if remote:
            head = _git(root, "rev-parse", "HEAD")
            merge_base = _git(root, "merge-base", "HEAD", f"origin/{branch_name}")
            if merge_base == head and head != remote:
                subprocess.run(["git", "-C", str(root), "merge", "--ff-only", f"origin/{branch_name}"], check=True, timeout=60)
            elif merge_base != remote and merge_base != head:
                raise RuntimeError(f"Git preflight refused: local branch {branch_name} diverged from its remote")
        return

    head = _git(root, "rev-parse", "HEAD")
    remote = _git(root, "rev-parse", "origin/main")
    if head == remote:
        return
    merge_base = _git(root, "merge-base", "HEAD", "origin/main")
    if merge_base == head:
        subprocess.run(["git", "-C", str(root), "merge", "--ff-only", "origin/main"], check=True, timeout=60)
        return
    if merge_base == remote:
        raise RuntimeError("Git preflight refused: local repository is ahead of origin/main")
    raise RuntimeError("Git preflight refused: local repository has diverged from origin/main")


def _parse_untracked(status: str) -> set[str]:
    return {
        line[3:]
        for line in status.splitlines()
        if line.startswith("?? ")
    }


def commit_and_push(root: Path, before: GitSnapshot, message: str, *, branch_name: str = "main", allow_preexisting_tracked_changes: bool = False) -> str | None:
    assert_unchanged_before_ci(root, before)
    current = snapshot(root)
    if not allow_preexisting_tracked_changes and any(line and not line.startswith("?? ") for line in before.status.splitlines()):
        raise RuntimeError("Git gate refused: tracked working-tree changes existed before the iteration")
    baseline_untracked = _parse_untracked(before.status)

    # Never absorb machine-local files that existed before this iteration.
    new_untracked = _parse_untracked(current.status) - baseline_untracked
    if new_untracked:
        subprocess.run(
            ["git", "-C", str(root), "add", "--", *sorted(new_untracked)],
            check=True,
            timeout=30,
        )
    subprocess.run(["git", "-C", str(root), "add", "-u"], check=True, timeout=30)

    staged = _git(root, "diff", "--cached", "--name-only")
    if not staged:
        return None

    subprocess.run(
        ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", "commit", "-m", message],
        check=True,
        timeout=60,
    )
    sha = _git(root, "rev-parse", "HEAD")
    subprocess.run(
        ["git", "-C", str(root), "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch_name}"],
        check=True,
        timeout=120,
    )
    return sha
