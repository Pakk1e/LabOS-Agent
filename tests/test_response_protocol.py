from pathlib import Path

from labos_agent.response_protocol import parse_labos_response


VALID = """Implemented the change.

<LABOS_STATE>
STATE => WAIT_CI
TASK_STATUS => IN_PROGRESS
CURRENT_COMMIT => 25124b5d3393263965c2d07adffc4f3cde6ce3b6
COMMIT_STATUS => PUSHED
REPOSITORY_CHANGED => YES
LOCAL_TESTS => PASSED
CI_RUN => 166
CI_STATUS => IN_PROGRESS
NEXT_ACTION => WAIT_FOR_CI
</LABOS_STATE>
(STATE WAIT_CI STATE)
"""


def test_parse_structured_response():
    result = parse_labos_response(VALID)
    assert result.valid is True
    assert result.structured is True
    assert result.state.value == "WAIT_CI"
    assert result.current_commit.startswith("25124b5")
    assert result.repository_changed is True
    assert result.local_tests == "PASSED"
    assert result.ci_run == 166
    assert result.ci_status == "IN_PROGRESS"
    assert result.next_action == "WAIT_FOR_CI"


def test_structured_response_rejects_missing_fields():
    response = """<LABOS_STATE>
STATE => DONE
</LABOS_STATE>
(STATE DONE STATE)
"""
    result = parse_labos_response(response)
    assert result.structured is True
    assert result.valid is False
    assert "missing field: CURRENT_COMMIT" in result.errors


def test_legacy_response_remains_supported():
    result = parse_labos_response("work\n(STATE CONTINUE STATE)\n")
    assert result.valid is True
    assert result.structured is False
    assert result.state.value == "CONTINUE"
