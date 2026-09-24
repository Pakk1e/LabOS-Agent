from pathlib import Path
import pytest
from labos_agent.execution import ExecutionPolicy, execute_request, format_execution_results, parse_execution_requests

def policy(root: Path) -> ExecutionPolicy:
    return ExecutionPolicy((root.resolve(),))

def test_parse_execution_requests():
    marker = chr(96) * 3
    response = "Inspect this first.\n" + marker + "labos-exec\n" + '{"action":"run_command","command":["python3","-c","print(42)"]}' + "\n" + marker
    assert parse_execution_requests(response) == [{"action":"run_command","command":["python3","-c","print(42)"]}]

def test_run_command_is_argv_and_returns_result(tmp_path: Path):
    result = execute_request(tmp_path, {"action":"run_command","command":["pytest","--version"]}, policy(tmp_path))
    assert result.success and result.exit_code == 0 and result.stdout.strip().startswith("pytest")

def test_write_and_read_file_stay_inside_root(tmp_path: Path):
    execute_request(tmp_path, {"action":"write_file","path":"x.txt","content":"hello"}, policy(tmp_path))
    assert execute_request(tmp_path, {"action":"read_file","path":"x.txt"}, policy(tmp_path)).stdout == "hello"

def test_rejects_path_escape(tmp_path: Path):
    with pytest.raises(PermissionError):
        execute_request(tmp_path, {"action":"read_file","path":"../secret.txt"}, policy(tmp_path))

def test_format_results():
    result = execute_request(Path("."), {"action":"run_command","command":["pytest","--version"]}, policy(Path(".")))
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
    response = marker + "labos-exec\\n" + '{"action":"write_file","path":"doc.md","content":"' + content.replace("\\","\\\\").replace('"','\\"').replace("\n","\\n") + '"}' + "\\n" + marker
    request = parse_execution_requests(response)[0]
    assert request["content"] == content
