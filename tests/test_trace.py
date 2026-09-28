from labos_agent.trace import trace


def test_trace_is_human_friendly(capsys, monkeypatch):
    monkeypatch.setenv("LABOS_TRACE", "1")

    trace(
        "ci.complete",
        project="weather",
        stage="test",
        success=True,
        result_tail="large internal output that should not be printed",
    )

    output = capsys.readouterr().err
    assert "OK" in output
    assert "[weather]" in output
    assert "Local CI finished" in output
    assert "stage=test" in output
    assert "large internal output" not in output
    assert output.startswith("20")
