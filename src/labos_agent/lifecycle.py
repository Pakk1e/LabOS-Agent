"""Project lifecycle and human approval gates for LabOS."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path


class ProjectPhase(StrEnum):
    IDEA = "IDEA"
    BRAINSTORM = "BRAINSTORM"
    DOCUMENTATION = "DOCUMENTATION"
    PLANNING = "PLANNING"
    DEVELOPMENT = "DEVELOPMENT"
    VALIDATION = "VALIDATION"
    MAINTENANCE = "MAINTENANCE"


@dataclass(frozen=True)
class LifecycleState:
    phase: ProjectPhase
    approved: bool = False
    approved_at: str | None = None

    def __post_init__(self) -> None:
        if self.approved and self.phase is not ProjectPhase.DEVELOPMENT:
            raise ValueError("lifecycle approval is only valid in the DEVELOPMENT phase")

    @property
    def approval_required(self) -> bool:
        return self.phase is ProjectPhase.DEVELOPMENT


NEXT_PHASE = {
    ProjectPhase.IDEA: ProjectPhase.BRAINSTORM,
    ProjectPhase.BRAINSTORM: ProjectPhase.DOCUMENTATION,
    ProjectPhase.DOCUMENTATION: ProjectPhase.PLANNING,
    ProjectPhase.PLANNING: ProjectPhase.DEVELOPMENT,
    ProjectPhase.DEVELOPMENT: ProjectPhase.VALIDATION,
    ProjectPhase.VALIDATION: ProjectPhase.MAINTENANCE,
}

PROJECT_MODES = {"guided", "specification", "existing_repository"}


def normalize_phase(value: str | None) -> ProjectPhase:
    if not value:
        return ProjectPhase.IDEA
    try:
        return ProjectPhase(str(value).upper())
    except ValueError as exc:
        raise ValueError(f"invalid project phase: {value}") from exc


def normalize_project_mode(value: str | None) -> str:
    mode = str(value or "guided").strip().lower().replace("-", "_").replace(" ", "_")
    if mode not in PROJECT_MODES:
        raise ValueError(
            f"invalid project mode: {value}; expected one of {sorted(PROJECT_MODES)}"
        )
    return mode


def can_start_supervisor(phase: ProjectPhase, approved: bool) -> bool:
    return phase is not ProjectPhase.DEVELOPMENT or approved


def next_phase(phase: ProjectPhase) -> ProjectPhase | None:
    return NEXT_PHASE.get(phase)


def lifecycle_state_path(state_root: Path, project: str) -> Path:
    return state_root / project / "project_lifecycle.json"


def load_lifecycle_state(
    state_root: Path,
    project: str,
    *,
    fallback_phase: ProjectPhase = ProjectPhase.IDEA,
    fallback_approved: bool = False,
    fallback_approved_at: str | None = None,
) -> LifecycleState:
    path = lifecycle_state_path(state_root, project)
    if not path.exists():
        return LifecycleState(
            phase=fallback_phase,
            approved=fallback_approved,
            approved_at=fallback_approved_at,
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        phase = normalize_phase(raw.get("phase"))
        approved = bool(raw.get("approved", False))
        approved_at = raw.get("approved_at")
        return LifecycleState(phase=phase, approved=approved, approved_at=approved_at)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid lifecycle state for project {project}") from exc


def save_lifecycle_state(
    state_root: Path,
    project: str,
    state: LifecycleState,
) -> Path:
    path = lifecycle_state_path(state_root, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": state.phase.value,
        "approved": state.approved,
        "approved_at": state.approved_at,
        "approval_required": state.approval_required,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def phase_instruction(phase: ProjectPhase) -> str:
    instructions = {
        ProjectPhase.IDEA: "Do not develop yet. Clarify the initial idea and identify the questions that need human discussion.",
        ProjectPhase.BRAINSTORM: "Explore the idea with the human. Capture product goals, users, workflows, MVP, scope, out-of-scope items, open questions, alternatives, and technical considerations. Do not implement product features.",
        ProjectPhase.DOCUMENTATION: "Turn the agreed idea into durable project documentation. Update README.md, AGENTS.md, docs/PRODUCT.md, docs/REQUIREMENTS.md, docs/ARCHITECTURE.md, docs/USER_FLOWS.md, docs/DECISIONS.md, and docs/ROADMAP.md as appropriate. Do not begin feature implementation.",
        ProjectPhase.PLANNING: "Create a concrete implementation plan from the agreed documentation, including milestones, tasks, dependencies, technical implementation sequence, and acceptance criteria. Do not implement the planned features yet.",
        ProjectPhase.DEVELOPMENT: "Implement the human-approved plan. Keep documentation synchronized with meaningful architectural or requirement changes. Run tests and use the normal commit/CI workflow.",
        ProjectPhase.VALIDATION: "Validate the implementation against requirements, architecture, and acceptance criteria. Fix discovered issues and update documentation. Do not expand scope without approval.",
        ProjectPhase.MAINTENANCE: "Continue normal maintenance against the documented project contract, requirements, architecture, and roadmap.",
    }
    return instructions[phase]
