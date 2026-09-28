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

def test_trace_shows_actionable_reason(capsys, monkeypatch):
    monkeypatch.setenv("LABOS_TRACE", "1")

    trace("run.error", project="weather", error="GitHub Actions failed: test=cancelled")

    output = capsys.readouterr().err
    assert "ERR" in output
    assert "Run failed" in output
    assert "reason=GitHub Actions failed: test=cancelled" in output


def test_trace_supports_json_diagnostics(capsys, monkeypatch):
    monkeypatch.setenv("LABOS_TRACE", "1")
    monkeypatch.setenv("LABOS_TRACE_FORMAT", "json")

    trace("ci.complete", project="weather", success=True)

    output = capsys.readouterr().err
    assert output.startswith("[LABOS] ")
    assert '"event": "ci.complete"' in output
    assert '"project": "weather"' in output
