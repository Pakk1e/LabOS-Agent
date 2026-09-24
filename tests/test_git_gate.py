from pathlib import Path
import subprocess

from labos_agent.git_gate import GitPushError, commit_and_push, snapshot


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


def test_commit_and_push_handles_filename_with_spaces(tmp_path: Path):
    remote = tmp_path / "remote.git"
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.name", "LabOS Test")
    _git(root, "config", "user.email", "labos@example.invalid")
    (root / "base.txt").write_text("base")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-m", "base")
    _git(root, "branch", "-M", "main")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-u", "origin", "main")
    before = snapshot(root)
    (root / "file with spaces.txt").write_text("new")
    sha = commit_and_push(root, before, "spaces")
    assert sha == _git(root, "rev-parse", "HEAD")
    assert "file with spaces.txt" in _git(root, "show", "--format=", "--name-only", "HEAD")


def test_gate_refusal_unstages_sensitive_paths(tmp_path: Path):
    remote = tmp_path / "remote.git"
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.name", "LabOS Test")
    _git(root, "config", "user.email", "labos@example.invalid")
    (root / "base.txt").write_text("base")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-m", "base")
    _git(root, "branch", "-M", "main")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-u", "origin", "main")
    before = snapshot(root)
    (root / ".env.example").write_text("SECRET=not-secret")
    try:
        commit_and_push(root, before, "reject sensitive")
    except RuntimeError as exc:
        assert "sensitive path" in str(exc)
    else:
        raise AssertionError("sensitive path was not rejected")
    assert _git(root, "diff", "--cached", "--name-only") == ""


def test_nested_sensitive_paths_are_rejected_before_staging(tmp_path: Path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    _git(tmp_path, "config", "user.name", "LabOS Test")
    _git(tmp_path, "config", "user.email", "labos@example.invalid")
    (tmp_path / "base.txt").write_text("base")
    _git(tmp_path, "add", "base.txt")
    _git(tmp_path, "commit", "-m", "base")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / ".github").mkdir()
    (tmp_path / "sub" / ".github" / "é.yml").write_text("blocked")
    before = snapshot(tmp_path)
    try:
        commit_and_push(tmp_path, before, "reject nested")
    except RuntimeError as exc:
        assert "sensitive path" in str(exc)
    else:
        raise AssertionError("nested sensitive path was not rejected")
    assert _git(tmp_path, "diff", "--cached", "--name-only") == ""


def test_recovery_commit_includes_untracked_file_from_failed_iteration(tmp_path: Path):
    remote = tmp_path / "remote.git"
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.name", "LabOS Test")
    _git(root, "config", "user.email", "labos@example.invalid")
    (root / "base.txt").write_text("base")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-m", "base")
    _git(root, "branch", "-M", "main")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-u", "origin", "main")
    before_failed_iteration = snapshot(root)
    (root / "a.txt").write_text("tracked fix")
    (root / "feature.txt").write_text("new fix")
    recovery_snapshot = snapshot(root)
    (root / "feature.txt").write_text("new fix updated")
    sha = commit_and_push(
        root,
        recovery_snapshot,
        "recovery",
        baseline_untracked=set(before_failed_iteration.untracked_paths),
    )
    changed = _git(root, "show", "--format=", "--name-only", sha)
    assert "a.txt" in changed
    assert "feature.txt" in changed


