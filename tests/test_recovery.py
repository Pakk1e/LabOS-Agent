def test_ignored_paths_distinguishes_ignored_from_deleted(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "LabOS Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "labos@example.invalid"], check=True)
    (tmp_path / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    (tmp_path / "keep.txt").write_text("keep\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "base"], check=True, capture_output=True)
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "python").write_text("x\n", encoding="utf-8")
    assert ignored_paths(tmp_path, (".venv/python", "missing.txt")) == {".venv/python"}


def test_reconcile_worktree_fingerprint_after_ignore_rule_change(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "LabOS Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "labos@example.invalid"], check=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "START_HERE.md").write_text("old\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "base"], check=True, capture_output=True)

    (tmp_path / "external.txt").write_text("pre-existing\n", encoding="utf-8")
    before = snapshot(tmp_path)

    # Simulate the recovered tracked changes. The persisted fingerprint still
    # carries the pre-existing untracked status view from before the ignore-rule
    # change, even though the files are now ignored by Git.
    (tmp_path / ".gitignore").write_text("*.log\nexternal.txt\n", encoding="utf-8")
    (tmp_path / "docs" / "START_HERE.md").write_text("new\n", encoding="utf-8")
    recorded = snapshot(
        tmp_path,
        untracked_paths=before.untracked_paths,
        status_untracked_paths=before.untracked_paths,
    )
    current = snapshot(tmp_path)
    assert "?? external.txt" in recorded.status
    assert "?? external.txt" not in current.status
    assert current.worktree_fingerprint != recorded.worktree_fingerprint

    state = SimpleNamespace(
        project="weather",
        pending_ci_fix=True,
        pending_ci_baseline_untracked=list(before.untracked_paths),
        pending_ci_worktree_fingerprint=recorded.worktree_fingerprint,
        iteration_started_sha=subprocess.check_output(
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
        ).strip(),
        reason=None,
    )
    state_dir = tmp_path / "state" / "weather"
    state_dir.mkdir(parents=True)
    (state_dir / "last_response.md").write_text(
        'labos-exec {"action":"write_file","path":".gitignore","content":"*.log\\nexternal.txt\\n"}\n'
        'labos-exec {"action":"write_file","path":"docs/START_HERE.md","content":"new\\n"}',
        encoding="utf-8",
    )
    project = SimpleNamespace(project_root=tmp_path)

    event = RecoveryManager(state, project).reconcile_worktree_fingerprint()

    assert event is not None
    assert event.kind == "worktree_fingerprint_reconciled"
    assert state.pending_ci_worktree_fingerprint == current.worktree_fingerprint
    assert state.pending_ci_worktree_fingerprint != recorded.worktree_fingerprint



def test_reconcile_worktree_fingerprint_rejects_new_untracked_file(tmp_path: Path):
    import subprocess

    head = _legacy_repo(tmp_path)
    (tmp_path / "old.txt").write_text("baseline\n", encoding="utf-8")
    before = snapshot(tmp_path)
    (tmp_path / ".gitignore").write_text("old.txt\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("operator\n", encoding="utf-8")

    state = SimpleNamespace(
        project="weather",
        pending_ci_fix=True,
        pending_ci_baseline_untracked=list(before.untracked_paths),
        pending_ci_worktree_fingerprint="persisted",
        iteration_started_sha=head,
        reason=None,
    )
    project = SimpleNamespace(project_root=tmp_path)
    state_dir = tmp_path / "state" / "weather"
    state_dir.mkdir(parents=True)
    (state_dir / "last_response.md").write_text(
        'labos-exec {"action":"write_file","path":".gitignore","content":"old.txt\\n"}',
        encoding="utf-8",
    )

    assert RecoveryManager(state, project).reconcile_worktree_fingerprint() is None
    assert state.pending_ci_worktree_fingerprint == "persisted"


def test_reconcile_worktree_fingerprint_rejects_deleted_baseline_file(tmp_path: Path):
    import subprocess

    head = _legacy_repo(tmp_path)
    (tmp_path / "old.txt").write_text("baseline\n", encoding="utf-8")
    before = snapshot(tmp_path)
    (tmp_path / ".gitignore").write_text("old.txt\n", encoding="utf-8")
    (tmp_path / "old.txt").unlink()

    state = SimpleNamespace(
        project="weather",
        pending_ci_fix=True,
        pending_ci_baseline_untracked=list(before.untracked_paths),
        pending_ci_worktree_fingerprint="persisted",
        iteration_started_sha=head,
        reason=None,
    )
    project = SimpleNamespace(project_root=tmp_path)
    state_dir = tmp_path / "state" / "weather"
    state_dir.mkdir(parents=True)
    (state_dir / "last_response.md").write_text(
        'labos-exec {"action":"write_file","path":".gitignore","content":"old.txt\\n"}',
        encoding="utf-8",
    )

    assert RecoveryManager(state, project).reconcile_worktree_fingerprint() is None
    assert state.pending_ci_worktree_fingerprint == "persisted"


def test_reconcile_worktree_fingerprint_rejects_unverified_tracked_change(tmp_path: Path):
    import subprocess

    head = _legacy_repo(tmp_path)
    (tmp_path / "old.txt").write_text("baseline\n", encoding="utf-8")
    before = snapshot(tmp_path)
    (tmp_path / ".gitignore").write_text("old.txt\n", encoding="utf-8")
    (tmp_path / "docs" / "START_HERE.md").write_text("unexpected\n", encoding="utf-8")

    state = SimpleNamespace(
        project="weather",
        pending_ci_fix=True,
        pending_ci_baseline_untracked=list(before.untracked_paths),
        pending_ci_worktree_fingerprint="persisted",
        iteration_started_sha=head,
        reason=None,
    )
    project = SimpleNamespace(project_root=tmp_path)
    state_dir = tmp_path / "state" / "weather"
    state_dir.mkdir(parents=True)
    (state_dir / "last_response.md").write_text(
        'labos-exec {"action":"write_file","path":".gitignore","content":"old.txt\\n"}',
        encoding="utf-8",
    )

    assert RecoveryManager(state, project).reconcile_worktree_fingerprint() is None
    assert state.pending_ci_worktree_fingerprint == "persisted"
