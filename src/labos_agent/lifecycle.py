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

PHASE_EVIDENCE_FILES = {
    ProjectPhase.BRAINSTORM: ("docs/IDEA.md",),
    ProjectPhase.DOCUMENTATION: (
        "docs/PRODUCT.md",
        "docs/REQUIREMENTS.md",
        "docs/ARCHITECTURE.md",
        "docs/DECISIONS.md",
        "docs/ROADMAP.md",
        "docs/USER_FLOWS.md",
        "AGENTS.md",
    ),
    ProjectPhase.PLANNING: ("docs/REQUIREMENTS.md",),
    ProjectPhase.VALIDATION: ("docs/REQUIREMENTS.md",),
}

PHASE_EVIDENCE_MARKERS = {
    ProjectPhase.PLANNING: ("Acceptance Criteria",),
    ProjectPhase.VALIDATION: ("Validation Results", "Acceptance Criteria"),
}

ALTERNATIVE_EVIDENCE = {
    "implementation_plan": ("PLAN.md", "docs/PLAN.md"),
    "validation_report": ("VALIDATION.md", "docs/VALIDATION.md"),
}

PLACEHOLDER_MARKERS = (
    "_To be completed",
    "_To be created",
    "_Document the important",
    "_No manual",
)


def phase_evidence(project_root: Path, phase: ProjectPhase) -> tuple[bool, tuple[str, ...]]:
    """Return whether the repository contains the minimum evidence for entering phase."""
    required = PHASE_EVIDENCE_FILES.get(phase, ())
    missing: list[str] = []
    alternatives = ALTERNATIVE_EVIDENCE.get(
        "implementation_plan" if phase is ProjectPhase.PLANNING else "validation_report",
        (),
    )
    if alternatives:
        existing_alternative = next(
            (relative for relative in alternatives if (project_root / relative).is_file()),
            None,
        )
        if existing_alternative is None:
            missing.append(" or ".join(alternatives))
        else:
            required = required + (existing_alternative,)
    for relative in required:
        path = project_root / relative
        if not path.is_file():
            missing.append(relative)
            continue
        try:
            content = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            missing.append(relative)
            continue
        if not content or any(marker in content for marker in PLACEHOLDER_MARKERS):
            missing.append(relative)
            continue
        for marker in PHASE_EVIDENCE_MARKERS.get(phase, ()):
            if marker not in content:
                missing.append(f"{relative} (missing '{marker}')")
    return not missing, tuple(missing)


def can_advance(project_root: Path, current: ProjectPhase, target: ProjectPhase) -> tuple[bool, tuple[str, ...]]:
    expected = next_phase(current)
    if target is not expected:
        return False, ("invalid sequential lifecycle transition",)
    if target is ProjectPhase.PLANNING:
        return phase_evidence(project_root, ProjectPhase.DOCUMENTATION)
    if target is ProjectPhase.DEVELOPMENT:
        return phase_evidence(project_root, ProjectPhase.PLANNING)
    if target is ProjectPhase.MAINTENANCE:
        return phase_evidence(project_root, ProjectPhase.VALIDATION)
    return True, ()


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
        if not isinstance(raw, dict) or "phase" not in raw:
            raise ValueError("lifecycle state must contain a phase")
        phase = normalize_phase(raw.get("phase"))
        approved_raw = raw.get("approved", False)
        if not isinstance(approved_raw, bool):
            raise ValueError("lifecycle state approved must be a boolean")
        approved_at = raw.get("approved_at")
        if approved_at is not None and not isinstance(approved_at, str):
            raise ValueError("lifecycle state approved_at must be a string or null")
        return LifecycleState(phase=phase, approved=approved_raw, approved_at=approved_at)
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
        ProjectPhase.DOCUMENTATION: "Turn the agreed idea into durable project documentation. Complete README.md, AGENTS.md, docs/PRODUCT.md, docs/REQUIREMENTS.md, docs/ARCHITECTURE.md, docs/USER_FLOWS.md, docs/DECISIONS.md, and docs/ROADMAP.md as appropriate. Remove template placeholders before advancing to PLANNING. Do not begin feature implementation.",
        ProjectPhase.PLANNING: "Create a concrete implementation plan in PLAN.md or docs/PLAN.md from the agreed documentation, including milestones, tasks, dependencies, technical implementation sequence, and an Acceptance Criteria section. Do not implement the planned features yet.",
        ProjectPhase.DEVELOPMENT: "Implement the human-approved plan. Keep documentation synchronized with meaningful architectural or requirement changes. Run tests and use the normal commit/CI workflow.",
        ProjectPhase.VALIDATION: "Validate the implementation against requirements, architecture, and acceptance criteria. Record Validation Results and Acceptance Criteria results in VALIDATION.md or docs/VALIDATION.md. Fix discovered issues and update documentation. Do not expand scope without approval.",
        ProjectPhase.MAINTENANCE: "Continue normal maintenance against the documented project contract, requirements, architecture, and roadmap.",
    }
    return instructions[phase]
