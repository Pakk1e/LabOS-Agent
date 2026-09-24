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
    untracked_paths: tuple[str, ...]


def _env(root: Path) -> dict[str, str]:
    resolved = root.expanduser().resolve()
    env = os.environ.copy()
    env.update({
        "GIT_DIR": str(resolved / ".git"),
        "GIT_WORK_TREE": str(resolved),
        "GIT_CEILING_DIRECTORIES": str(resolved.parent),
        "GIT_CONFIG_COUNT": "4",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": "/dev/null",
        "GIT_CONFIG_KEY_1": "core.fsmonitor",
        "GIT_CONFIG_VALUE_1": "false",
        "GIT_CONFIG_KEY_2": "user.name",
        "GIT_CONFIG_VALUE_2": "LabOS Agent",
        "GIT_CONFIG_KEY_3": "user.email",
        "GIT_CONFIG_VALUE_3": "labos-agent@localhost",
    })
    return env


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=check,
        capture_output=True,
        text=True,
        timeout=120,
        env=_env(root),
    )
    if not check and result.returncode:
        return ""
    return result.stdout.rstrip("\n")


def _untracked_paths(root: Path) -> tuple[str, ...]:
    raw = _git(root, "ls-files", "-o", "--exclude-standard", "-z")
    return tuple(sorted(p for p in raw.split("\0") if p))


UNTRACKED_FULL_HASH_LIMIT = 8 * 1024 * 1024


def _untracked_fingerprint(root: Path, paths: tuple[str, ...]) -> bytes:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        candidate = (root / path).resolve()
        root_resolved = root.resolve()
        if root_resolved not in candidate.parents and candidate != root_resolved:
            digest.update(b"OUTSIDE")
            continue
        raw_path = root / path
        try:
            stat = raw_path.lstat()
            if raw_path.is_symlink():
                digest.update(b"SYMLINK\0" + os.readlink(raw_path).encode("utf-8", "surrogateescape"))
            elif raw_path.is_file():
                digest.update(b"FILE\0")
                if stat.st_size <= UNTRACKED_FULL_HASH_LIMIT:
                    with raw_path.open("rb") as handle:
                        while chunk := handle.read(1024 * 1024):
                            digest.update(chunk)
                else:
                    digest.update(f"SIZE:{stat.st_size};MTIME:{stat.st_mtime_ns}".encode())
                    with raw_path.open("rb") as handle:
                        digest.update(handle.read(1024 * 1024))
                        if stat.st_size > 1024 * 1024:
                            handle.seek(max(0, stat.st_size - 1024 * 1024))
                            digest.update(handle.read(1024 * 1024))
            else:
                digest.update(f"OTHER:{stat.st_mode}".encode())
        except OSError as exc:
            digest.update(f"ERROR:{type(exc).__name__}:{exc}".encode())
    return digest.digest()


def git_status(root: Path) -> str:
    """Return sanitized controller-owned Git status for project inspection."""
    return _git(root, "status", "--short")


def snapshot(root: Path) -> GitSnapshot:
    status = _git(root, "status", "--porcelain=v1", "-z", "-uall")
    diff = _git(root, "diff", "HEAD", "--binary", "--")
    untracked = _untracked_paths(root)
    fingerprint = hashlib.sha256(
        status.encode("utf-8", "surrogateescape")
        + b"\0"
        + diff.encode("utf-8", "surrogateescape")
        + b"\0"
        + _untracked_fingerprint(root, untracked)
    ).hexdigest()
    return GitSnapshot(
        head=_git(root, "rev-parse", "HEAD"),
        upstream=_git(root, "rev-parse", "--verify", "@{upstream}", check=False),
        status=status,
        worktree_fingerprint=fingerprint,
        untracked_paths=untracked,
    )


def changed_paths(root: Path, before: GitSnapshot) -> tuple[str, ...]:
    """Return paths changed relative to the iteration baseline, including new untracked files."""
    current = snapshot(root)
    paths = set(_status_paths_z(current.status))
    before_untracked = set(before.untracked_paths)
    paths.update(set(current.untracked_paths) - before_untracked)
    return tuple(sorted(paths))


