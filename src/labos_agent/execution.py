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
    denied_path_prefixes: tuple[str, ...] = (".git", ".github", ".env", "browser-profile")

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
    allowed_roots = tuple(path.expanduser().resolve() for path in policy.allowed_roots)
    if not any(candidate == allowed or allowed in candidate.parents for allowed in allowed_roots):
        raise PermissionError(f"path is outside configured execution roots: {requested}")
    relative = candidate.relative_to(root.resolve())
    if any(relative == Path(prefix) or Path(prefix) in relative.parents for prefix in policy.denied_path_prefixes):
        raise PermissionError(f"path is reserved by the controller: {requested}")
    return candidate

_ALLOWED_EXECUTABLES = {"git", "ls", "pwd", "printf", "echo", "grep", "sed", "awk", "head", "tail", "wc", "sort", "uniq", "pytest"}
_BLOCKED_GIT_SUBCOMMANDS = {"push", "commit", "reset", "checkout", "merge", "rebase", "switch", "restore", "clean", "stash"}
_BLOCKED_EXECUTABLES = {"rm", "shutdown", "reboot", "poweroff", "mkfs", "mount", "umount", "systemctl"}

def _validate_command(command: list[str]) -> None:
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
        raise ValueError("run_command requires a non-empty argv list")
    executable = Path(command[0]).name.casefold()
    if executable != "git":
        raise PermissionError("autonomous run_command only permits read-only git inspection")
    if len(command) < 2 or command[1] in {"-C", "-c", "--git-dir", "--work-tree", "--no-index"}:
        raise PermissionError("git global options are not allowed in autonomous execution")
    subcommand = command[1].casefold()
    if subcommand not in {"status", "diff", "log", "show", "ls-files"}:
        raise PermissionError(f"git subcommand is not allowed in autonomous execution: {subcommand}")
    forbidden = {"-C", "-c", "--git-dir", "--work-tree", "--no-index", "--exec-path", "--upload-pack", "--receive-pack"}
    if any(token in forbidden or token.startswith("--config") for token in command[2:]):
        raise PermissionError("unsafe git options are not allowed in autonomous execution")

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
        try:
            completed = subprocess.run(command, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                                      text=True, timeout=policy.command_timeout_seconds, check=False)
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(action, False, None, str(exc.stdout or "")[-policy.max_output_chars:], f"command timed out after {policy.command_timeout_seconds}s")
        except OSError as exc:
            return ExecutionResult(action, False, None, "", f"execution failed: {exc}")
        return ExecutionResult(action, completed.returncode == 0, completed.returncode,
                               completed.stdout[-policy.max_output_chars:], completed.stderr[-policy.max_output_chars:])
    raise ValueError(f"unsupported execution action: {action}")

def parse_execution_requests(response: str) -> list[dict]:
    requests = []
    marker = chr(96) * 3 + "labos-exec"
    decoder = json.JSONDecoder()

    # Standard fenced execution blocks. Decode JSON directly so code fences
    # inside a JSON string do not terminate the request prematurely.
    search_from = 0
    while True:
        marker_start = response.find(marker, search_from)
        if marker_start == -1:
            break
        payload_start = marker_start + len(marker)
        try:
            value, consumed = decoder.raw_decode(response[payload_start:].lstrip())
        except json.JSONDecodeError:
            search_from = payload_start
            continue
        if not isinstance(value, dict):
            raise ValueError("labos-exec payload must be an object")
        requests.append((marker_start, value))
        search_from = payload_start + consumed

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
