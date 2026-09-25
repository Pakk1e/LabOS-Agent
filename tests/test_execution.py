from pathlib import Path
import subprocess
import pytest
from labos_agent.execution import ExecutionPolicy, execute_request, format_execution_results, parse_execution_requests

def policy(root: Path) -> ExecutionPolicy:
    return ExecutionPolicy((root.resolve(),))

def test_parse_execution_requests():
    marker = chr(96) * 3
    response = "Inspect this first.\n" + marker + "labos-exec\n" + '{"action":"run_command","command":["git","status","--short"]}' + "\n" + marker
    assert parse_execution_requests(response) == [{"action":"run_command","command":["git","status","--short"]}]

def test_run_command_is_argv_and_returns_result(tmp_path: Path):
    subprocess.run(["git","init",str(tmp_path)],check=True,capture_output=True)
    result = execute_request(tmp_path, {"action":"run_command","command":["git","status","--short"]}, policy(tmp_path))
    assert result.success and result.exit_code == 0

def test_write_and_read_file_stay_inside_root(tmp_path: Path):
    execute_request(tmp_path, {"action":"write_file","path":"x.txt","content":"hello"}, policy(tmp_path))
    assert execute_request(tmp_path, {"action":"read_file","path":"x.txt"}, policy(tmp_path)).stdout == "hello"

def test_rejects_path_escape(tmp_path: Path):
    with pytest.raises(PermissionError):
        execute_request(tmp_path, {"action":"read_file","path":"../secret.txt"}, policy(tmp_path))

def test_format_results():
    root = Path(__file__).resolve().parents[1]
    result = execute_request(root, {"action":"run_command","command":["git","status","--short"]}, policy(root))
    text = format_execution_results([result])
    assert "LabOS server execution results:" in text and "success=True" in text


def test_controller_commands_are_blocked(tmp_path: Path):
    with pytest.raises(PermissionError):
        execute_request(tmp_path, {"action":"run_command","command":["git","push"]}, policy(tmp_path))


def test_execution_protocol_marker_is_detectable():
    marker = chr(96) * 3
    response = marker + "labos-exec\n" + '{"action":"run_command","command":["git","status","--short"]}' + "\n" + marker
    assert parse_execution_requests(response)[0]["action"] == "run_command"


def test_parse_inline_execution_request():
    response = 'labos-exec{"action":"read_file","path":"frontend-dev/src/components/dashboard/Widget.jsx"}'
    requests = parse_execution_requests(response)
    assert requests == [{
        "action": "read_file",
        "path": "frontend-dev/src/components/dashboard/Widget.jsx",
    }]


def test_parse_inline_execution_request_allows_whitespace_after_marker():
    response = 'labos-exec  {"action":"read_file","path":"backend/test/weather.test.js"}'
    assert parse_execution_requests(response) == [{
        "action": "read_file",
        "path": "backend/test/weather.test.js",
    }]


def test_parse_mixed_execution_requests():
    response = (
        'labos-exec{"action":"read_file","path":"a.txt"}'
        '\n```labos-exec\n'
        '{"action":"read_file","path":"b.txt"}'
        '\n```'
    )
    requests = parse_execution_requests(response)
    assert requests == [
        {"action": "read_file", "path": "a.txt"},
        {"action": "read_file", "path": "b.txt"},
    ]

def test_parse_json_fenced_execution_request():
    response = (
        "The implementation is below.\n"
        "```json\n"
        '{"action":"write_file","path":"backend/test/weather.test.js","content":"ok"}\n'
        "```"
    )
    assert parse_execution_requests(response) == [{
        "action": "write_file",
        "path": "backend/test/weather.test.js",
        "content": "ok",
    }]


def test_parse_json_fence_ignores_non_execution_json():
    response = '```json\n{"action":"example","path":"not-a-request"}\n```'
    assert parse_execution_requests(response) == []


