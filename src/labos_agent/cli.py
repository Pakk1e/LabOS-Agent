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
    controller = Controller(state=state, limits=SafetyLimits(
        deadline=deadline, max_iterations=args.max_iterations,
        max_rollovers=args.max_rollovers))
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
    with BrowserSession(profile, headless=args.headless) as session:
        page = session.context.pages[0] if session.context.pages else session.context.new_page()
        chat = ChatGPTPage(page)
        status = chat.open()
        print(f"URL: {status.url}")
        print(f"Title: {status.title}")
        print(f"Message input detected: {status.has_input}")
        if not status.has_input:
            print("ChatGPT input was not detected. Do not enable autonomous mode.")

def main() -> None:
    args = build_parser().parse_args()
    if args.command == "run":
        run_command(args)
    elif args.command == "browser-smoke":
        browser_smoke_command(args)
