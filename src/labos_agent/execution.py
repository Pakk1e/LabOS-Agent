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
    denied = {prefix.casefold() for prefix in policy.denied_path_prefixes}
    for part in relative.parts:
        folded = part.casefold()
        if folded in denied or folded.startswith(".env"):
            raise PermissionError(f"path is reserved by the controller: {requested}")
    return candidate

_ALLOWED_GIT_SUBCOMMANDS = {"status", "diff", "log", "show", "ls-files"}
_BLOCKED_GIT_SUBCOMMANDS = {"push", "commit", "reset", "checkout", "merge", "rebase", "switch", "restore", "clean", "stash"}

# This is deliberately an allowlist, not a denylist.  These commands are the
# only subprocess surface exposed to the autonomous agent, so every accepted
# option must be known to be observational only.  In particular, git log/diff/
# show have options such as --output=<file> that turn their stdout into a
# filesystem write; unknown options are rejected before git starts.
_GIT_SAFE_OPTIONS = {
    "status": {
        "--short", "--porcelain", "--porcelain=v1", "-z", "-uall",
        "--untracked-files=all", "--branch",
    },
    "ls-files": {
        "-o", "--others", "--exclude-standard", "-z", "--stage", "--cached",
        "--deleted", "--modified", "--ignored",
    },
    "diff": {
        "--binary", "--cached", "--staged", "--name-only", "--name-status",
        "--numstat", "--stat", "--check", "-z", "--no-ext-diff",
        "--no-textconv", "--no-renames", "--minimal", "--patience",
    },
    "log": {
        "--oneline", "--decorate", "--no-decorate", "--name-only",
        "--name-status", "--stat", "--patch", "-p", "--no-patch",
        "--no-ext-diff", "--no-textconv", "-z",
    },
    "show": {
        "--oneline", "--name-only", "--name-status", "--stat", "--patch",
        "-p", "--no-patch", "--no-ext-diff", "--no-textconv", "-z",
    },
}

_GIT_SAFE_OPTION_PREFIXES = {
    "diff": ("--diff-filter=", "--unified=", "-U"),
    "log": ("--format=", "--pretty=", "--max-count=", "-n"),
    "show": ("--format=", "--pretty=", "--abbrev=", "--encoding="),
}

def _git_env(root: Path) -> dict[str, str]:
    import os
    resolved = root.expanduser().resolve()
    env = os.environ.copy()
    for key in ("GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS", "GIT_SSH_COMMAND", "GIT_PROXY_COMMAND"):
        env.pop(key, None)
    env.update({
        "GIT_DIR": str(resolved / ".git"),
        "GIT_WORK_TREE": str(resolved),
        "GIT_CEILING_DIRECTORIES": str(resolved.parent),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_PAGER": "cat",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": "/dev/null",
        "GIT_CONFIG_KEY_1": "core.fsmonitor",
        "GIT_CONFIG_VALUE_1": "false",
    })
    return env

def _validate_git_path(root: Path, raw: str) -> None:
    if Path(raw).is_absolute():
        raise PermissionError(f"git path is outside execution root: {raw}")
    _rooted_path(root, raw, ExecutionPolicy((root,)))

def _git_option_allowed(subcommand: str, token: str) -> bool:
    if token in _GIT_SAFE_OPTIONS.get(subcommand, set()):
        return True
    return any(token.startswith(prefix) for prefix in _GIT_SAFE_OPTION_PREFIXES.get(subcommand, ()))


def _validate_command(command: list[str]) -> None:
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
        raise ValueError("run_command requires a non-empty argv list")
    if Path(command[0]).name.casefold() != "git":
        raise PermissionError("autonomous run_command only permits read-only git inspection")
    if len(command) < 2:
        raise PermissionError("git subcommand is required")
    subcommand = command[1].casefold()
    if subcommand in _BLOCKED_GIT_SUBCOMMANDS:
        raise PermissionError("unsafe git operation is not allowed in autonomous execution")
    if subcommand not in _ALLOWED_GIT_SUBCOMMANDS:
        raise PermissionError(f"git subcommand is not allowed in autonomous execution: {subcommand}")

    separator = False
    for token in command[2:]:
        if token == "--":
            separator = True
            continue
        if separator:
            if token.startswith("-"):
                raise PermissionError(f"git option after '--' is not allowed in autonomous execution: {token}")
            continue

        # Only a fixed allowlist of read-only options is accepted.  This
        # intentionally rejects --output <file>, --output=<file>, and every
        # future/unknown option until it is reviewed and added explicitly.
        if token.startswith("-"):
            if not _git_option_allowed(subcommand, token):
                raise PermissionError(f"git option is not allowed in autonomous execution: {token}")
            continue

        if subcommand in {"status", "ls-files"}:
            raise PermissionError(f"git path must be supplied after '--': {token}")
        if subcommand in {"diff", "log", "show"}:
            if token.startswith("/") or token == ".." or token.startswith("../") or token.startswith("./"):
                raise PermissionError(f"git path must be supplied after '--': {token}")

def _validate_command_paths(command: list[str], root: Path) -> None:
    separator = False
    for token in command[2:]:
        if token == "--":
            separator = True
            continue
        if separator and not token.startswith("-"):
            _validate_git_path(root, token)

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
        _validate_command_paths(command, root)
        _rooted_path(root, str(request.get("cwd", ".")), policy)
        try:
            completed = subprocess.run(command, cwd=root, stdin=subprocess.DEVNULL, capture_output=True,
                                      text=True, timeout=policy.command_timeout_seconds, check=False,
                                      env=_git_env(root))
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
