from labos_agent.browser.detection import _has_max_length_notice


def test_max_length_notice_requires_both_phrases():
    assert _has_max_length_notice(
        "You've reached the maximum length for this conversation, but you can keep talking by starting a new chat."
    )


def test_max_length_notice_does_not_match_partial_text():
    assert not _has_max_length_notice("You've reached the maximum length for this conversation.")
    assert not _has_max_length_notice("You can keep talking by starting a new chat.")
    assert not _has_max_length_notice("Start a new chat")


def test_max_length_notice_is_case_and_whitespace_tolerant():
    assert _has_max_length_notice(
        "  YOU'VE REACHED THE MAXIMUM LENGTH FOR THIS CONVERSATION,\\n"
        "but   you can keep talking by starting a new chat.  "
    )
