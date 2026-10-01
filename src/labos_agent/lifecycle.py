"""Project lifecycle and autonomous progression rules for LabOS."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
import re
import shutil


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
        # Approval is retained only for backwards-compatible persisted state.
        # Lifecycle progression is autonomous; no phase requires a human gate.
        pass

    @property
    def approval_required(self) -> bool:
        return False


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
    ProjectPhase.BRAINSTORM: ("LabOS brainstorming notes",),
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


def phase_instruction(phase: ProjectPhase) -> str:
    """Return a direct, phase-specific work contract for the ChatGPT agent."""
    instructions = {
        ProjectPhase.IDEA: (
            "You are in the IDEA phase. Clarify the initial idea and establish the project "
            "starting point. When the idea is clear and recorded, report DONE."
        ),
        ProjectPhase.BRAINSTORM: (
            "You are in the BRAINSTORMING phase. Explore the idea thoroughly: define the "
            "problem, users, useful capabilities, alternatives, risks, open questions, and "
            "a practical direction. Work independently for this turn; do not stop after a "
            "small suggestion. When brainstorming is complete, create or update docs/IDEA.md "
            "with the agreed brainstorming notes and the marker 'LabOS brainstorming notes', "
            "then report DONE. Do not start the documentation phase."
        ),
        ProjectPhase.DOCUMENTATION: (
            "You are in the DOCUMENTATION phase. Turn the agreed idea into complete, durable "
            "project documentation. Finish the required product, requirements, architecture, "
            "decisions, roadmap, user-flow, and AGENTS documentation. Remove placeholders. "
            "Do not begin feature implementation. When the documentation set is complete, "
            "report DONE."
        ),
        ProjectPhase.PLANNING: (
            "You are in the PLANNING phase. Convert the requirements into an executable "
            "implementation plan with explicit AC-* acceptance criteria, sequencing, scope, "
            "technical decisions, and validation approach. Finish PLAN.md (or docs/PLAN.md). "
            "When the plan is complete, report DONE."
        ),
        ProjectPhase.DEVELOPMENT: (
            "You are in the DEVELOPMENT phase. Implement the planned product, not just a "
            "small incremental change. Work independently and use the available repository "
            "tools. Run relevant tests, commit and push completed work, and only report DONE "
            "when the implementation is genuinely complete and its required CI validation "
            "has passed."
        ),
        ProjectPhase.VALIDATION: (
            "You are in the VALIDATION phase. Validate the implementation against every "
            "acceptance criterion. Run the relevant checks, record concrete results in "
            "VALIDATION.md (or docs/VALIDATION.md), and mark each AC as PASS only when it "
            "is actually verified. When validation is complete, report DONE."
        ),
        ProjectPhase.MAINTENANCE: (
            "You are in the MAINTENANCE phase. Review the finished project and acceptance "
            "evidence, fix any remaining issues, and leave the repository in a validated "
            "maintainable state. When the lifecycle is genuinely complete, report DONE."
        ),
    }
    return instructions[phase]


def phase_evidence(project_root: Path, phase: ProjectPhase) -> tuple[bool, tuple[str, ...]]:
    """Return whether the repository contains the minimum evidence for entering phase."""
    required = PHASE_EVIDENCE_FILES.get(phase, ())
    missing: list[str] = []
    evidence_key = (
        "implementation_plan"
        if phase is ProjectPhase.PLANNING
        else "validation_report"
        if phase is ProjectPhase.VALIDATION
        else None
    )
    alternatives = ALTERNATIVE_EVIDENCE.get(evidence_key, ()) if evidence_key else ()
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
        if relative in alternatives or phase is ProjectPhase.BRAINSTORM:
            for marker in PHASE_EVIDENCE_MARKERS.get(phase, ()):
                if marker not in content:
                    missing.append(f"{relative} (missing '{marker}')")
    return not missing, tuple(missing)



def _find_evidence_file(project_root: Path, key: str) -> Path | None:
    return next(
        (project_root / relative for relative in ALTERNATIVE_EVIDENCE[key] if (project_root / relative).is_file()),
        None,
    )


def validate_acceptance_criteria(project_root: Path) -> tuple[bool, tuple[str, ...]]:
    """Verify every plan acceptance criterion has a PASS result in validation."""
    plan = _find_evidence_file(project_root, "implementation_plan")
    validation = _find_evidence_file(project_root, "validation_report")
    if plan is None:
        return False, ("implementation plan is missing",)
    if validation is None:
        return False, ("validation report is missing",)
    try:
        plan_text = plan.read_text(encoding="utf-8")
        validation_text = validation.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return False, (f"cannot read acceptance evidence: {exc}",)
    criteria = []
    in_section = False
    for line in plan_text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## ") and "acceptance criteria" in stripped.lower():
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped.startswith("- "):
            criterion = stripped[2:].strip()
            match = re.match(r"(AC-[A-Za-z0-9._-]+)\b", criterion)
            if not match:
                criteria.append("")
            else:
                criteria.append(match.group(1))
    if not criteria:
        return False, ("implementation plan contains no acceptance criteria",)
    if any(not criterion for criterion in criteria):
        return False, ("every acceptance criterion must start with an AC-* identifier",)
    validation_lines = validation_text.splitlines()
    missing = []
    for criterion_id in criteria:
        matching = [
            line
            for line in validation_lines
            if re.search(rf"\b{re.escape(criterion_id)}\b", line, re.IGNORECASE)
        ]
        if not matching:
            missing.append(f"{criterion_id} is missing from validation")
            continue
        passed = False
        for line in matching:
            status_text = re.sub(
                rf"^.*?\b{re.escape(criterion_id)}\b",
                "",
                line,
                count=1,
                flags=re.IGNORECASE,
            )
            if re.search(r"\bNOT\s+PASS\b", status_text, re.IGNORECASE):
                continue
            if re.search(r"\bPASS\b", status_text, re.IGNORECASE):
                passed = True
                break
        if not passed:
            missing.append(f"{criterion_id} is not marked PASS")
    return not missing, tuple(missing)

def can_advance(project_root: Path, current: ProjectPhase, target: ProjectPhase) -> tuple[bool, tuple[str, ...]]:
    expected = next_phase(current)
    if target is not expected:
        return False, ("invalid sequential lifecycle transition",)
    if target is ProjectPhase.DOCUMENTATION and current is ProjectPhase.BRAINSTORM:
        return phase_evidence(project_root, ProjectPhase.BRAINSTORM)
    if target is ProjectPhase.PLANNING:
        return phase_evidence(project_root, ProjectPhase.DOCUMENTATION)
    if target is ProjectPhase.DEVELOPMENT:
        return phase_evidence(project_root, ProjectPhase.PLANNING)
    if target is ProjectPhase.MAINTENANCE:
        ok, missing = phase_evidence(project_root, ProjectPhase.VALIDATION)
        if not ok:
            return ok, missing
        return validate_acceptance_criteria(project_root)
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
    """All configured lifecycle phases may run autonomously.

    The approved argument is retained for compatibility with existing
    configuration/state files, but it is no longer a supervisor gate.
    """
    return True


def next_phase(phase: ProjectPhase) -> ProjectPhase | None:
    return NEXT_PHASE.get(phase)


def lifecycle_state_path(state_root: Path, project: str) -> Path:
    if not project or project in {".", ".."} or "/" in project or "\\" in project:
        raise ValueError("invalid lifecycle project name")
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
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            try:
                raw = json.loads(backup.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or "phase" not in raw:
                    raise ValueError("invalid lifecycle backup")
                phase = normalize_phase(raw.get("phase"))
                approved_raw = raw.get("approved", False)
                if not isinstance(approved_raw, bool):
                    raise ValueError("invalid lifecycle backup approval")
                approved_at = raw.get("approved_at")
                if approved_at is not None and not isinstance(approved_at, str):
                    raise ValueError("invalid lifecycle backup approval timestamp")
                return LifecycleState(phase=phase, approved=approved_raw, approved_at=approved_at)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
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
    backup = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        shutil.copy2(path, backup)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path



