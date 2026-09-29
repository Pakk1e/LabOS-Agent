"""Project lifecycle and human approval gates for LabOS."""
from __future__ import annotations

from enum import StrEnum


class ProjectPhase(StrEnum):
    IDEA = "IDEA"
    BRAINSTORM = "BRAINSTORM"
    DOCUMENTATION = "DOCUMENTATION"
    PLANNING = "PLANNING"
    DEVELOPMENT = "DEVELOPMENT"
    VALIDATION = "VALIDATION"
    MAINTENANCE = "MAINTENANCE"


APPROVAL_REQUIRED = {
    ProjectPhase.DOCUMENTATION,
    ProjectPhase.DEVELOPMENT,
}


NEXT_PHASE = {
    ProjectPhase.IDEA: ProjectPhase.BRAINSTORM,
    ProjectPhase.BRAINSTORM: ProjectPhase.DOCUMENTATION,
    ProjectPhase.DOCUMENTATION: ProjectPhase.PLANNING,
    ProjectPhase.PLANNING: ProjectPhase.DEVELOPMENT,
    ProjectPhase.DEVELOPMENT: ProjectPhase.VALIDATION,
    ProjectPhase.VALIDATION: ProjectPhase.MAINTENANCE,
}


def normalize_phase(value: str | None) -> ProjectPhase:
    if not value:
        return ProjectPhase.IDEA
    try:
        return ProjectPhase(str(value).upper())
    except ValueError as exc:
        raise ValueError(f"invalid project phase: {value}") from exc


def can_start_supervisor(phase: ProjectPhase, approved: bool) -> bool:
    return phase not in APPROVAL_REQUIRED or approved


def next_phase(phase: ProjectPhase) -> ProjectPhase | None:
    return NEXT_PHASE.get(phase)


def phase_instruction(phase: ProjectPhase) -> str:
    instructions = {
        ProjectPhase.IDEA: "Do not develop yet. Clarify the initial idea and identify the questions that need human discussion.",
        ProjectPhase.BRAINSTORM: "Explore the idea with the human. Capture product goals, users, workflows, scope, open questions, alternatives, and technical considerations. Do not implement product features.",
        ProjectPhase.DOCUMENTATION: "Turn the agreed idea into durable project documentation. Update docs/PRODUCT.md, docs/REQUIREMENTS.md, docs/ARCHITECTURE.md, docs/DECISIONS.md, docs/ROADMAP.md, and AGENTS.md as appropriate. Do not begin feature implementation.",
        ProjectPhase.PLANNING: "Create a concrete implementation plan from the approved documentation, including milestones, tasks, dependencies, and acceptance criteria. Do not implement the planned features yet.",
        ProjectPhase.DEVELOPMENT: "Implement the approved plan. Keep documentation synchronized with meaningful architectural or requirement changes. Run tests and use the normal commit/CI workflow.",
        ProjectPhase.VALIDATION: "Validate the implementation against requirements and acceptance criteria. Fix discovered issues and update documentation. Do not expand scope without approval.",
        ProjectPhase.MAINTENANCE: "Continue normal maintenance against the documented project requirements and architecture.",
    }
    return instructions[phase]
