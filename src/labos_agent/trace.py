"""Human-friendly runtime logging for LabOS-Agent."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any


def enabled() -> bool:
    value = os.getenv("LABOS_TRACE", "1").strip().casefold()
    return value not in {"0", "false", "off", "no"}


def dom_enabled() -> bool:
    value = os.getenv("LABOS_TRACE_DOM", "0").strip().casefold()
    return value in {"1", "true", "on", "yes"}


_EVENT_LABELS = {
    "chat.prompt": "Sending task to ChatGPT",
    "chat.response": "ChatGPT responded",
    "ci.start": "Running local CI",
    "ci.complete": "Local CI finished",
    "execution.requests": "Server operations requested",
    "execution.complete": "Server operations finished",
    "execution.none": "No server operations requested",
    "trajectory.error": "Trajectory log warning",
}


def _level(event: str, fields: dict[str, Any]) -> str:
    if event.endswith(".error") or event.endswith(".failed") or fields.get("success") is False:
        return "ERROR"
    if event.endswith(".complete") and fields.get("success") is True:
        return "OK"
    if "error" in event or "failure" in event:
        return "ERROR"
    return "INFO"


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    return str(value)


def _details(fields: dict[str, Any]) -> str:
    hidden = {
        "response_tail",
        "result_tail",
        "prompt",
        "error",
        "detail",
    }
    parts: list[str] = []
    for key, value in fields.items():
        if key in hidden or key == "project":
            continue
        if key.endswith("_chars"):
            parts.append(f"{key.removesuffix('_chars')}={value} chars")
        elif key == "duration_seconds":
            parts.append(f"duration={float(value):.1f}s")
        elif key == "root":
            parts.append(f"root={value}")
        else:
            parts.append(f"{key}={_value(value)}")
    return "  " + "  ".join(parts) if parts else ""


def trace(event: str, **fields: object) -> None:
    """Write concise operator-facing logs to stderr.

    Set LABOS_TRACE=0 to disable these logs. Detailed response/DOM diagnostics
    remain available through dedicated debug tooling rather than normal output.
    """
    if not enabled():
        return

    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    level = _level(event, fields)
    project = str(fields.get("project", "")).strip()
    scope = f" [{project}]" if project else ""
    label = _EVENT_LABELS.get(event, event.replace(".", " › "))
    details = _details(fields)
    print(f"{now} {level:<5}{scope} {label}{details}", file=sys.stderr, flush=True)
