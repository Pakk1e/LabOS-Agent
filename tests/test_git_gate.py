from pathlib import Path
import subprocess

from labos_agent.git_gate import commit_and_push, snapshot


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_commit_and_push_excludes_preexisting_untracked_files(tmp_path: Path):
    remote = tmp_path / "remote.git"
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.name", "LabOS Test")
    _git(root, "config", "user.email", "labos@example.invalid")
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-m", "base")
    _git(root, "branch", "-M", "main")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-u", "origin", "main")

    (root / "preexisting.txt").write_text("keep\n", encoding="utf-8")
    before = snapshot(root)

    (root / "tracked.txt").write_text("validated\n", encoding="utf-8")
    (root / "new.txt").write_text("new\n", encoding="utf-8")
    sha = commit_and_push(root, before, "lab-agent: test validated push")

    assert sha == _git(root, "rev-parse", "HEAD")
    assert "tracked.txt" in _git(root, "show", "--format=", "--name-only", "HEAD")
    assert "new.txt" in _git(root, "show", "--format=", "--name-only", "HEAD")
    assert (root / "preexisting.txt").exists()
    assert _git(root, "status", "--short") == "?? preexisting.txt"
    assert _git(root, "ls-remote", "origin", "refs/heads/main").split()[0] == sha


def test_prepare_repository_syncs_remote_ahead_and_rejects_unsafe_history(tmp_path: Path):
    from labos_agent.git_gate import prepare_repository

    remote = tmp_path / "remote.git"
    root = tmp_path / "repo"
    peer = tmp_path / "peer"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    for repo in (root,):
        _git(repo, "config", "user.name", "LabOS Test")
        _git(repo, "config", "user.email", "labos@example.invalid")
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-m", "base")
    _git(root, "branch", "-M", "main")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-u", "origin", "main")

    subprocess.run(["git", "clone", "-b", "main", str(remote), str(peer)], check=True, capture_output=True)
    _git(peer, "branch", "-M", "main")
    _git(peer, "config", "user.name", "LabOS Peer")
    _git(peer, "config", "user.email", "peer@example.invalid")
    (peer / "remote.txt").write_text("remote\n", encoding="utf-8")
    _git(peer, "add", "remote.txt")
    _git(peer, "commit", "-m", "remote")
    _git(peer, "push", "origin", "main")

    prepare_repository(root)
    assert _git(root, "rev-parse", "HEAD") == _git(root, "rev-parse", "origin/main")

    (root / "local.txt").write_text("local\n", encoding="utf-8")
    _git(root, "add", "local.txt")
    _git(root, "commit", "-m", "local")
    try:
        prepare_repository(root)
    except RuntimeError as exc:
        assert "ahead of origin/main" in str(exc)
    else:
        raise AssertionError("local-ahead repository was not rejected")

    _git(peer, "pull", "--ff-only", "origin", "main")
    (peer / "peer2.txt").write_text("peer2\n", encoding="utf-8")
    _git(peer, "add", "peer2.txt")
    _git(peer, "commit", "-m", "peer2")
    _git(peer, "push", "origin", "main")

    try:
        prepare_repository(root)
    except RuntimeError as exc:
        assert "diverged" in str(exc)
    else:
        raise AssertionError("diverged repository was not rejected")

def test_snapshot_detects_untracked_files_with_spaces(tmp_path: Path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    _git(tmp_path, "config", "user.name", "LabOS Test")
    _git(tmp_path, "config", "user.email", "labos@example.invalid")
    (tmp_path / "base.txt").write_text("base")
    _git(tmp_path, "add", "base.txt")
    _git(tmp_path, "commit", "-m", "base")
    (tmp_path / "file with spaces.txt").write_text("new")
    before = snapshot(tmp_path)
    assert "file with spaces.txt" in before.status
    (tmp_path / "another file.txt").write_text("newer")
    after = snapshot(tmp_path)
    assert after.worktree_fingerprint != before.worktree_fingerprint
