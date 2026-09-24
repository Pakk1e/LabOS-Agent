from pathlib import Path
from labos_agent.project import inspect_project,build_continuation_prompt

def test_project_snapshot_reads_state(tmp_path:Path):
    (tmp_path/"AGENTS.md").write_text("rules",encoding="utf-8")
    import subprocess
    subprocess.run(["git","init"],cwd=tmp_path,check=True,capture_output=True)
    snap=inspect_project(tmp_path,"Pakk1e/example",("AGENTS.md","PLAN.md"))
    assert snap.files["AGENTS.md"]=="rules"
    prompt=build_continuation_prompt(snap,"Continue")
    assert "Pakk1e/example" in prompt
    assert "rules" in prompt
    assert "may not have access to that controller-side path" in prompt
    assert "Do not treat inability to access the local project path as a blocker" in prompt
    assert "configured repository as the authoritative source" in prompt
    assert "do not rely solely on a commit-workflow endpoint" in prompt
    assert "match the target commit SHA" in prompt


def test_continuation_prompt_marks_ci_as_previous_iteration():
    snapshot = inspect_project(Path("."), "Pakk1e/example", ())
    prompt = build_continuation_prompt(snapshot, "Continue", "stage=test success=True")
    assert "Previous iteration LocalCI result:" in prompt
    assert "previous controller iteration" in prompt
    assert "controller will commit/push them" in prompt


def test_continuation_prompt_without_ci_feedback_has_no_ci_handoff():
    snapshot = inspect_project(Path("."), "Pakk1e/example", ())
    prompt = build_continuation_prompt(snapshot, "Continue")
    assert "Previous iteration LocalCI result:" not in prompt
    assert "LocalCI handoff:" not in prompt


def test_continuation_prompt_corrects_no_progress():
    snapshot = inspect_project(Path("."), "Pakk1e/example", ())
    prompt = build_continuation_prompt(
        snapshot,
        "Continue",
        progress_feedback="assistant response completed but repository working tree did not change",
    )
    assert "Previous iteration progress result:" in prompt
    assert "Do not repeat a baseline test-only iteration" in prompt
    assert "implement the smallest concrete source/documentation change now" in prompt
    assert "A passing LocalCI run without a corresponding repository change is not progress." in prompt


def test_continuation_prompt_requires_execution_or_done_marker(tmp_path, monkeypatch):
    from labos_agent.project import inspect_project, build_continuation_prompt
    monkeypatch.setattr("labos_agent.project.git_status", lambda root: "")
    snapshot = inspect_project(tmp_path, "Pakk1e/example", ())
    prompt = build_continuation_prompt(snapshot, "Continue", execution_enabled=True)
    assert "Every non-final implementation response MUST contain at least one executable" in prompt
    assert "LABOS_DONE" in prompt
    assert "Do not finish with prose such as 'I will make...'" in prompt
    assert "LocalCI is run automatically by LabOS" in prompt
