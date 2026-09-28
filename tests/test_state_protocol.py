from labos_agent.state_protocol import ChatState, parse_state


def test_parse_final_state():
    assert parse_state("work\n\n(STATE CONTINUE STATE)") == ChatState.CONTINUE
    assert parse_state("done\n(STATE DONE STATE)") == ChatState.DONE


def test_parse_state_with_trailing_browser_artifacts():
    assert parse_state("work\n(STATE WAIT_CI STATE)\nCopy") == ChatState.WAIT_CI
    assert parse_state("done\n(STATE DONE STATE)\n") == ChatState.DONE


def test_parse_last_valid_marker():
    response = "(STATE CONTINUE STATE)\nmore work\n(STATE WAIT_CI STATE)\nrendered UI text"
    assert parse_state(response) == ChatState.WAIT_CI


def test_invalid_state_is_none():
    assert parse_state("work") is None
    assert parse_state("(STATE continue STATE)") is None
    assert parse_state("(STATE RUN_COMMAND STATE)") is None


def test_empty_response_is_none():
    assert parse_state("") is None
