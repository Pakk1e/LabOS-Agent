"""Command-line entry point for LabOS-Agent."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import uuid

from .browser.chatgpt import ChatGPTPage
from .browser.session import BrowserSession
from .controller import Controller
from .safety import SafetyLimits
from .state import AgentState, save_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lab-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="start a controller run")
    run.add_argument("project")
    run.add_argument("--until", help="local deadline, HH:MM")
    run.add_argument("--max-iterations", type=int, default=50)
    run.add_argument("--max-rollovers", type=int, default=10)

    browser = sub.add_parser("browser-smoke", help="open persistent ChatGPT browser for manual smoke testing")
    browser.add_argument("--profile-dir", default="./browser-profile")
    browser.add_argument("--headless", action="store_true")
    browser.add_argument("--display", help="X display to use for headed Chromium, e.g. :99")
    browser.add_argument("--test-message", help="send exactly one controlled test message")
    browser.add_argument("--keep-open", action="store_true", help="keep the browser open after smoke testing")

    attach = sub.add_parser("browser-attach", help="attach to an existing Chromium over CDP")
    attach.add_argument("--cdp", default="http://127.0.0.1:9222", help="Chromium DevTools HTTP endpoint")
    attach.add_argument("--test-message", help="send exactly one controlled test message")
    attach.add_argument("--keep-open", action="store_true", help="keep the attached session alive")
    return parser


def parse_deadline(value: str | None, now: datetime) -> datetime | None:
    if value is None:
        return None
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
    except ValueError as exc:
        raise ValueError("--until must be HH:MM") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("--until must be HH:MM")
    deadline = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if deadline <= now:
        raise ValueError("--until must be later today")
    return deadline


def run_command(args: argparse.Namespace) -> None:
    now = datetime.now().astimezone()
    deadline = parse_deadline(args.until, now)
    state = AgentState(project=args.project, run_id=uuid.uuid4().hex)
    controller = Controller(
        state=state,
        limits=SafetyLimits(deadline=deadline, max_iterations=args.max_iterations, max_rollovers=args.max_rollovers),
    )
    controller.start()
    state_path = Path("state") / args.project / "current.json"
    save_state(state_path, state)
    print(f"LabOS-Agent started: project={args.project} run={state.run_id}")
    if deadline:
        print(f"Deadline: {deadline.isoformat()}")
    print(f"State: {state.state.value}")


def browser_smoke_command(args: argparse.Namespace) -> None:
    profile = Path(args.profile_dir).expanduser().resolve()
    print(f"Launching persistent Chromium profile: {profile}")
    print("If ChatGPT is not authenticated, log in manually. No credentials are stored by LabOS-Agent.")
    import os
    if args.display:
        os.environ["DISPLAY"] = args.display
    with BrowserSession(profile, headless=args.headless) as session:
        page = session.context.pages[0] if session.context.pages else session.context.new_page()
        chat = ChatGPTPage(page)
        status = chat.open()
        print(f"URL: {status.url}")
        print(f"Title: {status.title}")
        print(f"Message input detected: {status.has_input}")
        if status.is_challenge:
            print("Browser is showing a Cloudflare/verification challenge. Complete verification in an interactive browser before retrying.")
        if args.test_message and status.has_input:
            print("Sending one controlled test message.")
            response = chat.send_and_wait_for_response(args.test_message)
            print("Test message completed.")
            print("Assistant response:")
            print(response)
        if not status.has_input:
            screenshot = profile / "browser-smoke.png"
            session.context.pages[0].screenshot(path=str(screenshot), full_page=True)
            print(f"Diagnostic screenshot: {screenshot}")
            print("ChatGPT input was not detected. Do not enable autonomous mode.")
        if args.keep_open:
            print("Browser kept open. Press Ctrl+C to close it.")
            try:
                import time
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("Closing browser.")


def browser_attach_command(args: argparse.Namespace) -> None:
    print(f"Attaching to existing Chromium: {args.cdp}")
    print("LabOS-Agent will not launch the remote Chromium.")

    with BrowserSession(Path(".")) as session:
        context = session.connect_over_cdp(args.cdp)
        pages = context.pages
        chat_pages = [page for page in pages if page.url.startswith("https://chatgpt.com/")]
        page = chat_pages[0] if chat_pages else (pages[0] if pages else context.new_page())
        chat = ChatGPTPage(page)
        status = chat.status()

        print(f"URL: {status.url}")
        print(f"Title: {status.title}")
        print(f"Message input detected: {status.has_input}")
        if status.is_challenge:
            print("Browser is showing a verification challenge. Stop here and complete it manually.")
            return
        if not status.is_chatgpt:
            print("No ChatGPT page is currently attached. Open ChatGPT in the existing Chromium first.")
            return
        if not status.has_input:
            print("ChatGPT message input was not detected. Do not enable autonomous mode.")
            return

        if args.test_message:
            print("Sending one controlled test message.")
            response = chat.send_and_wait_for_response(args.test_message)
            print("Test message completed.")
            print("Assistant response:")
            print(response)

        if args.keep_open:
            print("Attached session kept alive. Press Ctrl+C to detach.")
            try:
                import time
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("Detaching from Chromium.")


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "run":
        run_command(args)
    elif args.command == "browser-smoke":
        browser_smoke_command(args)
    elif args.command == "browser-attach":
        browser_attach_command(args)
