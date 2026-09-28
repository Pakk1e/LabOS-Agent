"""Human-friendly runtime logging for LabOS-Agent.

Normal output is optimized for an operator watching a long-running server.
Set LABOS_TRACE_FORMAT=json when machine-readable diagnostic output is needed.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from typing import Any


_START = time.monotonic()

_EVENT_LABELS = {
    "run.start": "Starting run",
    "run.complete": "Run complete",
    "run.stop": "Run stopped",
    "run.error": "Run failed",
    "iteration.start": "Starting iteration",
    "iteration.complete": "Iteration complete",
    "chat.prompt": "Sending task to ChatGPT",
    "chat.response": "ChatGPT responded",
    "chat.error": "ChatGPT communication failed",
    "ci.start": "Running local CI",
    "ci.complete": "Local CI finished",
    "ci.error": "Local CI failed",
    "remote_ci.start": "Waiting for GitHub Actions",
    "remote_ci.progress": "GitHub Actions still running",
    "remote_ci.complete": "GitHub Actions finished",
    "remote_ci.error": "GitHub Actions check failed",
    "execution.requests": "Server operations requested",
    "execution.complete": "Server operations finished",
    "execution.none": "No server operations requested",
    "trajectory.error": "Trajectory log warning",
    "recovery.start": "Recovering previous run",
    "recovery.complete": "Recovery complete",
}

_LEVEL_TEXT = {
    "DEBUG": ("DBG", "·"),
    "INFO": ("INF", "›"),
    "OK": (" OK", "✓"),
    "WARN": ("WRN", "!"),
    "ERROR": ("ERR", "✗"),
}


def enabled() -> bool:
    value = os.getenv("LABOS_TRACE", "1").strip().casefold()
    return value not in {"0", "false", "off", "no"}


def dom_enabled() -> bool:
    value = os.getenv("LABOS_TRACE_DOM", "0").strip().casefold()
    return value in {"1", "true", "on", "yes"}


def _json_enabled() -> bool:
    return os.getenv("LABOS_TRACE_FORMAT", "human").strip().casefold() == "json"


def _level(event: str, fields: dict[str, Any]) -> str:
    if fields.get("success") is False or event.endswith((".error", ".failed")):
        return "ERROR"
    if event.endswith(".complete") and fields.get("success") is True:
        return "OK"
    if event.endswith((".warning", ".warn")) or event == "trajectory.error":
        return "WARN"
    return "INFO"


def _short(value: Any, limit: int = 180) -> str:
    if isinstance(value, bool):
        text = "yes" if value else "no"
    elif isinstance(value, (list, tuple, set)):
        text = ", ".join(str(item) for item in value)
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _format_key(key: str) -> str:
    return key.replace("_", " ")


def _details(fields: dict[str, Any]) -> str:
    hidden = {
        "response_tail",
        "result_tail",
        "prompt",
        "project",
        "error",
        "detail",
    }
    priority = (
        "iteration", "stage", "success", "meaningful", "count",
        "action", "actions", "sha", "conclusion", "status",
        "duration_seconds", "reason", "summary", "timeout_seconds",
        "worktree_fingerprint",
    )
    keys = [key for key in priority if key in fields and key not in hidden]
    keys.extend(
        key for key in fields
        if key not in hidden and key not in keys and not key.endswith("_chars")
    )

    parts: list[str] = []
    for key in keys:
        value = fields[key]
        if key == "duration_seconds":
            value_text = f"{float(value):.1f}s"
            key_text = "duration"
        elif key.endswith("_chars"):
            value_text = f"{value} chars"
            key_text = _format_key(key.removesuffix("_chars"))
        else:
            value_text = _short(value)
            key_text = _format_key(key)
        parts.append(f"{key_text}={value_text}")

    for key, value in fields.items():
        if key.endswith("_chars") and key not in hidden and key not in keys:
            parts.append(f"{_format_key(key.removesuffix('_chars'))}={value} chars")

    if fields.get("error") or fields.get("detail"):
        error = fields.get("error") or fields.get("detail")
        parts.append(f"reason={_short(error, 240)}")

    return "  " + "  ".join(parts) if parts else ""


def trace(event: str, **fields: object) -> None:
    """Write concise operator-facing logs to stderr."""
    if not enabled():
        return

    payload = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "event": event,
        **fields,
    }

    if _json_enabled():
        print("[LABOS] " + json.dumps(payload, ensure_ascii=False, default=str), file=sys.stderr, flush=True)
        return

    level = _level(event, fields)
    level_text, icon = _LEVEL_TEXT[level]
    now = datetime.now().astimezone().strftime("%H:%M:%S")
    elapsed = time.monotonic() - _START
    project = _short(fields.get("project", ""), 32).strip()
    scope = f" [{project}]" if project else ""
    label = _EVENT_LABELS.get(event, event.replace(".", " › "))
    details = _details(fields)

    print(
        f"{now} {level_text} {icon} +{elapsed:7.1f}s{scope} {label}{details}",
        file=sys.stderr,
        flush=True,
    )
