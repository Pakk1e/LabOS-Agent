"""Explicit iteration delivery phases.

The controller loop owns lifecycle/error recovery; this module owns the
verification -> LocalCI -> commit/push -> remote-CI delivery transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import time
from typing import Callable

from .git_gate import GitPushError, assert_unchanged_before_ci, snapshot as git_snapshot, commit_and_push
from .verification import RepositoryVerifier


@dataclass(frozen=True)
class DeliveryOutcome:
    kind: str
    response: str


def verify_and_deliver(
    *,
    project,
    project_name: str,
    state,
    controller,
    before_git,
    response: str,
    deadline: datetime | None,
    run_local_ci: Callable[..., bool],
    mark_dirty_recovery: Callable[..., None],
    mark_push_failure_recovery: Callable[..., None],
    commit_recovery_baseline: Callable[..., set[str] | None],
    remote_verify: Callable[..., object],
    trajectory_event: Callable[..., None],
    save_state: Callable[[], None],
    handle_no_progress: Callable[[], bool],
    remaining_timeout: Callable[[datetime | None, float], float],
) -> DeliveryOutcome:
    """Run the explicit observation/verification/delivery phase for one iteration."""
    assert_unchanged_before_ci(project.project_root, before_git)

    verification = RepositoryVerifier(project.project_root).compare(before_git)
    after_git = verification.after
    recovery_pending = state.pending_ci_fix
    trajectory_event(
        state,
        project_name,
        "verification",
        "repository.observed",
        changed=not verification.clean_relative_to_baseline,
        meaningful=verification.meaningful_change,
        changed_paths=list(verification.changed_paths),
        recovery_pending=recovery_pending,
    )

    if verification.clean_relative_to_baseline and not recovery_pending:
        should_stop = handle_no_progress()
        save_state()
        if should_stop:
            return DeliveryOutcome("stop", response)
        time.sleep(2)
        return DeliveryOutcome("continue", response)

    meaningful = verification.meaningful_change or recovery_pending
    controller.progress_verified(meaningful=meaningful)
    if not state.meaningful_progress:
        should_stop = handle_no_progress()
        save_state()
        if should_stop:
            return DeliveryOutcome("stop", response)
        time.sleep(2)
        return DeliveryOutcome("continue", response)

    controller.ci_running()
    if deadline is not None and datetime.now().astimezone() >= deadline:
        mark_dirty_recovery(state, before_git, project=project)
        controller.stop("deadline reached before LocalCI")
        save_state()
        return DeliveryOutcome("stop", response)

    ci_ok = run_local_ci(project, state, deadline=deadline)
    if ci_ok:
        controller.ci_passed()
    if not ci_ok:
        state.pending_ci_fix = True
        state.pending_ci_baseline_untracked = list(before_git.untracked_paths)
        controller.mark_failure("local CI failed")
        save_state()
        if state.consecutive_failures >= controller.limits.max_consecutive_failures:
            controller.stop("maximum consecutive failures reached")
            save_state()
            return DeliveryOutcome("stop", response)
        time.sleep(2)
        return DeliveryOutcome("continue", response)

    if deadline is not None and datetime.now().astimezone() >= deadline:
        mark_dirty_recovery(state, before_git)
        controller.stop("deadline reached before commit")
        save_state()
        return DeliveryOutcome("stop", response)

    try:
        controller.committing()
        committed_sha = commit_and_push(
            project.project_root,
            before_git,
            f"lab-agent: iteration {state.iteration}",
            branch_name=state.branch_name,
            allow_preexisting_tracked_changes=state.pending_ci_fix,
            baseline_untracked=commit_recovery_baseline(state, project),
        )
    except GitPushError as exc:
        mark_push_failure_recovery(
            state,
            before_git,
            git_snapshot(project.project_root),
            project=project,
        )
        controller.mark_failure(
            f"validated changes could not be pushed; recovery is pending: {exc}"
        )
        save_state()
        if state.consecutive_failures >= controller.limits.max_consecutive_failures:
            controller.stop("maximum consecutive failures reached")
            save_state()
            return DeliveryOutcome("stop", response)
        time.sleep(2)
        return DeliveryOutcome("continue", response)

    if not committed_sha:
        mark_dirty_recovery(state, before_git, git_snapshot(project.project_root))
        controller.mark_failure("working tree changed but Git gate produced no commit")
        save_state()
        if state.consecutive_failures >= controller.limits.max_consecutive_failures:
            controller.stop("maximum consecutive failures reached")
            save_state()
            return DeliveryOutcome("stop", response)
        time.sleep(2)
        return DeliveryOutcome("continue", response)

    state.commit_created = True
    controller.pushing()
    state.last_action = f"committed and pushed {committed_sha}"
    state.last_commit_sha = committed_sha
    controller.remote_verified(committed_sha)
    remote_ci = remote_verify(
        project.repository,
        committed_sha,
        timeout_seconds=project.remote_ci_timeout_seconds,
        poll_seconds=project.remote_ci_poll_seconds,
    )
    state.pending_remote_ci_result = remote_ci.summary
    if not remote_ci.success:
        state.pending_remote_ci_fix = True
        state.pending_remote_ci_sha = committed_sha
        controller.mark_failure(
            f"GitHub Actions failed for exact SHA {committed_sha}: {remote_ci.summary}"
        )
        save_state()
        if state.consecutive_failures >= controller.limits.max_consecutive_failures:
            controller.stop("maximum consecutive failures reached")
            save_state()
            return DeliveryOutcome("stop", response)
        time.sleep(2)
        return DeliveryOutcome("continue", response)

    state.pending_remote_ci_fix = False
    state.pending_remote_ci_sha = None
    controller.github_ci_verified()
    controller.mark_success(continue_running=True)
    state.pending_ci_fix = False
    state.pending_ci_baseline_untracked = []
    state.pending_ci_worktree_fingerprint = None
    state.execution_requested = False
    state.execution_applied = False
    save_state()
    return DeliveryOutcome("success", response)
