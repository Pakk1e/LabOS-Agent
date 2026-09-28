from pathlib import Path
from types import SimpleNamespace

from labos_agent.recovery import RecoveryManager
from labos_agent.git_gate import ignored_paths, snapshot


def test_recovery_manager_records_exact_worktree_fingerprint(tmp_path: Path):
    import subprocess
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "LabOS Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "labos@example.invalid"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("x=1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "src/x.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "base"], check=True, capture_output=True)

    project = SimpleNamespace(project_root=tmp_path)
    state = SimpleNamespace(
        pending_ci_fix=False,
        pending_ci_baseline_untracked=[],
        pending_ci_worktree_fingerprint=None,
        pending_remote_ci_fix=False,
        pending_remote_ci_sha=None,
        pending_remote_ci_result=None,
        last_commit_sha=None,
        last_ci_result=None,
        reason=None,
    )
    before = snapshot(tmp_path)
    (tmp_path / "src" / "x.py").write_text("x=2\n", encoding="utf-8")
    current = snapshot(tmp_path)

    event = RecoveryManager(state, project).record_dirty(before, current, reason="test")
    assert event.kind == "dirty_worktree"
    assert state.pending_ci_fix
    assert state.pending_ci_worktree_fingerprint == current.worktree_fingerprint
    assert state.pending_ci_baseline_untracked == list(before.untracked_paths)


def test_recovery_manager_clear_removes_pending_recovery(tmp_path: Path):
    project = SimpleNamespace(project_root=tmp_path)
    state = SimpleNamespace(
        pending_ci_fix=True,
        pending_ci_baseline_untracked=["external.txt"],
        pending_ci_worktree_fingerprint="fingerprint",
        reason=None,
    )
    manager = RecoveryManager(state, project)
    manager.clear("test clear")
    assert not state.pending_ci_fix
    assert state.pending_ci_baseline_untracked == []
    assert state.pending_ci_worktree_fingerprint is None
    assert state.reason == "test clear"


def test_recover_interrupted_iteration_requires_same_persisted_observation():
    from labos_agent.git_gate import GitSnapshot

    baseline = GitSnapshot(
        head="base",
        upstream="base",
        status="",
        worktree_fingerprint="baseline",
        untracked_paths=(),
    )
    observed = GitSnapshot(
        head="base",
        upstream="base",
        status=" M docs/START_HERE.md\\0",
        worktree_fingerprint="observed",
        untracked_paths=(),
    )
    project = SimpleNamespace(project_root=Path("/project"))
    state = SimpleNamespace(
        pending_ci_fix=False,
        pending_ci_baseline_untracked=[],
        pending_ci_worktree_fingerprint=None,
        iteration_baseline_worktree_fingerprint=baseline.worktree_fingerprint,
        iteration_baseline_untracked=[],
        iteration_observed_worktree_fingerprint=observed.worktree_fingerprint,
        execution_requested=True,
        reason=None,
    )
    manager = RecoveryManager(state, project, snapshot_fn=lambda _: observed)

    event = manager.recover_interrupted_iteration()

    assert event is not None
    assert event.kind == "interrupted_worktree_recovered"
    assert state.pending_ci_fix is True
    assert state.pending_ci_worktree_fingerprint == "observed"


def test_recover_interrupted_iteration_rejects_post_observation_mutation():
    from labos_agent.git_gate import GitSnapshot

    observed = GitSnapshot(
        head="base",
        upstream="base",
        status=" M docs/START_HERE.md\\0",
        worktree_fingerprint="observed",
        untracked_paths=(),
    )
    mutated = GitSnapshot(
        head="base",
        upstream="base",
        status=" M docs/START_HERE.md\\0",
        worktree_fingerprint="mutated",
        untracked_paths=(),
    )
    project = SimpleNamespace(project_root=Path("/project"))
    state = SimpleNamespace(
        pending_ci_fix=False,
        pending_ci_baseline_untracked=[],
        pending_ci_worktree_fingerprint=None,
        iteration_baseline_worktree_fingerprint="baseline",
        iteration_baseline_untracked=[],
        iteration_observed_worktree_fingerprint="observed",
        execution_requested=True,
        reason=None,
    )
    manager = RecoveryManager(state, project, snapshot_fn=lambda _: mutated)

    event = manager.recover_interrupted_iteration()

    assert event is not None
    assert event.kind == "interrupted_worktree_conflict"
    assert state.pending_ci_fix is False
    assert "changed after" in state.reason


