"""Project state loading and validation."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import subprocess

@dataclass(frozen=True)
class ProjectSnapshot:
    root: Path
    repository: str
    files: dict[str,str]
    git_status: str

def inspect_project(root: Path, repository: str, state_files: tuple[str,...]) -> ProjectSnapshot:
    if not root.exists() or not root.is_dir():
        raise RuntimeError(f"project root does not exist: {root}")
    files={}
    for name in state_files:
        path=root/name
        if path.is_file():
            files[name]=path.read_text(encoding="utf-8",errors="replace")
    try:
        result=subprocess.run(["git","-C",str(root),"status","--short"],check=True,capture_output=True,text=True,timeout=20)
    except (subprocess.SubprocessError,OSError) as exc:
        raise RuntimeError(f"could not inspect git state: {exc}") from exc
    return ProjectSnapshot(root,repository,files,result.stdout.strip())

def build_continuation_prompt(snapshot: ProjectSnapshot, message: str) -> str:
    parts=[message,"","LabOS-Agent controller context:",
           f"- Repository: {snapshot.repository}",
           f"- Project root: {snapshot.root}",
           "- Persistent project state is authoritative. Continue from the repository, not assumptions."]
    if snapshot.git_status:
        parts += ["","Current git working-tree status:",snapshot.git_status]
    for name,content in snapshot.files.items():
        parts += ["",f"--- {name} ---",content]
    parts += ["","Work autonomously within the project rules. Make the smallest useful next change, test it, and report exactly what changed, what was tested, blockers, and the precise next action."]
    return "\n".join(parts)
