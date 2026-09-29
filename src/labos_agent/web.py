"""Lightweight Lab OS control UI and local API.

The web service is intentionally dependency-free beyond LabOS-Agent's existing
PyYAML dependency. It is designed for the trusted local server, not public
internet exposure.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import unquote, urlparse

from .config import load_config

_PROJECT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
_REPO_RE = re.compile(r"^[^/\\s]+/[^/\\s]+$")
_processes: dict[str, subprocess.Popen] = {}
_process_lock = Lock()


def _config_payload(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_config(path: Path, payload: dict) -> None:
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    tmp.replace(path)


def _memory(config_path: Path, project: str) -> dict:
    path = config_path.parent / "state" / project / "supervisor_state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _project_view(config_path: Path, name: str, project) -> dict:
    memory = _memory(config_path, name)
    analysis = memory.get("last_analysis") or {}
    with _process_lock:
        process = _processes.get(name)
        running = process is not None and process.poll() is None
        if process is not None and not running:
            _processes.pop(name, None)
    return {
        "name": name,
        "repository": project.repository,
        "project_root": str(project.project_root),
        "project_name": project.project_name or name,
        "project_url": project.project_url,
        "ci_stage": project.ci_stage,
        "running": running,
        "pid": process.pid if running else None,
        "conversation_url": memory.get("conversation_url"),
        "commit": memory.get("last_observed_commit"),
        "branch": memory.get("last_observed_branch"),
        "ci_run": memory.get("last_observed_ci_run"),
        "ci_status": memory.get("last_observed_ci_status"),
        "ci_conclusion": memory.get("last_observed_ci_conclusion"),
        "state": analysis.get("state") or analysis.get("STATE") or "IDLE",
        "task_status": analysis.get("task_status") or analysis.get("TASK_STATUS") or "UNKNOWN",
        "next_action": analysis.get("next_action") or analysis.get("NEXT_ACTION") or "—",
        "updated_at": memory.get("updated_at"),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "LabOS-Web/0.1"

    @property
    def config_path(self) -> Path:
        return self.server.config_path  # type: ignore[attr-defined]

    def _send(self, status: int, body, content_type: str = "application/json") -> None:
        raw = body if isinstance(body, bytes) else (
            json.dumps(body, ensure_ascii=False).encode() if content_type == "application/json"
            else str(body).encode()
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON body") from exc

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            path = Path(__file__).parent / "web" / "index.html"
            return self._send(200, path.read_bytes(), "text/html; charset=utf-8")
        if parsed.path == "/api/projects":
            config = load_config(self.config_path)
            return self._send(200, {
                "projects": [_project_view(self.config_path, n, p) for n, p in config.projects.items()]
            })
        if parsed.path.startswith("/api/projects/"):
            name = unquote(parsed.path.removeprefix("/api/projects/")).strip("/")
            config = load_config(self.config_path)
            project = config.projects.get(name)
            if project is None:
                return self._send(404, {"error": "project not found"})
            return self._send(200, _project_view(self.config_path, name, project))
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            body = self._json()
            if parsed.path == "/api/projects":
                return self._create_project(body)
            if parsed.path.startswith("/api/projects/") and parsed.path.endswith("/supervise"):
                name = unquote(parsed.path.removeprefix("/api/projects/").removesuffix("/supervise")).strip("/")
                return self._start_supervisor(name, body)
            return self._send(404, {"error": "not found"})
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})
        except Exception as exc:
            return self._send(500, {"error": str(exc)})

    def _create_project(self, body: dict):
        name = str(body.get("name", "")).strip()
        repository = str(body.get("repository", "")).strip()
        root = str(body.get("project_root", "")).strip()
        if not _PROJECT_RE.fullmatch(name):
            return self._send(400, {"error": "name must use letters, numbers, dot, dash or underscore"})
        if not _REPO_RE.fullmatch(repository):
            return self._send(400, {"error": "repository must be owner/name"})
        if not root.startswith("/"):
            return self._send(400, {"error": "project_root must be an absolute path"})
        payload = _config_payload(self.config_path)
        projects = payload.setdefault("projects", {})
        if name in projects:
            return self._send(409, {"error": "project already exists"})
        project = {
            "repository": repository,
            "project_root": root,
            "continuation_message": body.get("continuation_message") or f"Continue {name.title()}",
        }
        for key in ("project_name", "project_url", "new_chat_selector", "ci_stage"):
            if body.get(key):
                project[key] = body[key]
        projects[name] = project
        _write_config(self.config_path, payload)
        config = load_config(self.config_path)
        return self._send(201, _project_view(self.config_path, name, config.projects[name]))

    def _start_supervisor(self, name: str, body: dict):
        config = load_config(self.config_path)
        if name not in config.projects:
            return self._send(404, {"error": "project not found"})
        with _process_lock:
            existing = _processes.get(name)
            if existing is not None and existing.poll() is None:
                return self._send(409, {"error": "supervisor already running", "pid": existing.pid})
            max_turns = int(body.get("max_turns", 0))
            cmd = [sys.executable, "-m", "labos_agent.cli", "supervise", name,
                   "--config", str(self.config_path), "--max-turns", str(max_turns)]
            process = subprocess.Popen(cmd, cwd=str(self.config_path.parent))
            _processes[name] = process
        return self._send(202, {"started": True, "pid": process.pid, "project": name})


def serve(config_path: Path, host: str = "127.0.0.1", port: int = 8080) -> None:
    config_path = config_path.expanduser().resolve()
    load_config(config_path)
    server = ThreadingHTTPServer((host, port), Handler)
    server.config_path = config_path  # type: ignore[attr-defined]
    print(f"Lab OS UI: http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    serve(Path(args.config), args.host, args.port)