def test_recover_interrupted_iteration_ignores_iterations_without_observation():
    project = SimpleNamespace(project_root=Path("/project"))
    state = SimpleNamespace(
        pending_ci_fix=False,
        pending_ci_baseline_untracked=[],
        pending_ci_worktree_fingerprint=None,
        iteration_baseline_worktree_fingerprint="baseline",
        iteration_baseline_untracked=[],
        iteration_observed_worktree_fingerprint=None,
        execution_requested=True,
        reason=None,
    )
    manager = RecoveryManager(state, project, snapshot_fn=lambda _: None)

    assert manager.recover_interrupted_iteration() is None


def _legacy_repo(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "LabOS Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "labos@example.invalid"], check=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "START_HERE.md").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "base"], check=True, capture_output=True)
    return subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()


def _legacy_state(head: str):
    from labos_agent.state import IterationStage

    return SimpleNamespace(
        project="weather",
        pending_ci_fix=False,
        pending_ci_baseline_untracked=[],
        pending_ci_worktree_fingerprint=None,
        pending_remote_ci_fix=False,
        pending_remote_ci_sha=None,
        pending_remote_ci_result=None,
        last_commit_sha=None,
        last_ci_result=None,
        reason=None,
        iteration_stage=IterationStage.ITERATION_STARTED,
        iteration_started_sha=head,
        execution_requested=False,
    )


def test_recover_legacy_interrupted_write_from_exact_last_response(tmp_path: Path, monkeypatch):
    head = _legacy_repo(tmp_path)
    (tmp_path / "docs" / "START_HERE.md").write_text("new\n", encoding="utf-8")
    state_dir = tmp_path / "state" / "weather"
    state_dir.mkdir(parents=True)
    (state_dir / "last_response.md").write_text(
        'labos-exec  {"action":"write_file","path":"docs/START_HERE.md","content":"new\\n"}',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    state = _legacy_state(head)
    project = SimpleNamespace(project_root=tmp_path)
    event = RecoveryManager(state, project, state_root=tmp_path / "state").recover_interrupted_iteration()

    assert event is not None
    assert event.kind == "legacy_interrupted_worktree_recovered"
    assert state.pending_ci_fix is True
    assert state.pending_ci_worktree_fingerprint
    assert state.pending_ci_baseline_untracked == ["state/weather/last_response.md"]


def test_recover_legacy_interrupted_write_rejects_mismatched_content(tmp_path: Path, monkeypatch):
    head = _legacy_repo(tmp_path)
    (tmp_path / "docs" / "START_HERE.md").write_text("operator change\n", encoding="utf-8")
    state_dir = tmp_path / "state" / "weather"
    state_dir.mkdir(parents=True)
    (state_dir / "last_response.md").write_text(
        'labos-exec  {"action":"write_file","path":"docs/START_HERE.md","content":"agent change\\n"}',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    state = _legacy_state(head)
    project = SimpleNamespace(project_root=tmp_path)
    assert RecoveryManager(state, project, state_root=tmp_path / "state").recover_interrupted_iteration() is None
    assert state.pending_ci_fix is False


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

    state_dir = tmp_path / "state" / "weather"
    state_dir.mkdir(parents=True)
    (state_dir / "last_response.md").write_text(
        'labos-exec {"action":"write_file","path":".gitignore","content":"*.log\\nexternal.txt\\n"}\n'
        'labos-exec {"action":"write_file","path":"docs/START_HERE.md","content":"new\\n"}',
        encoding="utf-8",
    )
    (tmp_path / "external.txt").write_text("pre-existing\n", encoding="utf-8")
    before = snapshot(tmp_path)

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
    project = SimpleNamespace(project_root=tmp_path)

    event = RecoveryManager(state, project, state_root=tmp_path / "state").reconcile_worktree_fingerprint()

    assert event is not None
    assert event.kind == "worktree_fingerprint_reconciled"
    assert state.pending_ci_worktree_fingerprint == current.worktree_fingerprint
    assert state.pending_ci_worktree_fingerprint != recorded.worktree_fingerprint


def test_reconcile_worktree_fingerprint_rejects_new_untracked_file(tmp_path: Path):
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

    assert RecoveryManager(state, project, state_root=tmp_path / "state").reconcile_worktree_fingerprint() is None
    assert state.pending_ci_worktree_fingerprint == "persisted"


def test_reconcile_worktree_fingerprint_rejects_deleted_baseline_file(tmp_path: Path):
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

    assert RecoveryManager(state, project, state_root=tmp_path / "state").reconcile_worktree_fingerprint() is None
    assert state.pending_ci_worktree_fingerprint == "persisted"


def test_reconcile_worktree_fingerprint_rejects_unverified_tracked_change(tmp_path: Path):
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

    assert RecoveryManager(state, project, state_root=tmp_path / "state").reconcile_worktree_fingerprint() is None
    assert state.pending_ci_worktree_fingerprint == "persisted"


def _clean_descendant_state(started_sha: str):
    return SimpleNamespace(
        project="weather", pending_ci_fix=True,
        pending_ci_baseline_untracked=[],
        pending_ci_worktree_fingerprint="stale",
        pending_remote_ci_fix=False, pending_remote_ci_sha=None,
        pending_remote_ci_result=None, github_ci_verified=False,
        iteration_started_sha=started_sha, reason=None,
    )


def test_reconcile_clean_pushed_descendant_requires_single_commit(tmp_path: Path):
    import subprocess
    started = _legacy_repo(tmp_path)
    (tmp_path / "docs" / "START_HERE.md").write_text("new\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "validated change"], check=True, capture_output=True)
    head = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    state = _clean_descendant_state(started)
    manager = RecoveryManager(state, SimpleNamespace(project_root=tmp_path), remote_sha_fn=lambda _root, _branch: head)
    event = manager.reconcile_clean_pushed_descendant()
    assert event is not None
    assert event.kind == "clean_pushed_descendant_reconciled"
    assert state.pending_ci_fix is False
    assert state.pending_ci_worktree_fingerprint is None


def test_reconcile_clean_pushed_descendant_rejects_unpublished_commit(tmp_path: Path):
    import subprocess
    started = _legacy_repo(tmp_path)
    (tmp_path / "docs" / "START_HERE.md").write_text("new\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "local only"], check=True, capture_output=True)
    state = _clean_descendant_state(started)
    manager = RecoveryManager(state, SimpleNamespace(project_root=tmp_path), remote_sha_fn=lambda _root, _branch: started)
    assert manager.reconcile_clean_pushed_descendant() is None
    assert state.pending_ci_fix is True


def test_reconcile_clean_pushed_descendant_rejects_dirty_worktree(tmp_path: Path):
    import subprocess
    started = _legacy_repo(tmp_path)
    (tmp_path / "docs" / "START_HERE.md").write_text("new\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "validated change"], check=True, capture_output=True)
    head = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    (tmp_path / "docs" / "START_HERE.md").write_text("operator change\n", encoding="utf-8")
    state = _clean_descendant_state(started)
    manager = RecoveryManager(state, SimpleNamespace(project_root=tmp_path), remote_sha_fn=lambda _root, _branch: head)
    assert manager.reconcile_clean_pushed_descendant() is None
    assert state.pending_ci_fix is True


def test_reconcile_clean_pushed_descendant_rejects_multiple_commits(tmp_path: Path):
    import subprocess
    started = _legacy_repo(tmp_path)
    for value in ("one\n", "two\n"):
        (tmp_path / "docs" / "START_HERE.md").write_text(value, encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "incremental change"], check=True, capture_output=True)
    head = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    state = _clean_descendant_state(started)
    manager = RecoveryManager(state, SimpleNamespace(project_root=tmp_path), remote_sha_fn=lambda _root, _branch: head)
    assert manager.reconcile_clean_pushed_descendant() is None
    assert state.pending_ci_fix is True