def meaningful_change(paths: tuple[str, ...]) -> bool:
    """Classify repository changes that represent implementation progress."""
    if not paths:
        return False
    # Tests-only changes are validation work, not implementation progress, unless
    # the task explicitly consists of tests (the controller can still commit them
    # when the caller marks the iteration as meaningful).
    meaningful_prefixes = ("src/", "app/", "lib/", "docs/", "config.", "pyproject.toml", "README")
    return any(path.startswith(meaningful_prefixes) and not path.startswith("tests/") for path in paths)


def assert_unchanged_before_ci(root: Path, before: GitSnapshot) -> None:
    after = snapshot(root)
    if after.head != before.head:
        raise RuntimeError("LocalCI gate refused: HEAD changed before LocalCI ran")
    if after.upstream != before.upstream:
        raise RuntimeError("LocalCI gate refused: upstream changed before LocalCI ran")


def _status_paths_z(status: str) -> list[str]:
    records = [record for record in status.split("\0") if record]
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        if len(record) >= 3 and record[2] == " ":
            paths.append(record[3:])
            code = record[:2]
            if code[0] in "RC" or code[1] in "RC":
                if index + 1 < len(records):
                    paths.append(records[index + 1])
                    index += 1
        else:
            paths.append(record)
        index += 1
    return paths


def _validate_sensitive_paths(paths: list[str]) -> None:
    for path in paths:
        parts = Path(path).parts
        for part in parts:
            folded = part.casefold()
            if folded in {".git", ".github"} or folded.startswith(".env"):
                raise RuntimeError(f"Git gate refused sensitive path: {path}")


def _discard_tracked_sensitive_changes(root: Path, status: str) -> None:
    """
    Recover from a sensitive tracked-path mutation without committing it.

    The autonomous execution surface must never write these paths, but a
    defense-in-depth failure should not permanently wedge the next iteration.
    Only paths already tracked by HEAD are restored here; an untracked
    sensitive file may be a pre-existing human secret and is therefore left
    untouched and still causes the normal gate refusal.
    """
    sensitive = []
    for path in _status_paths_z(status):
        parts = Path(path).parts
        if any(part.casefold() in {".git", ".github"} or part.casefold().startswith(".env") for part in parts):
            sensitive.append(path)

    if not sensitive:
        return

    tracked = []
    for path in sorted(set(sensitive)):
        result = subprocess.run(
            ["git", "cat-file", "-e", f"HEAD:{path}"],
            cwd=root,
            env=_env(root),
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            tracked.append(path)

    if tracked:
        _git(root, "restore", "--staged", "--worktree", "--", *tracked, check=False)


def prepare_repository(root: Path, *, branch_name: str = "main", allow_dirty: bool = False, expected_dirty_fingerprint: str | None = None) -> None:
    status = _git(root, "status", "--porcelain=v1", "-z", "-uall")
    _discard_tracked_sensitive_changes(root, status)
    status = _git(root, "status", "--porcelain=v1", "-z", "-uall")
    _validate_sensitive_paths(_status_paths_z(status))
    has_tracked_changes = any(
        record and len(record) >= 3 and record[2] == " " and not record.startswith("?? ")
        for record in status.split("\0") if record
    )
    has_changes = bool(status)
    if allow_dirty:
        if expected_dirty_fingerprint is None:
            raise RuntimeError("Git preflight refused: dirty recovery requires an expected worktree fingerprint")
        current = snapshot(root)
        if current.worktree_fingerprint != expected_dirty_fingerprint:
            raise RuntimeError("Git preflight refused: pending recovery worktree changed outside LabOS")
        if not has_changes:
            raise RuntimeError("Git preflight refused: pending recovery expected dirty worktree changes, but none remain")
    elif has_tracked_changes:
        raise RuntimeError("Git preflight refused: tracked working-tree changes exist before the iteration")
    _git(root, "fetch", "origin", "main")
    current_branch = _git(root, "branch", "--show-current")
    if allow_dirty and current_branch != branch_name:
        raise RuntimeError(
            f"Git preflight refused: pending recovery is on {branch_name!r}, but checkout is {current_branch!r}"
        )
    if branch_name != "main":
        if current_branch != branch_name:
            exists = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
                cwd=root, env=_env(root), check=False,
            ).returncode == 0
            if exists:
                _git(root, "switch", branch_name)
            else:
                _git(root, "switch", "-c", branch_name, "origin/main")
        _git(root, "fetch", "origin", branch_name, check=False)
        remote = _git(root, "rev-parse", f"origin/{branch_name}", check=False)
        if remote:
            head = _git(root, "rev-parse", "HEAD")
            if allow_dirty and head != remote:
                raise RuntimeError(
                    f"Git preflight refused: pending recovery cannot synchronize branch {branch_name} while dirty"
                )
            merge_base = _git(root, "merge-base", "HEAD", f"origin/{branch_name}")
            if merge_base == head and head != remote:
                _git(root, "merge", "--ff-only", f"origin/{branch_name}")
            elif merge_base != remote and merge_base != head:
                raise RuntimeError(f"Git preflight refused: local branch {branch_name} diverged from its remote")
        return
    head = _git(root, "rev-parse", "HEAD")
    remote = _git(root, "rev-parse", "origin/main")
    if allow_dirty and head != remote:
        raise RuntimeError("Git preflight refused: pending recovery cannot synchronize main while dirty")
    if head == remote:
        return
    merge_base = _git(root, "merge-base", "HEAD", "origin/main")
    if merge_base == head:
        _git(root, "merge", "--ff-only", "origin/main")
        return
    if merge_base == remote:
        raise RuntimeError("Git preflight refused: local repository is ahead of origin/main")
    raise RuntimeError("Git preflight refused: local repository has diverged from origin/main")


