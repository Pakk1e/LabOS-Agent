from pathlib import Path

from labos_agent.execution import ExecutionPolicy
from labos_agent.runtime import ExecutionRuntime
from labos_agent.trajectory import append_event, trajectory_path
from labos_agent.verification import RepositoryVerifier


def test_trajectory_events_are_append_only_jsonl(tmp_path: Path):
    path = trajectory_path(tmp_path)
    append_event(path, run_id="run-1", project="demo", iteration=1,
                 phase="reasoning", event="reasoning.requested", prompt_chars=12)
    append_event(path, run_id="run-1", project="demo", iteration=1,
                 phase="execution", event="execution.completed", success=True)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert '"event": "reasoning.requested"' in lines[0]
    assert '"event": "execution.completed"' in lines[1]
    assert '"run_id": "run-1"' in lines[1]


def test_execution_runtime_returns_structured_observation(tmp_path: Path):
    runtime = ExecutionRuntime(tmp_path, ExecutionPolicy((tmp_path.resolve(),)))
    observation = runtime.apply([
        {"action": "write_file", "path": "a.txt", "content": "ok"},
        {"action": "read_file", "path": "a.txt"},
    ])

    assert observation.requested_count == 2
    assert observation.success
    assert observation.successful_count == 2
    assert observation.failed_count == 0
    assert observation.results[1].stdout == "ok"


def test_repository_verifier_reports_meaningful_change(tmp_path: Path):
    import subprocess
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "LabOS Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "labos@example.invalid"], check=True)
    (tmp_path / "src").mkdir()\n    (tmp_path / "src" / "source.py").write_text("print('base')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "src/source.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "base"], check=True, capture_output=True)

    verifier = RepositoryVerifier(tmp_path)
    before = verifier.capture()
    (tmp_path / "src" / "source.py").write_text("print('changed')\n", encoding="utf-8")
    result = verifier.compare(before)

    assert not result.clean_relative_to_baseline
    assert result.meaningful_change


def test_repository_verifier_exposes_changed_paths(tmp_path: Path):
    import subprocess
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "LabOS Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "labos@example.invalid"], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "source.py").write_text("print('base')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "src/source.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "base"], check=True, capture_output=True)
    verifier = RepositoryVerifier(tmp_path)
    before = verifier.capture()
    (tmp_path / "src" / "source.py").write_text("print('changed')\n", encoding="utf-8")
    result = verifier.compare(before)
    assert result.changed_paths == ("src/source.py",)
