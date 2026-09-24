from pathlib import Path
import pytest
from labos_agent.execution import ExecutionPolicy, execute_request, format_execution_results, parse_execution_requests

def policy(root: Path) -> ExecutionPolicy:
    return ExecutionPolicy((root.resolve(),))

def test_reserved_paths_are_blocked(tmp_path: Path):
    for path in (".env", ".git/config", ".github/workflows/test.yml", "browser-profile/cookies"):
        with pytest.raises(PermissionError):
            execute_request(tmp_path, {"action":"read_file","path":path}, policy(tmp_path))

def test_unallowlisted_executables_are_blocked(tmp_path: Path):
    for command in (["cat","/etc/passwd"],["find",".","-delete"],["bash","-lc","echo unsafe"],["python3","-c","print(1)"]):
        with pytest.raises(PermissionError):
            execute_request(tmp_path, {"action":"run_command","command":command}, policy(tmp_path))

def test_controller_git_commands_are_blocked(tmp_path: Path):
    for command in (["git","push"],["git","switch","main"],["git","clean","-fdx"],["git","stash"],["git","-c","alias.p=push","p"]):
        with pytest.raises(PermissionError):
            execute_request(tmp_path, {"action":"run_command","command":command}, policy(tmp_path))

def test_shell_and_network_escape_tools_are_blocked(tmp_path: Path):
    for command in (["env","git","push"],["curl","http://example.invalid"],["wget","http://example.invalid"]):
        with pytest.raises(PermissionError):
            execute_request(tmp_path, {"action":"run_command","command":command}, policy(tmp_path))

def test_execution_protocol_marker_is_detectable():
    marker = chr(96) * 3
    response = marker + "labos-exec\\n" + '{"action":"run_command","command":["git","status","--short"]}' + "\\n" + marker
    assert parse_execution_requests(response)[0]["action"] == "run_command"

def test_parse_inline_execution_request():
    response = 'labos-exec{"action":"read_file","path":"a.txt"}'
    assert parse_execution_requests(response) == [{"action":"read_file","path":"a.txt"}]

def test_parse_mixed_execution_requests():
    response = 'labos-exec{"action":"read_file","path":"a.txt"}\\n' + chr(96)*3 + 'labos-exec\\n{"action":"read_file","path":"b.txt"}\\n' + chr(96)*3
    assert parse_execution_requests(response) == [{"action":"read_file","path":"a.txt"},{"action":"read_file","path":"b.txt"}]
