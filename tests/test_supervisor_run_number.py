from labos_agent import supervisor


def test_next_run_number_survives_existing_run_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runs = tmp_path / "state" / "weather" / "runs"
    runs.mkdir(parents=True)
    (runs / "000007.json").write_text("{}", encoding="utf-8")
    (runs / "000008.events.jsonl").write_text("", encoding="utf-8")
    supervisor._RUN_NUMBER = 0
    assert supervisor._next_run_number("weather") == 9
    assert supervisor._next_run_number("weather") == 10