def test_untracked_content_changes_change_fingerprint(tmp_path: Path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    _git(tmp_path, "config", "user.name", "LabOS Test")
    _git(tmp_path, "config", "user.email", "labos@example.invalid")
    (tmp_path / "base.txt").write_text("base")
    _git(tmp_path, "add", "base.txt")
    _git(tmp_path, "commit", "-m", "base")
    (tmp_path / "new.txt").write_text("one")
    before = snapshot(tmp_path)
    (tmp_path / "new.txt").write_text("two")
    after = snapshot(tmp_path)
    assert after.worktree_fingerprint != before.worktree_fingerprint


def test_prepare_repository_recovers_tracked_sensitive_worktree_change(tmp_path: Path):
    from labos_agent.git_gate import prepare_repository

    remote = tmp_path / "remote.git"
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.name", "LabOS Test")
    _git(root, "config", "user.email", "labos@example.invalid")
    workflow = root / ".github" / "workflows"
    workflow.mkdir(parents=True)
    (workflow / "ci.yml").write_text("name: CI\n")
    (root / "base.txt").write_text("base")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    _git(root, "branch", "-M", "main")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-u", "origin", "main")

    (workflow / "ci.yml").write_text("name: attacker\n")
    prepare_repository(root)
    assert (workflow / "ci.yml").read_text() == "name: CI\n"
    assert _git(root, "status", "--short") == ""


def test_prepare_repository_still_refuses_untracked_sensitive_path(tmp_path: Path):
    from labos_agent.git_gate import prepare_repository

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    _git(tmp_path, "config", "user.name", "LabOS Test")
    _git(tmp_path, "config", "user.email", "labos@example.invalid")
    (tmp_path / "base.txt").write_text("base")
    _git(tmp_path, "add", "base.txt")
    _git(tmp_path, "commit", "-m", "base")
    (tmp_path / ".env.local").write_text("SECRET=keep")

    try:
        prepare_repository(tmp_path)
    except RuntimeError as exc:
        assert "sensitive path" in str(exc)
    else:
        raise AssertionError("untracked sensitive path was not rejected")


def test_commit_and_push_requires_expected_branch(tmp_path: Path):
    remote = tmp_path / "remote.git"
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.name", "LabOS Test")
    _git(root, "config", "user.email", "labos@example.invalid")
    (root / "base.txt").write_text("base")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-m", "base")
    _git(root, "branch", "-M", "main")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-u", "origin", "main")
    before = snapshot(root)
    (root / "change.txt").write_text("change")
    try:
        commit_and_push(root, before, "wrong branch", branch_name="agent/test")
    except RuntimeError as exc:
        assert "does not match expected branch" in str(exc)
    else:
        raise AssertionError("branch mismatch was not rejected")
    assert _git(root, "rev-parse", "HEAD") == before.head
    assert _git(root, "status", "--short") == "?? change.txt"


def test_commit_and_push_resets_commit_when_push_fails(tmp_path: Path):
    root = tmp_path / "repo"
    remote = tmp_path / "missing-remote.git"
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.name", "LabOS Test")
    _git(root, "config", "user.email", "labos@example.invalid")
    (root / "base.txt").write_text("base")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-m", "base")
    _git(root, "branch", "-M", "main")
    _git(root, "remote", "add", "origin", str(remote))
    before = snapshot(root)
    (root / "change.txt").write_text("change")
    try:
        commit_and_push(root, before, "push failure")
    except GitPushError as exc:
        assert "push failed" in str(exc).lower()
    else:
        raise AssertionError("push failure was not surfaced")
    assert _git(root, "rev-parse", "HEAD") == before.head
    assert _git(root, "status", "--short") == "?? change.txt"


def test_prepare_repository_rejects_changed_pending_recovery_worktree(tmp_path: Path):
    from labos_agent.git_gate import prepare_repository

    remote = tmp_path / "remote.git"
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.name", "LabOS Test")
    _git(root, "config", "user.email", "labos@example.invalid")
    (root / "base.txt").write_text("base")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-m", "base")
    _git(root, "branch", "-M", "main")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-u", "origin", "main")

    (root / "fix.txt").write_text("agent fix")
    expected = snapshot(root).worktree_fingerprint
    (root / "fix.txt").write_text("unexpected mutation")

    try:
        prepare_repository(
            root,
            allow_dirty=True,
            expected_dirty_fingerprint=expected,
        )
    except RuntimeError as exc:
        assert "changed outside LabOS" in str(exc)
    else:
        raise AssertionError("changed recovery worktree was accepted")
