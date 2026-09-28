from labos_agent.state_protocol import ChatState, parse_state


def test_parse_final_state():
    assert parse_state("work\n\n(STATE CONTINUE STATE)") == ChatState.CONTINUE
    assert parse_state("done\n(STATE DONE STATE)") == ChatState.DONE


def test_parse_only_final_non_empty_line():
    assert parse_state("(STATE DONE STATE)\nmore text") is None
    assert parse_state("x\n(STATE WAIT_CI STATE)\n\n") == ChatState.WAIT_CI


def test_invalid_state_is_none():
    assert parse_state("work") is None
    assert parse_state("(STATE continue STATE)") is None
    assert parse_state("(STATE RUN_COMMAND STATE)") is None


def test_empty_response_is_none():
    assert parse_state("") is None
