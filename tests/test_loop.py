from pathlib import Path
from labos_agent.config import AppConfig, BrowserConfig, ProjectConfig
from labos_agent.loop import run_loop
from contextlib import contextmanager

import labos_agent.loop as loop
from labos_agent.state import AgentState, RunState, save_state


def test_prepare_state_resets_execution_counters_but_preserves_failure_history(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(loop, "state_path", lambda project: tmp_path / project / "current.json")
    path = tmp_path / "weather" / "current.json"
    state = AgentState(
        project="weather",
        run_id="old",
        state=RunState.STOPPED,
        iteration=23,
        rollover_count=1,
        consecutive_failures=3,
        iteration_at_last_rollover=20,
        failure_history=[
            {
                "timestamp": "2026-09-23T20:02:29+00:00",
                "iteration": 23,
                "reason": "old fetch failure",
            }
        ],
        last_ci_result="stage=test success=False",
        pending_ci_fix=True,
    )
    save_state(path, state)

    fresh = loop._prepare_state("weather")

    assert fresh.state == RunState.IDLE
    assert fresh.run_id != "old"
    assert fresh.iteration == 0
    assert fresh.rollover_count == 0
    assert fresh.consecutive_failures == 0
    assert fresh.iteration_at_last_rollover == 0
    assert fresh.failure_history == state.failure_history
    assert fresh.last_ci_result is None
    assert fresh.started_at is None
    assert fresh.stopped_at is None
    assert fresh.last_action is None
    assert fresh.reason is None


def test_prepare_state_preserves_active_run_state(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(loop, "state_path", lambda project: tmp_path / project / "current.json")
    path = tmp_path / "weather" / "current.json"
    state = AgentState(
        project="weather",
        run_id="active",
        state=RunState.WORKING,
        iteration=4,
        rollover_count=1,
        consecutive_failures=1,
        iteration_at_last_rollover=3,
        last_ci_result="stage=test success=False",
    )
    save_state(path, state)

    prepared = loop._prepare_state("weather")

    assert prepared.run_id == "active"
    assert prepared.state == RunState.WORKING
    assert prepared.iteration == 4
    assert prepared.rollover_count == 1
    assert prepared.consecutive_failures == 1
    assert prepared.iteration_at_last_rollover == 3
    assert prepared.last_ci_result == "stage=test success=False"

class FakeSession:
    def __init__(self,*args,**kwargs): self.context=type("Context",(),{"pages":[object()]})()
    def __enter__(self): return self
    def __exit__(self,*args): pass

class FakeChat:
    @staticmethod
    def select_page(context, **kwargs): return context.pages[0]
    def __init__(self,page): pass
    def assert_ready(self): pass
    def status(self):
        return type("Status", (), {"rollover_required": False})()
    def project_context_present(self,name): return True
    def send_and_wait_for_response(self,*args,**kwargs): return "no execution request"

def test_run_loop_selects_project_page_and_stops_at_iteration_limit(monkeypatch,tmp_path):
    project=ProjectConfig(name="test",repository="x",project_root=tmp_path,continuation_message="continue",project_name="Test",execution_enabled=False)
    config=AppConfig(browser=BrowserConfig(profile_dir=tmp_path),projects={"test":project})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("labos_agent.loop.BrowserSession",FakeSession)
    monkeypatch.setattr("labos_agent.loop.ChatGPTPage",FakeChat)
    monkeypatch.setattr("labos_agent.loop.prepare_repository",lambda *args,**kwargs: None)
    monkeypatch.setattr("labos_agent.loop.inspect_project",lambda *args,**kwargs: "snapshot")
    monkeypatch.setattr("labos_agent.loop.git_snapshot",lambda *args,**kwargs: type("S",(),{"head":"h","upstream":"u","status":"","worktree_fingerprint":"same"})())
    monkeypatch.setattr("labos_agent.loop.build_continuation_prompt",lambda *args,**kwargs: "prompt")
    result=run_loop(config,"test",deadline=None,max_iterations=1,max_rollovers=1)
    assert result.state.iteration == 1
    assert result.state.reason == "maximum iterations reached"

def test_prepare_state_recover_preserves_pending_ci_fix(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(loop, "state_path", lambda project: tmp_path / project / "current.json")
    path = tmp_path / "weather" / "current.json"
    state = AgentState(
        project="weather", run_id="recover-me", branch_name="agent/recover-me",
        state=RunState.STOPPED, iteration=7, consecutive_failures=3,
        pending_ci_fix=True, last_ci_result="stage=test success=False",
    )
    save_state(path, state)
    recovered = loop._prepare_state("weather", recover=True)
    assert recovered.state == RunState.IDLE
    assert recovered.run_id == "recover-me"
    assert recovered.branch_name == "agent/recover-me"
    assert recovered.pending_ci_fix is True
    assert recovered.last_ci_result == "stage=test success=False"
    assert recovered.consecutive_failures == 0


def test_prepare_state_recovers_stale_working_state_and_preserves_ci_recovery(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(loop, "state_path", lambda project: tmp_path / project / "current.json")
    path = tmp_path / "weather" / "current.json"
    state = AgentState(
        project="weather",
        run_id="stale",
        branch_name="agent/stale",
        state=RunState.WORKING,
        iteration=9,
        pending_ci_fix=True,
        pending_ci_baseline_untracked=["old.txt"],
        last_ci_result="stage=test success=False",
    )
    save_state(path, state)
    recovered = loop._prepare_state("weather", recover=True)
    assert recovered.state == RunState.IDLE
    assert recovered.run_id == "stale"
    assert recovered.branch_name == "agent/stale"
    assert recovered.pending_ci_fix is True
    assert recovered.pending_ci_baseline_untracked == ["old.txt"]
    assert recovered.last_ci_result == "stage=test success=False"
    assert recovered.consecutive_failures == 0


def test_project_lock_rejects_concurrent_owner(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(loop, "state_dir", lambda project: tmp_path / project)
    with loop._project_lock("weather"):
        try:
            with loop._project_lock("weather"):
                raise AssertionError("second lock unexpectedly acquired")
        except RuntimeError as exc:
            assert "already running" in str(exc)


def test_prepare_state_recovers_rollover_and_waiting_without_resetting_counters(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(loop, "state_path", lambda project: tmp_path / project / "current.json")
    for transient in (RunState.ROLLOVER, RunState.ITERATION_SUCCEEDED):
        path = tmp_path / "weather" / "current.json"
        state = AgentState(
            project="weather", run_id="same-run", branch_name="agent/same-run",
            state=transient, iteration=8, rollover_count=2, consecutive_failures=2,
            consecutive_no_progress=1, pending_ci_fix=True,
        )
        save_state(path, state)
        recovered = loop._prepare_state("weather", recover=True)
        assert recovered.state == RunState.IDLE
        assert recovered.run_id == "same-run"
        assert recovered.branch_name == "agent/same-run"
        assert recovered.iteration == 8
        assert recovered.rollover_count == 2
        assert recovered.consecutive_failures == 2
        assert recovered.consecutive_no_progress == 1
        assert recovered.pending_ci_fix is True


def test_run_once_records_unexpected_post_iteration_exception(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(loop, "state_path", lambda project: tmp_path / project / "current.json")
    path = tmp_path / "weather" / "current.json"
    save_state(path, AgentState(project="weather", run_id="run", state=RunState.WORKING, iteration=3))
    def explode(*args, **kwargs):
        raise RuntimeError("unexpected setup failure")
    monkeypatch.setattr(loop, "_run_once_impl", explode)
    config = AppConfig(browser=BrowserConfig(profile_dir=tmp_path), projects={})
    result = loop.run_once(config, "weather")
    assert result.state.state == RunState.ERROR
    assert result.state.reason == "unexpected setup failure"
    assert result.state.consecutive_failures == 1
    assert result.state.failure_history[-1]["reason"] == "unexpected setup failure"


def test_required_execution_cannot_fall_back_to_prose(monkeypatch, tmp_path):
    class Project:
        execution_enabled = True
    class Config:
        browser = BrowserConfig(profile_dir=tmp_path, response_timeout_seconds=1, quiet_seconds=0)
    class Chat:
        def __init__(self):
            self.calls = 0
        def send_and_wait_for_response(self, *args, **kwargs):
            self.calls += 1
            return "still prose"
    chat = Chat()
    monkeypatch.setattr(loop, "_execute_agent_requests", lambda response, project: (response, False))
    monkeypatch.setattr(loop, "save_response", lambda *args, **kwargs: None)
    try:
        loop._resolve_execution(chat, "initial prose", Project(), "test", Config())
    except RuntimeError as exc:
        assert "server execution handshake did not complete" in str(exc)
    else:
        raise AssertionError("required execution was allowed to fall back to prose")
    assert chat.calls == 4


def test_push_failure_recovery_marks_pending_ci_fix():
    state = AgentState(
        project="weather",
        run_id="run",
        pending_ci_fix=False,
        pending_ci_baseline_untracked=[],
    )
    before = type("Snapshot", (), {"untracked_paths": ("preexisting.txt",)})()
    current = type("Snapshot", (), {"worktree_fingerprint": "recovery-fp"})()
    loop._mark_push_failure_recovery(state, before, current)
    assert state.pending_ci_fix is True
    assert state.pending_ci_baseline_untracked == ["preexisting.txt"]
    assert state.pending_ci_worktree_fingerprint == "recovery-fp"


def test_run_once_outer_exception_records_failure_under_project_lock(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(loop, "state_path", lambda project: tmp_path / project / "current.json")
    path = tmp_path / "weather" / "current.json"
    save_state(path, AgentState(project="weather", run_id="run", state=RunState.WORKING, iteration=3))
    acquired = {"value": False}

    @contextmanager
    def fake_lock(project):
        acquired["value"] = True
        yield

    monkeypatch.setattr(loop, "_project_lock", fake_lock)
    monkeypatch.setattr(loop, "_run_once_impl", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("unexpected")))
    config = AppConfig(browser=BrowserConfig(profile_dir=tmp_path), projects={})
    result = loop.run_once(config, "weather")
    assert acquired["value"] is True
    assert result.state.state == RunState.ERROR


def test_delivery_phase_owns_git_push_recovery_path():
    loop_names = set(loop._run_loop_impl.__code__.co_names)
    phase_names = set(phases.verify_and_deliver.__code__.co_names)
    assert "GitPushError" not in loop_names
    assert "GitPushError" in phase_names
    assert "mark_push_failure_recovery" in phase_names


def test_resolve_execution_reprompts_when_response_promises_work_without_request(monkeypatch, tmp_path):
    class Project:
        execution_enabled = True

    class Config:
        browser = BrowserConfig(profile_dir=tmp_path, response_timeout_seconds=1, quiet_seconds=0)

    class Chat:
        def __init__(self):
            self.calls = 0
        def send_and_wait_for_response(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return "I will make the remaining source change now."
            return "final response\nLABOS_DONE"

    responses = iter([
        ("feedback 1", True),
        ("feedback 2", True),
        ("final response\nLABOS_DONE", False),
    ])
    monkeypatch.setattr(loop, "_execute_agent_requests", lambda response, project: next(responses))
    monkeypatch.setattr(loop, "save_response", lambda *args, **kwargs: None)

    chat = Chat()
    result = loop._resolve_execution(chat, "initial execution request", Project(), "test", Config())

    assert result.endswith("LABOS_DONE")
    assert chat.calls == 2


def test_resolve_execution_requires_done_marker_after_execution(monkeypatch, tmp_path):
    class Project:
        execution_enabled = True

    class Config:
        browser = BrowserConfig(profile_dir=tmp_path, response_timeout_seconds=1, quiet_seconds=0)

    class Chat:
        def __init__(self):
            self.calls = 0
        def send_and_wait_for_response(self, *args, **kwargs):
            self.calls += 1
            return "still prose"

    responses = iter([
        ("feedback", True),
        ("still prose", False),
        ("still prose", False),
        ("still prose", False),
    ])
    monkeypatch.setattr(loop, "_execute_agent_requests", lambda response, project: next(responses))
    monkeypatch.setattr(loop, "save_response", lambda *args, **kwargs: None)

    chat = Chat()
    try:
        loop._resolve_execution(chat, "initial execution request", Project(), "test", Config())
    except RuntimeError as exc:
        assert "handshake did not complete" in str(exc)
    else:
        raise AssertionError("prose-only completion was accepted")
    assert chat.calls == 4


def test_resolve_execution_returns_after_execution_round_completes(monkeypatch, tmp_path):
    class Project:
        execution_enabled = True

    class Config:
        browser = BrowserConfig(profile_dir=tmp_path, response_timeout_seconds=1, quiet_seconds=0)

    class Chat:
        def __init__(self):
            self.calls = 0

        def send_and_wait_for_response(self, *args, **kwargs):
            self.calls += 1
            return "next execution request" if self.calls == 1 else "final response\nLABOS_DONE"

    responses = iter([
        ("feedback 1", True),
        ("feedback 2", True),
        ("final response\nLABOS_DONE", False),
    ])
    monkeypatch.setattr(loop, "_execute_agent_requests", lambda response, project: next(responses))
    monkeypatch.setattr(loop, "save_response", lambda *args, **kwargs: None)

    chat = Chat()
    result = loop._resolve_execution(chat, "initial execution request", Project(), "test", Config())

    assert result == "final response\nLABOS_DONE"
    assert chat.calls == 2


def test_run_loop_source_marks_deadline_dirty_worktree_for_recovery():
    source = loop._run_loop_impl.__code__
    names = set(source.co_names)
    assert "_mark_dirty_recovery" in names


def test_remaining_timeout_respects_deadline():
    from datetime import datetime, timedelta, timezone
    deadline = datetime.now(timezone.utc) + timedelta(seconds=10)
    value = loop._remaining_timeout(deadline, 300)
    assert 0 < value <= 10


def test_remaining_timeout_rejects_expired_deadline():
    from datetime import datetime, timedelta, timezone
    deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
    try:
        loop._remaining_timeout(deadline, 300)
    except loop.DeadlineReached as exc:
        assert "deadline" in str(exc)
    else:
        raise AssertionError("expired deadline was accepted")


def test_long_run_recovery_hooks_are_present():
    source = loop._run_loop_impl.__code__
    names = set(source.co_names)
    assert "_rollover_and_process_resume" in names
    assert "_is_browser_connection_error" in names
    assert "_mark_dirty_recovery" in names
    assert "reconnect" in names
    assert "RolloverLimitReached" in names


def test_max_length_error_detection():
    assert loop._is_max_length_error(RuntimeError("ChatGPT conversation has reached maximum length"))
    assert not loop._is_max_length_error(RuntimeError("browser target closed"))


def test_project_state_prompt_is_bounded(tmp_path, monkeypatch):
    from labos_agent.project import inspect_project, MAX_STATE_FILE_CHARS
    monkeypatch.setattr("labos_agent.project.git_status", lambda root: "")
    path = tmp_path / "AGENTS.md"
    path.write_text("x" * (MAX_STATE_FILE_CHARS + 100), encoding="utf-8")
    snapshot = inspect_project(tmp_path, "example/repo", ("AGENTS.md",))
    assert len(snapshot.files["AGENTS.md"]) < MAX_STATE_FILE_CHARS + 300
    assert "state file truncated" in snapshot.files["AGENTS.md"]


def test_hard_limit_rollover_preserves_iteration_dirty_recovery(monkeypatch, tmp_path):
    class Project:
        project_root = tmp_path
        project_name = "Test"
        name = "test"

    state = AgentState(project="test", run_id="run", iteration=3)
    baseline = type("Snapshot", (), {"worktree_fingerprint": "before", "untracked_paths": ("old.txt",)})()
    changed = type("Snapshot", (), {"worktree_fingerprint": "after", "untracked_paths": ("old.txt", "new.txt")})()
    snapshots = iter([baseline, changed])
    monkeypatch.setattr(loop, "git_snapshot", lambda root: next(snapshots))
    monkeypatch.setattr(loop, "rollover_from_max_length", lambda *args, **kwargs: ("continue", None, "resume"))
    monkeypatch.setattr(loop, "_resolve_execution", lambda *args, **kwargs: "resume")
    monkeypatch.setattr(loop, "save_response", lambda *args, **kwargs: None)
    config = type("Config", (), {"browser": BrowserConfig(profile_dir=tmp_path)})()

    continuation, response = loop._rollover_and_process_resume(
        object(), Project(), state, "test", config, deadline=None, max_rollovers=3,
        recovery_baseline=baseline,
    )

    assert continuation == "continue"
    assert response == "resume"
    assert state.pending_ci_fix is True
    assert state.pending_ci_baseline_untracked == ["old.txt"]


def test_migrate_legacy_dirty_recovery_adopts_validated_tracked_changes(tmp_path: Path, monkeypatch):
    from labos_agent.loop import _migrate_legacy_dirty_recovery

    class Snapshot:
        head = "head"
        status = " M src/change.py"
        worktree_fingerprint = "fingerprint"
        untracked_paths = (".venv/bin/python",)

    state = AgentState(
        project="weather",
        run_id="run",
        pending_ci_fix=True,
        pending_ci_baseline_untracked=[".venv/bin/python"],
        last_ci_result="stage=test success=True",
    )
    monkeypatch.setattr(loop, "can_clear_legacy_dirty_recovery", lambda *args: False)
    monkeypatch.setattr(loop, "git_snapshot", lambda root: Snapshot())

    _migrate_legacy_dirty_recovery(state, tmp_path)

    assert state.pending_ci_fix is True
    assert state.pending_ci_worktree_fingerprint == "fingerprint"
    assert "adopted legacy validated worktree" in state.reason


def test_continue_path_reconciles_recovery_before_dirty_preflight():
    source = loop._run_loop_impl.__code__
    names = set(source.co_names)
    assert "_reconcile_committed_recovery" in names


def test_reconcile_committed_recovery_keeps_pending_when_local_ci_fails(tmp_path, monkeypatch):
    class Project:
        project_root = tmp_path

    state = AgentState(
        project="weather",
        run_id="run",
        pending_ci_fix=True,
        pending_ci_worktree_fingerprint="fingerprint",
        last_commit_sha="recovery-sha",
    )
    current = type("Snapshot", (), {
        "head": "descendant-sha",
        "upstream": "main-sha",
        "status": "",
        "untracked_paths": (),
        "worktree_fingerprint": "current-fingerprint",
    })()
    monkeypatch.setattr(loop, "git_snapshot", lambda root: current)
    monkeypatch.setattr(loop, "is_ancestor", lambda root, ancestor, descendant: True)
    monkeypatch.setattr(loop, "_git_remote_branch_sha", lambda root, branch: state.last_commit_sha)
    monkeypatch.setattr(loop, "_run_local_ci", lambda project, state: False)

    loop._reconcile_committed_recovery(state, Project())

    assert state.pending_ci_fix is True
    assert state.pending_ci_worktree_fingerprint == "fingerprint"
    assert state.last_commit_sha == "recovery-sha"


def test_reconcile_committed_recovery_accepts_pushed_descendant_with_new_untracked_files(tmp_path, monkeypatch):
    class Project:
        project_root = tmp_path
        repository = "Pakk1e/VilaPro-Weather"
        remote_ci_timeout_seconds = 1
        remote_ci_poll_seconds = 0

    state = AgentState(
        project="weather",
        run_id="run",
        pending_ci_fix=True,
        pending_ci_baseline_untracked=["preexisting.txt"],
        pending_ci_worktree_fingerprint="old-fingerprint",
        last_commit_sha="d48d18ae4c97c7e72db9fd10ca3d75021cd48b44",
    )

    current = type(
        "Snapshot",
        (),
        {
            "head": "ab9b64b5a1564e492a1d03c2d922459d6be08ee3",
            "upstream": "d48d18ae4c97c7e72db9fd10ca3d75021cd48b44",
            "status": "?? .venv/bin/python\\0",
            "untracked_paths": (".venv/bin/python",),
            "worktree_fingerprint": "new-fingerprint",
        },
    )()

    monkeypatch.setattr(loop, "git_snapshot", lambda root: current)
    monkeypatch.setattr(loop, "is_ancestor", lambda root, ancestor, descendant: (
        ancestor == state.last_commit_sha and descendant == current.head
    ))
    monkeypatch.setattr(loop, "_git_remote_branch_sha", lambda root, branch: state.last_commit_sha)
    monkeypatch.setattr(loop, "_run_local_ci", lambda project, state: (
        setattr(state, "last_ci_result", "stage=test success=True") or True
    ))

    loop._reconcile_committed_recovery(state, Project())

    assert state.pending_ci_fix is False
    assert state.pending_ci_baseline_untracked == []
    assert state.pending_ci_worktree_fingerprint is None
    assert state.pending_remote_ci_fix is False
    assert state.pending_remote_ci_sha is None
    assert state.github_ci_verified is False
    assert state.pending_remote_ci_result is None
    assert state.github_ci_verified is False
    assert "reconciled pushed recovery descendant" in state.reason
