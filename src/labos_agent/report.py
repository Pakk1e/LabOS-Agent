"""Deterministic morning report generation from persisted LabOS supervisor state."""
from __future__ import annotations
import json
from pathlib import Path

def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}

def _latest_events(state_dir: Path, limit: int = 12) -> list[dict]:
    runs = state_dir / "runs"
    if not runs.exists(): return []
    candidates = sorted(runs.glob("*.events.jsonl"))
    if not candidates: return []
    try: lines = candidates[-1].read_text(encoding="utf-8").splitlines()
    except OSError: return []
    events = []
    for line in lines:
        if not line.strip(): continue
        try: value = json.loads(line)
        except (ValueError, TypeError): continue
        if isinstance(value, dict): events.append(value)
    return events[-limit:]

def build_morning_report(state_dir: Path, project: str) -> str:
    """Build a concise Markdown report from persisted supervisor evidence."""
    state_dir = state_dir.expanduser()
    state = _read_json(state_dir / "supervisor_state.json")
    current = _read_json(state_dir / "current.json")
    status = state.get("state") or current.get("state") or "UNKNOWN"
    iteration = state.get("iteration", current.get("iteration", 0))
    commit = state.get("last_observed_commit") or current.get("commit") or "unknown"
    branch = state.get("last_observed_branch") or current.get("branch") or "unknown"
    ci_status = state.get("last_observed_ci_status") or "unknown"
    ci_conclusion = state.get("last_observed_ci_conclusion") or "unknown"
    next_action = state.get("next_action") or current.get("next_action") or "unknown"
    reason = state.get("reason") or current.get("reason") or "—"
    lines = [f"# LabOS Morning Report — {project}", "", "## Current state",
             f"- Supervisor: **{status}**", f"- Iteration: **{iteration}**",
             f"- Branch: `{branch}`", f"- Commit: `{commit}`",
             f"- GitHub Actions: **{ci_status} / {ci_conclusion}**",
             f"- Next action: **{next_action}**", f"- Reason: {reason}", "", "## Recent events"]
    events = _latest_events(state_dir)
    if not events: lines.append("- No persisted run events.")
    else:
        for event in events:
            name = event.get("event", "unknown")
            details = [f"{k}={event[k]}" for k in ("iteration","state","success","conclusion","status","reason") if k in event]
            lines.append(f"- `{name}`" + (f" — {", ".join(details)}" if details else ""))
    return "\n".join(lines) + "\n"