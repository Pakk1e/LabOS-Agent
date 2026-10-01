from labos_agent.github_observer import GitHubObservation
from labos_agent.response_protocol import parse_labos_response
from labos_agent.supervisor_state_machine import SupervisorPhase, reconcile


def observation(sha="abc1234", conclusion="success", ci_sha="abc1234"):
    return GitHubObservation(
        branch="main",
        commit_sha=sha,
        ci_run_id=166,
        ci_run_number=42,
        ci_workflow="CI",
        ci_status="completed",
        ci_conclusion=conclusion,
        ci_sha=ci_sha,
        ci_name="CI",
        ci_created_at="2026-09-29T08:00:00Z",
        ci_url="https://example.test/ci/166",
    )


def response(state="DONE", commit="abc1234", ci_status="PASSED"):
    return parse_labos_response(f"""<LABOS_STATE>
STATE => {state}
TASK_STATUS => {'COMPLETE' if state == 'DONE' else 'IN_PROGRESS'}
CURRENT_COMMIT => {commit}
COMMIT_STATUS => PUSHED
REPOSITORY_CHANGED => YES
LOCAL_TESTS => PASSED
CI_RUN => 42
CI_RUN_ID => 166
CI_WORKFLOW => CI
CI_STATUS => {ci_status}
NEXT_ACTION => {'FINISH' if state == 'DONE' else 'WAIT_FOR_CI'}
</LABOS_STATE>
(STATE {state} STATE)
""")


def test_done_is_verified_against_github():
    result = reconcile(response(), observation())
    assert result.phase == SupervisorPhase.DONE
    assert result.verified is True
    assert result.ci_verified is True


def test_done_conflict_is_not_verified():
    result = reconcile(response(), observation(sha="def5678"))
    assert result.phase == SupervisorPhase.CONFLICT
    assert result.verified is False


def test_wait_ci_becomes_verified_when_exact_commit_ci_passed():
    result = reconcile(response("WAIT_CI", ci_status="IN_PROGRESS"), observation())
    assert result.phase == SupervisorPhase.WAITING_CI
    assert result.ci_verified is True


def test_legacy_done_is_unverified():
    legacy = parse_labos_response("(STATE DONE STATE)")
    result = reconcile(legacy, observation())
    assert result.phase == SupervisorPhase.DONE
    assert result.verified is False


def test_wait_for_ci_filters_to_target_sha():
    from labos_agent.supervisor import CIRun, wait_for_ci
    from datetime import datetime, timezone

    runs = [
        CIRun(1, "wrong", "completed", "success", "2026-09-29T08:00:00Z", "", "Wrong CI"),
        CIRun(2, "target", "completed", "success", "2026-09-29T08:01:00Z", "", "Target CI"),
    ]

    passed, summary = wait_for_ci(
        "owner/repo",
        datetime(2026, 9, 29, 8, 2, tzinfo=timezone.utc),
        baseline_run_ids=set(),
        timeout_seconds=1,
        poll_seconds=0,
        target_sha="target",
        request_fn=lambda _repository: runs,
        sleep_fn=lambda _seconds: None,
    )

    assert passed is True
    assert summary == "GitHub CI passed: Target CI=success"


def test_done_rejects_wrong_ci_run_identity():
    wrong = GitHubObservation(
        branch="main",
        commit_sha="abc1234",
        ci_run_id=999,
        ci_run_number=42,
        ci_status="completed",
        ci_conclusion="success",
        ci_sha="abc1234",
        ci_name="CI",
        ci_created_at="2026-09-29T08:00:00Z",
        ci_url="https://example.test/ci/999",
        ci_workflow="CI",
    )
    result = reconcile(response(), wrong)
    assert result.verified is False
    assert result.phase == SupervisorPhase.CONFLICT


def test_supervisor_syncs_remote_commit_before_phase_gate():
    from labos_agent.supervisor import ConversationSupervisor

    supervisor = ConversationSupervisor.__new__(ConversationSupervisor)
    calls = []

    supervisor._sync_workspace = lambda observed, *, reason: calls.append(
        (observed.commit_sha, reason)
    )

    supervisor._sync_if_remote_changed(
        observation("newsha"),
        "oldsha",
    )

    assert calls == [("newsha", "remote-commit-observed")]


def test_supervisor_does_not_resync_unchanged_commit():
    from labos_agent.supervisor import ConversationSupervisor

    supervisor = ConversationSupervisor.__new__(ConversationSupervisor)
    calls = []

    supervisor._sync_workspace = lambda observed, *, reason: calls.append(
        (observed.commit_sha, reason)
    )

    supervisor._sync_if_remote_changed(
        observation("same"),
        "same",
    )

    assert calls == []
