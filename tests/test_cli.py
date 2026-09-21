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
