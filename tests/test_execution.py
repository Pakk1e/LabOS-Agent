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
    result = execute_request(tmp_path, {"action":"run_command","command":["python3","-c","print('ok')"]}, policy(tmp_path))
    assert result.success and result.exit_code == 0 and result.stdout.strip() == "ok"

def test_write_and_read_file_stay_inside_root(tmp_path: Path):
    execute_request(tmp_path, {"action":"write_file","path":"x.txt","content":"hello"}, policy(tmp_path))
    assert execute_request(tmp_path, {"action":"read_file","path":"x.txt"}, policy(tmp_path)).stdout == "hello"

def test_rejects_path_escape(tmp_path: Path):
    with pytest.raises(PermissionError):
        execute_request(tmp_path, {"action":"read_file","path":"../secret.txt"}, policy(tmp_path))

def test_format_results():
    result = execute_request(Path("."), {"action":"run_command","command":["python3","-c","print('ok')"]}, policy(Path(".")))
    text = format_execution_results([result])
    assert "LabOS server execution results:" in text and "success=True" in text


def test_controller_commands_are_blocked(tmp_path: Path):
    with pytest.raises(PermissionError):
        execute_request(tmp_path, {"action":"run_command","command":["git","push"]}, policy(tmp_path))


def test_execution_protocol_marker_is_detectable():
    marker = chr(96) * 3
    response = marker + "labos-exec\n" + '{"action":"run_command","command":["git","status","--short"]}' + "\n" + marker
    assert parse_execution_requests(response)[0]["action"] == "run_command"
