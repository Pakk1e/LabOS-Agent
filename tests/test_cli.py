from labos_agent.cli import build_parser


def test_project_new_chat_test_command_parser():
    parser = build_parser()
    args = parser.parse_args([
        "browser-project-new-chat-test",
        "--cdp", "http://127.0.0.1:9222",
        "--project-name", "Vadovsky Tech — Lab OS",
    ])
    assert args.command == "browser-project-new-chat-test"
    assert args.project_name == "Vadovsky Tech — Lab OS"


def test_project_rollover_test_command_parser():
    parser = build_parser()
    args = parser.parse_args([
        "browser-project-rollover-test",
        "--cdp", "http://127.0.0.1:9222",
        "--project-name", "Vadovsky Tech — Lab OS",
        "--handoff-message", "LABOS_ROLLOVER_TEST",
        "--require-rollover",
    ])
    assert args.command == "browser-project-rollover-test"
    assert args.project_name == "Vadovsky Tech — Lab OS"
    assert args.handoff_message == "LABOS_ROLLOVER_TEST"
    assert args.require_rollover is True



def test_project_handoff_rollover_test_command_parser():
    parser = build_parser()
    args = parser.parse_args([
        "browser-project-handoff-rollover-test",
        "--cdp", "http://127.0.0.1:9222",
        "--project-name", "Vadovsky Tech — Weather",
        "--project-url", "https://chatgpt.com/g/g-p-6ab2a81b723081918c9e1052acf0af9c-vadovsky-tech-weather/project",
        "--state-dir", "state/test-rollover",
    ])
    assert args.command == "browser-project-handoff-rollover-test"
    assert args.project_name == "Vadovsky Tech — Lab OS"
    assert args.state_dir == "state/test-rollover"
