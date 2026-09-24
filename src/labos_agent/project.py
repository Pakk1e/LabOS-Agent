"""Project state loading and validation."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from .git_gate import git_status

MAX_STATE_FILE_CHARS = 24000
MAX_PROMPT_STATE_CHARS = 80000

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
            content=path.read_text(encoding="utf-8",errors="replace")
            if len(content) > MAX_STATE_FILE_CHARS:
                content = (
                    content[:MAX_STATE_FILE_CHARS]
                    + "\n\n[LabOS-Agent: state file truncated for prompt-size safety; "
                    "inspect the repository file directly if more context is required.]"
                )
            files[name]=content
    return ProjectSnapshot(root,repository,files,git_status(root))

def build_continuation_prompt(snapshot: ProjectSnapshot, message: str, ci_feedback: str|None = None, progress_feedback: str|None = None, execution_enabled: bool = False) -> str:
    parts=[message,"","LabOS-Agent controller context:",
           f"- Repository: {snapshot.repository}",f"- Project root: {snapshot.root}",
           "- Persistent project state is authoritative. Continue from the repository, not assumptions."]
    if snapshot.git_status: parts += ["","Current git working-tree status:",snapshot.git_status]
    if ci_feedback: parts += ["","Previous iteration LocalCI result:",ci_feedback]
    if progress_feedback: parts += ["","Previous iteration progress result:",progress_feedback,
        "","Progress correction: the previous iteration produced no repository change. Do not repeat a baseline test-only iteration. Inspect the requested task and implement the smallest concrete source/documentation change now. A passing LocalCI run without a corresponding repository change is not progress."]
    if execution_enabled:
        tick=chr(96)
        parts += ["","Server execution protocol:",
                  f"Source changes must be made through a controlled {tick}{tick}{tick}labos-exec JSON block. The controller is the only component with write access to the server checkout.",
                  f"Every non-final implementation response MUST contain at least one executable {tick}{tick}{tick}labos-exec request. When the implementation is complete, end the response with the exact marker LABOS_DONE.",
                  'Supported actions: {"action":"read_file","path":"..."}, {"action":"write_file","path":"...","content":"..."}, {"action":"run_command","command":["git","status","--short"],"cwd":"..."}.',
                  "The autonomous run_command surface is intentionally read-only Git inspection; do not use it to execute tests, write files, commit, push, or run arbitrary programs.",
                  "Use write_file for source/documentation changes. Do not claim a write happened until the controller returns its real execution result.",
                  "LocalCI is run automatically by LabOS after a real working-tree change. If LocalCI fails, fix the reported failure with write_file and request another execution round.",
                  "Do not finish with prose such as 'I will make...' or 'I am updating...' without an execution request. If there is genuinely nothing left to change, issue a final read/inspection request and then end with LABOS_DONE."]
    state_budget = MAX_PROMPT_STATE_CHARS
    for name,content in snapshot.files.items():
        if state_budget <= 0:
            parts += ["", "[LabOS-Agent: remaining state files omitted for prompt-size safety.]"]
            break
        if len(content) > state_budget:
            content = content[:state_budget] + "\n\n[LabOS-Agent: remaining state content omitted for prompt-size safety.]"
        parts += ["",f"--- {name} ---",content]
        state_budget -= len(content)
    parts += ["","Execution boundary: the LabOS controller inspected the local project filesystem before sending this prompt. The ChatGPT execution environment may not have access to that controller-side path. Do not treat inability to access the local project path as a blocker, and do not claim local commands were run unless your execution environment actually ran them. Use the configured repository as the authoritative source for source-code inspection and changes. Treat the reported local git status as controller-provided context; it may contain deployment-only or machine-local changes that must not be overwritten blindly.",
              "","Work autonomously within the project rules. Make the smallest useful next change using the authoritative repository, test it with the available repository/CI mechanisms, and report exactly what changed, what was tested, blockers, and the precise next action. For CI verification, do not rely solely on a commit-workflow endpoint that may omit push-triggered runs; when available, inspect the repository Actions/workflow runs and match the target commit SHA. A successful push-triggered CI run for that exact SHA is valid evidence even if a narrower commit-status wrapper returns no result.",
              "","Controller Git gate: when a local CI stage is configured, do not commit or push changes yourself. Leave source changes in the working tree. LabOS will commit/push only after the controller has verified that LocalCI passed."]
    if ci_feedback: parts += ["","LocalCI handoff: the result above belongs to the previous controller iteration. If it passed, treat the previously reported working-tree changes as locally validated; the LabOS controller will commit/push them before the next implementation iteration. If it failed, fix that failure and leave the changes uncommitted for another LocalCI run."]
    return "\n".join(parts)