def test_parse_json_fenced_content_may_contain_markdown_fence():
    content = "before ```python\nprint(1)\n``` after"
    import json
    response = "```json\n" + json.dumps({
        "action": "write_file",
        "path": "doc.md",
        "content": content,
    }) + "\n```"
    assert parse_execution_requests(response) == [{
        "action": "write_file",
        "path": "doc.md",
        "content": content,
    }]



def test_git_global_option_cannot_bypass_controller_gate(tmp_path: Path):
    with pytest.raises(PermissionError):
        execute_request(
            tmp_path,
            {"action": "run_command", "command": ["git", "-C", str(tmp_path), "push"]},
            policy(tmp_path),
        )


def test_shell_interpreters_are_blocked(tmp_path: Path):
    with pytest.raises(PermissionError):
        execute_request(
            tmp_path,
            {"action": "run_command", "command": ["bash", "-lc", "echo unsafe"]},
            policy(tmp_path),
        )


def test_relative_allowed_root_is_normalized(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    policy_with_relative_root = ExecutionPolicy((Path("."),))
    execute_request(
        tmp_path,
        {"action": "write_file", "path": "nested/file.txt", "content": "ok"},
        policy_with_relative_root,
    )
    assert (tmp_path / "nested/file.txt").read_text() == "ok"

def test_reserved_paths_are_blocked(tmp_path: Path):
    for path in (".env", ".git/config", ".github/workflows/test.yml", "browser-profile/cookies"):
        with pytest.raises(PermissionError):
            execute_request(tmp_path, {"action":"read_file","path":path}, policy(tmp_path))

def test_unallowlisted_executables_are_blocked(tmp_path: Path):
    for command in (["cat","/etc/passwd"],["find",".","-delete"],["bash","-lc","echo unsafe"],["python3","-c","print(1)"]):
        with pytest.raises(PermissionError):
            execute_request(tmp_path, {"action":"run_command","command":command}, policy(tmp_path))

def test_controller_git_commands_extended(tmp_path: Path):
    for command in (["git","switch","main"],["git","clean","-fdx"],["git","stash"],["git","-c","alias.p=push","p"]):
        with pytest.raises(PermissionError):
            execute_request(tmp_path, {"action":"run_command","command":command}, policy(tmp_path))

def test_network_escape_tools_are_blocked(tmp_path: Path):
    for command in (["env","git","push"],["curl","http://example.invalid"],["wget","http://example.invalid"]):
        with pytest.raises(PermissionError):
            execute_request(tmp_path, {"action":"run_command","command":command}, policy(tmp_path))

def test_write_file_json_content_may_contain_markdown_fence():
    marker = chr(96) * 3
    content = "before " + marker + "python\\nprint(1)\\n" + marker + " after"
    response = marker + "labos-exec\n" + '{"action":"write_file","path":"doc.md","content":"' + content.replace("\\","\\\\").replace('"','\\"').replace("\n","\\n") + '"}' + "\\n" + marker
    request = parse_execution_requests(response)[0]
    assert request["content"] == content


def test_reserved_paths_are_blocked_at_any_depth_case_insensitively(tmp_path: Path):
    for path in ("sub/.env.local", "sub/.ENV", "sub/.git/config", "sub/.github/workflows/x.yml"):
        with pytest.raises(PermissionError):
            execute_request(tmp_path, {"action": "write_file", "path": path, "content": "blocked"}, policy(tmp_path))


def test_git_read_surface_rejects_no_index_output_and_external_paths(tmp_path: Path):
    for command in (
        ["git", "diff", "/dev/null", "etc/passwd"],
        ["git", "diff", "--output=/tmp/labos-out", "HEAD", "--", "file.txt"],
        ["git", "diff", "--output", "/tmp/labos-out", "HEAD", "--", "file.txt"],
        ["git", "log", "--output=/tmp/labos-out"],
        ["git", "log", "--output", "/tmp/labos-out"],
        ["git", "show", "--output=/tmp/labos-out", "HEAD"],
        ["git", "show", "--output", "/tmp/labos-out", "HEAD"],
        ["git", "diff", "--ext-diff", "HEAD", "--", "file.txt"],
        ["git", "show", "HEAD", "--", "/etc/passwd"],
        ["git", "diff", "src/foo.py"],
        ["git", "log", "HEAD:../outside"],
        ["git", "show", "origin/../outside"],
    ):
        with pytest.raises(PermissionError):
            execute_request(tmp_path, {"action": "run_command", "command": command}, policy(tmp_path))


def test_git_read_surface_is_allowlisted_per_subcommand(tmp_path: Path):
    for command in (
        ["git", "status", "--future-write-option"],
        ["git", "diff", "--future-write-option"],
        ["git", "log", "--future-write-option"],
        ["git", "show", "--future-write-option"],
        ["git", "ls-files", "--future-write-option"],
        ["git", "log", "--output", "file.txt"],
        ["git", "diff", "--output=file.txt"],
        ["git", "show", "--output", "file.txt"],
    ):
        with pytest.raises(PermissionError):
            execute_request(tmp_path, {"action": "run_command", "command": command}, policy(tmp_path))


def test_git_execution_is_rooted_even_from_nested_cwd(tmp_path: Path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "LabOS Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "labos@example.invalid"], check=True)
    (tmp_path / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "base"], check=True, capture_output=True)
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / ".git").mkdir()
    (nested / ".git" / "config").write_text(
        "[core]\nfsmonitor = /bin/sh -c 'echo escaped > /tmp/labos-agent-escape'\n",
        encoding="utf-8",
    )
    result = execute_request(
        tmp_path,
        {"action": "run_command", "command": ["git", "status"], "cwd": "sub"},
        policy(tmp_path),
    )
    assert result.success
    assert not Path("/tmp/labos-agent-escape").exists()


