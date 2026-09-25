"""Small, always-available runtime tracing for LabOS-Agent."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime


def enabled() -> bool:
    value = os.getenv("LABOS_TRACE", "1").strip().casefold()
    return value not in {"0", "false", "off", "no"}


def dom_enabled() -> bool:
    value = os.getenv("LABOS_TRACE_DOM", "0").strip().casefold()
    return value in {"1", "true", "on", "yes"}


def trace(event: str, **fields: object) -> None:
    if not enabled():
        return
    payload = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "event": event,
        **fields,
    }
    print("[LABOS] " + json.dumps(payload, ensure_ascii=False, default=str), file=sys.stderr, flush=True)
