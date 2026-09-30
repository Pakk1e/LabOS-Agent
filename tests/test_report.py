from labos_agent.report import build_morning_report

def test_morning_report_summarizes_state_and_events(tmp_path):
    state = tmp_path / "state" / "weather"
    runs = state / "runs"
    runs.mkdir(parents=True)
    (state / "supervisor_state.json").write_text('{"state":"WAITING_CI","iteration":4,"last_observed_commit":"abc123","last_observed_branch":"agent/4","last_observed_ci_status":"completed","last_observed_ci_conclusion":"success","next_action":"CONTINUE","reason":"waiting for CI"}', encoding="utf-8")
    (runs / "000004.events.jsonl").write_text('{"event":"run.start","iteration":4}\n{"event":"iteration.complete","iteration":4,"success":true,"state":"WAIT_CI"}\n', encoding="utf-8")
    report = build_morning_report(state, "weather")
    assert "WAITING_CI" in report
    assert "abc123" in report
    assert "iteration.complete" in report

def test_morning_report_handles_missing_state(tmp_path):
    report = build_morning_report(tmp_path / "missing", "demo")
    assert "Supervisor: **UNKNOWN**" in report
    assert "No persisted run events." in report