def test_run_command_honors_relative_cwd(tmp_path: Path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    nested = tmp_path / "nested"
    nested.mkdir()
    result = execute_request(
        tmp_path,
        {"action": "run_command", "command": ["git", "status", "--short"], "cwd": "nested"},
        policy(tmp_path),
    )
    assert result.success


def test_git_read_surface_allows_slash_containing_revision_refs(tmp_path: Path, monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stdout = "deadbeef"
        stderr = ""

    original_run = subprocess.run

    def fake_run(command, **kwargs):
        if command[:4] == ["git", "rev-parse", "--verify", "--end-of-options"]:
            calls.append(command)
            return Result()
        return original_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    for command in (
        ["git", "log", "feature/foo"],
        ["git", "show", "refs/heads/feature/foo"],
    ):
        execute_request(tmp_path, {"action": "run_command", "command": command}, policy(tmp_path))
    assert any("feature/foo" in command[-1] for command in calls)


def test_parse_stripped_json_execution_response():
    response = 'JSON  {"action":"read_file","path":"README.md"}'
    assert parse_execution_requests(response) == [{
        "action": "read_file",
        "path": "README.md",
    }]


def test_parse_stripped_json_requires_supported_whole_response():
    assert parse_execution_requests('JSON  {"action":"example","path":"README.md"}') == []
    assert parse_execution_requests('JSON  {"action":"read_file","path":"README.md"} extra') == []
    assert parse_execution_requests('Here is JSON  {"action":"read_file","path":"README.md"}') == []

def test_parse_bare_json_execution_response():
    response = '{"action":"read_file","path":"README.md"}'
    assert parse_execution_requests(response) == [{
        "action": "read_file",
        "path": "README.md",
    }]


def test_parse_bare_json_requires_supported_whole_response():
    assert parse_execution_requests('{"action":"example","path":"README.md"}') == []
    assert parse_execution_requests('{"action":"read_file","path":"README.md"} extra') == []
