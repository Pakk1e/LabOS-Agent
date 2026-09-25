from pathlib import Path
from types import SimpleNamespace

from labos_agent.recovery import RecoveryManager
from labos_agent.git_gate import snapshot


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
