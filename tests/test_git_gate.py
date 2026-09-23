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
