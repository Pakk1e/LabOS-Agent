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
