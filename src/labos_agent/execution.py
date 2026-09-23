"""Controlled server execution for autonomous project work."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json, subprocess

@dataclass(frozen=True)
class ExecutionPolicy:
    allowed_roots: tuple[Path, ...]
    max_output_chars: int = 12000
    command_timeout_seconds: float = 300.0

@dataclass(frozen=True)
class ExecutionResult:
    action: str
    success: bool
    exit_code: int | None
    stdout: str
    stderr: str

_BLOCKED_COMMANDS = {"git push","git commit","git reset","git checkout","git merge","git rebase","rm","shutdown","reboot","poweroff","mkfs","mount","umount","systemctl"}

def _rooted_path(root: Path, requested: str, policy: ExecutionPolicy) -> Path:
    candidate = (root / requested).resolve()
    if not any(candidate == allowed or allowed in candidate.parents for allowed in policy.allowed_roots):
        raise PermissionError(f"path is outside configured execution roots: {requested}")
    return candidate

def _validate_command(command: list[str]) -> None:
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
        raise ValueError("run_command requires a non-empty argv list")
    if " ".join(command).casefold() in _BLOCKED_COMMANDS or command[0].casefold() in _BLOCKED_COMMANDS:
        raise PermissionError("command is reserved for the LabOS controller or human approval")

def execute_request(root: Path, request: dict, policy: ExecutionPolicy) -> ExecutionResult:
    action = request.get("action")
    if action == "read_file":
        path = _rooted_path(root, str(request["path"]), policy)
        return ExecutionResult(action, True, 0, path.read_text(encoding="utf-8", errors="replace")[:policy.max_output_chars], "")
    if action == "write_file":
        path = _rooted_path(root, str(request["path"]), policy)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(request["content"]), encoding="utf-8")
        return ExecutionResult(action, True, 0, f"wrote {path}", "")
    if action == "run_command":
        command = request.get("command")
        _validate_command(command)
        cwd = _rooted_path(root, str(request.get("cwd", ".")), policy)
        completed = subprocess.run(command, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, timeout=policy.command_timeout_seconds, check=False)
        return ExecutionResult(action, completed.returncode == 0, completed.returncode,
                               completed.stdout[-policy.max_output_chars:], completed.stderr[-policy.max_output_chars:])
    raise ValueError(f"unsupported execution action: {action}")

def parse_execution_requests(response: str) -> list[dict]:
    requests = []
    marker = chr(96) * 3 + "labos-exec"
    decoder = json.JSONDecoder()

    # Standard fenced execution blocks.
    search_from = 0
    while True:
        marker_start = response.find(marker, search_from)
        if marker_start == -1:
            break

        payload_start = marker_start + len(marker)
        closing = response.find(chr(96) * 3, payload_start)

        if closing == -1:
            break

        payload = response[payload_start:closing]
        value = json.loads(payload.strip())

        if not isinstance(value, dict):
            raise ValueError("labos-exec payload must be an object")

        requests.append((marker_start, value))
        search_from = closing + 3

    # Compact form emitted by some ChatGPT responses:
    # labos-exec{"action":"read_file",...}
    #
    # Only recognize the marker when immediately followed by '{'.
    search_from = 0
    inline_marker = "labos-exec"

    while True:
        marker_start = response.find(inline_marker, search_from)
        if marker_start == -1:
            break

        payload_start = marker_start + len(inline_marker)

        if payload_start < len(response) and response[payload_start] == "{":
            value, consumed = decoder.raw_decode(response[payload_start:])

            if not isinstance(value, dict):
                raise ValueError("labos-exec payload must be an object")

            # Ignore a marker that is actually the fenced form.
            if marker_start == 0 or response[marker_start - 3:marker_start] != chr(96) * 3:
                requests.append((marker_start, value))

            search_from = payload_start + consumed
        else:
            search_from = payload_start

    requests.sort(key=lambda item: item[0])
    return [value for _, value in requests]

def format_execution_results(results: list[ExecutionResult]) -> str:
    lines=["LabOS server execution results:"]
    for i,result in enumerate(results,1):
        lines.append(f"[{i}] action={result.action} success={result.success} exit_code={result.exit_code}")
        if result.stdout: lines.extend(["stdout:",result.stdout])
        if result.stderr: lines.extend(["stderr:",result.stderr])
    return "\n".join(lines)
