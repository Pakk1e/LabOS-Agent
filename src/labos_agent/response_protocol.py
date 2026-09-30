"""Structured ChatGPT response protocol for the LabOS supervisor.

The human-readable response remains useful to an operator, but the LABOS_STATE
block is the machine-readable contract used by the supervisor state machine.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import re

from .state_protocol import ChatState, parse_state

_BLOCK_RE = re.compile(r"<LABOS_STATE>\s*(.*?)\s*</LABOS_STATE>", re.DOTALL)
_FIELD_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=>\s*(.*?)\s*$")
_SHA_RE = re.compile(r"^(?:[0-9a-f]{7,40}|UNKNOWN)$", re.IGNORECASE)

_ALLOWED = {
    "STATE": {"CONTINUE", "WAIT_CI", "FIX_CI", "DONE"},
    "TASK_STATUS": {"IN_PROGRESS", "COMPLETE", "BLOCKED"},
    "COMMIT_STATUS": {"NONE", "CREATED", "PUSHED"},
    "REPOSITORY_CHANGED": {"YES", "NO"},
    "LOCAL_TESTS": {"NOT_RUN", "PASSED", "FAILED"},
    "CI_STATUS": {"NONE", "QUEUED", "IN_PROGRESS", "PASSED", "FAILED", "CANCELLED"},
    "NEXT_ACTION": {"CONTINUE_WORK", "WAIT_FOR_CI", "FIX_CI", "FINISH"},
}

REQUIRED_FIELDS = (
    "STATE", "TASK_STATUS", "CURRENT_COMMIT", "COMMIT_STATUS",
    "REPOSITORY_CHANGED", "LOCAL_TESTS", "CI_RUN", "CI_RUN_ID", "CI_WORKFLOW", "CI_STATUS", "NEXT_ACTION",
)

@dataclass(frozen=True)
class LabOSResponse:
    state: ChatState | None
    task_status: str | None = None
    current_commit: str | None = None
    commit_status: str | None = None
    repository_changed: bool | None = None
    local_tests: str | None = None
    ci_run: int | None = None
    ci_run_id: int | None = None
    ci_workflow: str | None = None
    ci_status: str | None = None
    next_action: str | None = None
    structured: bool = False
    valid: bool = False
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)

def _bool(value: str | None) -> bool | None:
    if value == "YES":
        return True
    if value == "NO":
        return False
    return None

def parse_labos_response(response: str) -> LabOSResponse:
    """Parse the latest structured LABOS_STATE block."""
    blocks = list(_BLOCK_RE.finditer(response))
    if not blocks:
        state = parse_state(response)
        return LabOSResponse(state=state, valid=state is not None)

    fields: dict[str, str] = {}
    errors: list[str] = []
    for line in blocks[-1].group(1).splitlines():
        line = line.strip()
        if not line:
            continue
        match = _FIELD_RE.fullmatch(line)
        if not match:
            errors.append(f"invalid state line: {line}")
            continue
        key, value = match.groups()
        if key in fields:
            errors.append(f"duplicate field: {key}")
        fields[key] = value

    for key in REQUIRED_FIELDS:
        if key not in fields:
            errors.append(f"missing field: {key}")
    for key, allowed in _ALLOWED.items():
        value = fields.get(key)
        if value is not None and value not in allowed:
            errors.append(f"invalid {key}: {value}")

    commit = fields.get("CURRENT_COMMIT")
    if commit is not None and not _SHA_RE.fullmatch(commit):
        errors.append(f"invalid CURRENT_COMMIT: {commit}")

    ci_run: int | None = None
    ci_run_id: int | None = None
    raw_ci_id = fields.get("CI_RUN_ID")
    if raw_ci_id not in (None, "NONE"):
        try:
            ci_run_id = int(raw_ci_id)
            if ci_run_id < 1: raise ValueError
        except ValueError:
            errors.append(f"invalid CI_RUN_ID: {raw_ci_id}")
    raw_ci = fields.get("CI_RUN")
    if raw_ci not in (None, "NONE"):
        try:
            ci_run = int(raw_ci)
            if ci_run < 1:
                raise ValueError
        except ValueError:
            errors.append(f"invalid CI_RUN: {raw_ci}")

    state_value = fields.get("STATE")
    task_status = fields.get("TASK_STATUS")
    next_action = fields.get("NEXT_ACTION")
    ci_status = fields.get("CI_STATUS")
    repository_changed = _bool(fields.get("REPOSITORY_CHANGED"))
    commit_status = fields.get("COMMIT_STATUS")
    if state_value == "DONE":
        if task_status != "COMPLETE": errors.append("DONE requires TASK_STATUS => COMPLETE")
        if next_action != "FINISH": errors.append("DONE requires NEXT_ACTION => FINISH")
        if commit in (None, "UNKNOWN"): errors.append("DONE requires a concrete CURRENT_COMMIT")
        if repository_changed and commit_status != "PUSHED": errors.append("DONE with repository changes requires COMMIT_STATUS => PUSHED")
        if ci_status != "PASSED": errors.append("DONE requires CI_STATUS => PASSED")
        if fields.get("CI_RUN") in (None, "NONE") or fields.get("CI_RUN_ID") in (None, "NONE"): errors.append("DONE requires CI_RUN and CI_RUN_ID")
        if fields.get("CI_WORKFLOW") in (None, "NONE", ""): errors.append("DONE requires CI_WORKFLOW")
    elif state_value == "WAIT_CI":
        if next_action != "WAIT_FOR_CI": errors.append("WAIT_CI requires NEXT_ACTION => WAIT_FOR_CI")
        if fields.get("CI_RUN") in (None, "NONE") or fields.get("CI_RUN_ID") in (None, "NONE"): errors.append("WAIT_CI requires CI_RUN and CI_RUN_ID")
        if ci_status not in {"QUEUED", "IN_PROGRESS", "PASSED"}: errors.append("WAIT_CI requires CI_STATUS => QUEUED, IN_PROGRESS, or PASSED")
    elif state_value == "FIX_CI":
        if next_action != "FIX_CI": errors.append("FIX_CI requires NEXT_ACTION => FIX_CI")
        if ci_status not in {"FAILED", "CANCELLED"}: errors.append("FIX_CI requires CI_STATUS => FAILED or CANCELLED")
    elif state_value == "CONTINUE":
        if task_status != "IN_PROGRESS": errors.append("CONTINUE requires TASK_STATUS => IN_PROGRESS")
        if next_action != "CONTINUE_WORK": errors.append("CONTINUE requires NEXT_ACTION => CONTINUE_WORK")
    try:
        state = ChatState(state_value) if state_value else None
    except ValueError:
        state = None

    return LabOSResponse(
        state=state,
        task_status=fields.get("TASK_STATUS"),
        current_commit=commit,
        commit_status=fields.get("COMMIT_STATUS"),
        repository_changed=_bool(fields.get("REPOSITORY_CHANGED")),
        local_tests=fields.get("LOCAL_TESTS"),
        ci_run=ci_run,
        ci_run_id=ci_run_id,
        ci_workflow=fields.get("CI_WORKFLOW"),
        ci_status=fields.get("CI_STATUS"),
        next_action=fields.get("NEXT_ACTION"),
        structured=True,
        valid=not errors and state is not None,
        errors=tuple(errors),
    )

STATE_BLOCK_INSTRUCTION = """Every response must include exactly one machine-readable LABOS_STATE block and the legacy state marker.

Use this exact format:
<LABOS_STATE>
STATE => CONTINUE|WAIT_CI|FIX_CI|DONE
TASK_STATUS => IN_PROGRESS|COMPLETE|BLOCKED
CURRENT_COMMIT => <full commit SHA, or UNKNOWN before a commit exists>
COMMIT_STATUS => NONE|CREATED|PUSHED
REPOSITORY_CHANGED => YES|NO
LOCAL_TESTS => NOT_RUN|PASSED|FAILED
CI_RUN => <GitHub Actions workflow run number, or NONE>
CI_RUN_ID => <GitHub Actions unique run ID (databaseId), or NONE>
CI_WORKFLOW => <GitHub Actions workflow name, or NONE>
CI_STATUS => NONE|QUEUED|IN_PROGRESS|PASSED|FAILED|CANCELLED
NEXT_ACTION => CONTINUE_WORK|WAIT_FOR_CI|FIX_CI|FINISH
</LABOS_STATE>

Then finish with the legacy marker on the final non-empty line:
(STATE CONTINUE STATE)
(STATE WAIT_CI STATE)
(STATE FIX_CI STATE)
(STATE DONE STATE)

Report only facts you actually observed. LabOS independently verifies GitHub state."""
