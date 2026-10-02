from labos_agent.lifecycle import ProjectPhase, phase_instruction


def test_brainstorm_contract_requires_done_without_waiting_for_authorization():
    instruction = phase_instruction(ProjectPhase.BRAINSTORM)

    assert "then report DONE" in instruction
    assert "Do not start the documentation phase" in instruction
    assert "Reporting DONE is the trigger for LabOS to advance automatically" in instruction
    assert "do not wait for human authorization to report DONE" in instruction
