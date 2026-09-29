import pytest

from labos_agent.lifecycle import (
    ProjectPhase,
    can_start_supervisor,
    next_phase,
    normalize_phase,
    phase_instruction,
)


def test_guided_lifecycle_progression():
    phase = ProjectPhase.IDEA
    phases = []
    while phase is not None:
        phases.append(phase)
        phase = next_phase(phase)
    assert phases == [
        ProjectPhase.IDEA,
        ProjectPhase.BRAINSTORM,
        ProjectPhase.DOCUMENTATION,
        ProjectPhase.PLANNING,
        ProjectPhase.DEVELOPMENT,
        ProjectPhase.VALIDATION,
        ProjectPhase.MAINTENANCE,
    ]


def test_development_requires_human_approval():
    assert can_start_supervisor(ProjectPhase.PLANNING, False)
    assert not can_start_supervisor(ProjectPhase.DEVELOPMENT, False)
    assert can_start_supervisor(ProjectPhase.DEVELOPMENT, True)


def test_phase_normalization_and_invalid_value():
    assert normalize_phase("brainstorm") is ProjectPhase.BRAINSTORM
    with pytest.raises(ValueError):
        normalize_phase("coding")


def test_phase_instructions_are_specific():
    assert "Do not implement product features" in phase_instruction(ProjectPhase.BRAINSTORM)
    assert "documentation" in phase_instruction(ProjectPhase.DOCUMENTATION).lower()
    assert "approved plan" in phase_instruction(ProjectPhase.DEVELOPMENT).lower()
