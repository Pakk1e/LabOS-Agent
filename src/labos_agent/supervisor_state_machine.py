"""Deterministic reconciliation rules for the ChatGPT supervisor."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .github_observer import GitHubObservation
from .response_protocol import LabOSResponse


class SupervisorPhase(StrEnum):
    START = "START"
    WORKING = "WORKING"
    WAITING_CI = "WAITING_CI"
    FIXING_CI = "FIXING_CI"
    DONE = "DONE"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class Reconciliation:
    phase: SupervisorPhase
    verified: bool
    reason: str
    new_commit: bool = False
    ci_verified: bool = False


def reconcile(
    analysis: LabOSResponse,
    observation: GitHubObservation,
    *,
    previous_commit: str | None = None,
) -> Reconciliation:
    if analysis.state is None:
        return Reconciliation(SupervisorPhase.WORKING, False, "response has no usable state")

    new_commit = bool(
        previous_commit
        and observation.commit_sha
        and observation.commit_sha != previous_commit
    )

    if analysis.state.value == "WAIT_CI":
        if (
            analysis.current_commit
            and analysis.current_commit != "UNKNOWN"
            and observation.commit_sha == analysis.current_commit
            and observation.ci_sha == analysis.current_commit
            and observation.ci_conclusion == "success"
        ):
            return Reconciliation(
                SupervisorPhase.WAITING_CI,
                True,
                "reported commit and CI are already verified",
                new_commit,
                True,
            )
        return Reconciliation(
            SupervisorPhase.WAITING_CI,
            False,
            "waiting for the reported commit's GitHub Actions result",
            new_commit,
            False,
        )

    if analysis.state.value == "FIX_CI":
        return Reconciliation(
            SupervisorPhase.FIXING_CI,
            False,
            "assistant requested CI repair",
            new_commit,
            False,
        )

    if analysis.state.value == "DONE":
        if not analysis.structured:
            return Reconciliation(
                SupervisorPhase.DONE,
                False,
                "legacy DONE marker is not independently structured",
                new_commit,
                False,
            )
        commit_verified = (
            analysis.current_commit not in (None, "UNKNOWN")
            and observation.commit_sha == analysis.current_commit
        )
        ci_verified = (
            analysis.ci_status != "PASSED"
            or (
                observation.ci_sha == analysis.current_commit
                and observation.ci_conclusion == "success"
            )
        )
        verified = commit_verified and ci_verified
        return Reconciliation(
            SupervisorPhase.DONE if verified else SupervisorPhase.CONFLICT,
            verified,
            "completion claims match GitHub" if verified else "completion claims do not fully match GitHub",
            new_commit,
            ci_verified,
        )

    return Reconciliation(
        SupervisorPhase.WORKING,
        False,
        "assistant requested continued engineering work",
        new_commit,
        False,
    )