class GitPushError(RuntimeError):
    """Raised when a validated commit was created but the push failed and was rolled back."""


def commit_and_push(
    root: Path,
    before: GitSnapshot,
    message: str,
    *,
    branch_name: str = "main",
    allow_preexisting_tracked_changes: bool = False,
    baseline_untracked: set[str] | None = None,
) -> str | None:
    assert_unchanged_before_ci(root, before)
    current = snapshot(root)
    if not allow_preexisting_tracked_changes and any(
        record and len(record) >= 3 and record[2] == " " and not record.startswith("?? ")
        for record in before.status.split("\0") if record
    ):
        raise RuntimeError("Git gate refused: tracked working-tree changes existed before the iteration")

    baseline = baseline_untracked if baseline_untracked is not None else set(before.untracked_paths)
    new_untracked = set(current.untracked_paths) - baseline
    current_branch = _git(root, "branch", "--show-current")
    committed = False
    try:
        _validate_sensitive_paths(_status_paths_z(current.status))
        if current_branch != branch_name:
            raise RuntimeError(
                f"Git gate refused: current branch {current_branch!r} does not match expected branch {branch_name!r}"
            )
        if new_untracked:
            _git(root, "add", "--", *sorted(new_untracked))
        _git(root, "add", "-u", "--")
        staged = [path for path in _git(root, "diff", "--cached", "--name-only", "-z", "--").split("\0") if path]
        _validate_sensitive_paths(staged)
        if not staged:
            return None
        _git(root, "commit", "-m", message)
        committed = True
        sha = _git(root, "rev-parse", "HEAD")
        try:
            _git(root, "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch_name}")
        except Exception as exc:
            remote = _git(root, "ls-remote", "origin", f"refs/heads/{branch_name}", check=False)
            remote_sha = remote.split()[0] if remote else ""
            if remote_sha == sha:
                return sha
            _git(root, "reset", "--mixed", "HEAD~1", check=False)
            raise GitPushError(f"validated commit was created but push failed: {exc}") from exc
        remote = _git(root, "ls-remote", "origin", f"refs/heads/{branch_name}", check=False)
        remote_sha = remote.split()[0] if remote else ""
        if remote_sha != sha:
            _git(root, "reset", "--mixed", "HEAD~1", check=False)
            raise GitPushError(
                f"push completed but remote {branch_name} is {remote_sha or '<missing>'}, expected {sha}"
            )
        return sha
    except Exception:
        if not committed:
            _git(root, "reset", "--mixed", "HEAD", "--", check=False)
        raise
