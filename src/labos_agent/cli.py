"""Command-line entry point for LabOS-Agent v0.1."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import uuid

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

    return parser


def parse_deadline(value: str | None, now: datetime) -> datetime | None:
    if value is None:
        return None
    hour, minute = (int(part) for part in value.split(":", 1))
    deadline = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if deadline <= now:
        raise ValueError("--until must be later today")
    return deadline


def main() -> None:
    args = build_parser().parse_args()
    if args.command != "run":
        return

    now = datetime.now().astimezone()
    deadline = parse_deadline(args.until, now)

    state = AgentState(project=args.project, run_id=uuid.uuid4().hex)
    controller = Controller(
        state=state,
        limits=SafetyLimits(
            deadline=deadline,
            max_iterations=args.max_iterations,
            max_rollovers=args.max_rollovers,
        ),
    )
    controller.start()

    state_path = Path("state") / args.project / "current.json"
    save_state(state_path, state)

    print(f"LabOS-Agent started: project={args.project} run={state.run_id}")
    if deadline:
        print(f"Deadline: {deadline.isoformat()}")
    print(f"State: {state.state.value}")
