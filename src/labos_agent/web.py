"""Lightweight Lab OS control UI and local API."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Lock, Thread
from urllib.parse import unquote, urlparse

from .config import load_config
from .lifecycle import ProjectPhase, normalize_phase, next_phase

_PROJECT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
_REPO_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
_processes: dict[str, subprocess.Popen] = {}
_process_lock = Lock()


def _config_payload(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_config(path: Path, payload: dict) -> None:
    import yaml
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


def _run_history(config_path: Path, project: str) -> list[dict]:
    root = config_path.parent / "state" / project / "runs"
    records = []
    if not root.exists():
        return records
    for path in sorted(root.glob("*.json"), reverse=True):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return records[:50]


def _gh_json(args: list[str]):
    result = subprocess.run(
        ["gh", *args],
        cwd=Path.cwd(),
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return json.loads(result.stdout or "null")


def _create_github_repository(repository: str, visibility: str) -> None:
    if visibility not in {"private", "public"}:
        raise ValueError("repository visibility must be private or public")
    subprocess.run(
        ["gh", "repo", "create", repository, f"--{visibility}"],
        cwd=Path.cwd(), check=True, capture_output=True, text=True, timeout=30,
    )


def _bootstrap_project_documents(root: str, name: str, idea: str) -> None:
    target = Path(root).expanduser()
    docs = target / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    templates = {
        "IDEA.md": "# Project Idea\n\n## Initial idea\n\n" + (idea.strip() or "Capture the initial project idea here.") + "\n\n## Open questions\n\n- What problem are we solving?\n- Who is the intended user?\n- What is explicitly out of scope?\n",
        "PRODUCT.md": "# Product Definition\n\n_To be completed during documentation._\n",
        "REQUIREMENTS.md": "# Requirements\n\n_To be completed during documentation._\n",
        "ARCHITECTURE.md": "# Architecture\n\n_To be completed during documentation._\n",
        "DECISIONS.md": "# Architecture Decisions\n\nRecord important decisions and their rationale here.\n",
        "ROADMAP.md": "# Roadmap\n\n_To be created after requirements and architecture are agreed._\n",
    }
    for filename, content in templates.items():
        path = docs / filename
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    agents = target / "AGENTS.md"
    if not agents.exists():
        agents.write_text("# " + name + "\n\nThis repository is managed through LabOS. Follow the project lifecycle. During brainstorming and documentation, do not implement product features. Keep requirements, architecture, decisions, and roadmap synchronized with the agreed project.\n", encoding="utf-8")


def _clone_github_repository(repository: str, root: str) -> None:
    target = Path(root).expanduser()
    if target.exists():
        if not target.is_dir():
            raise ValueError("project_root exists but is not a directory")
        if any(target.iterdir()):
            raise ValueError("project_root must be empty when creating a new repository")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["gh", "repo", "clone", repository, str(target)],
        cwd=Path.cwd(), check=True, capture_output=True, text=True, timeout=120,
    )


def _ci_runs(repository: str, limit: int = 30) -> list[dict]:
    data = _gh_json([
        "run", "list", "--repo", repository, "--limit", str(min(max(limit, 1), 100)),
        "--json", "databaseId,number,status,conclusion,headSha,name,createdAt,updatedAt,url,displayTitle",
    ])
    return data if isinstance(data, list) else []


def _latest_run_events(config_path: Path, project: str, limit: int = 20) -> list[dict]:
    root = config_path.parent / "state" / project / "runs"
    if not root.exists():
        return []
    candidates = []
    for path in root.glob("*.events.jsonl"):
        try:
            candidates.append((int(path.name.removesuffix(".events.jsonl")), path))
        except ValueError:
            continue
    if not candidates:
        return []
    _, path = max(candidates, key=lambda item: item[0])
    events = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    except (OSError, ValueError):
        return []
    return events[-max(1, min(limit, 100)):]


def _run_events(config_path: Path, project: str, run_number: int) -> list[dict]:
    path = config_path.parent / "state" / project / "runs" / f"{run_number:06d}.events.jsonl"
    if not path.exists():
        return []
    events = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    except (OSError, ValueError):
        return []
    return events[-500:]


def _file_signature(path: Path):
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _latest_event_signature(config_path: Path, project: str):
    root = config_path.parent / "state" / project / "runs"
    latest = None
    if root.exists():
        for path in root.glob("*.events.jsonl"):
            signature = _file_signature(path)
            if signature is not None and (latest is None or signature > latest):
                latest = signature
    return latest


def _event_snapshot(config_path: Path) -> dict[str, tuple]:
    """Return cheap filesystem state used by the single SSE watcher thread."""
    snapshot = {"__config__": (_file_signature(config_path),)}
    try:
        config = load_config(config_path)
        names = config.projects.keys()
    except (OSError, ValueError, KeyError, TypeError):
        names = ()
    for name in names:
        snapshot[name] = (
            _file_signature(config_path.parent / "state" / name / "supervisor_state.json"),
            _file_signature(config_path.parent / "state" / name / "web-process.json"),
            _latest_event_signature(config_path, name),
        )
    return snapshot


class EventHub:
    """Broadcast project changes to all connected SSE clients."""

    def __init__(self, config_path: Path, interval: float = 0.75):
        self.config_path = config_path
        self.interval = interval
        self._clients: set[Queue] = set()
        self._lock = Lock()
        self._stop = False
        self._thread: Thread | None = None
        self._snapshot = _event_snapshot(config_path)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = False
        self._thread = Thread(target=self._watch, name="labos-web-events", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            self._offer(client, {"type": "shutdown"})
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def subscribe(self) -> Queue:
        client: Queue = Queue(maxsize=20)
        with self._lock:
            self._clients.add(client)
        return client

    def unsubscribe(self, client: Queue) -> None:
        with self._lock:
            self._clients.discard(client)

    @staticmethod
    def _offer(client: Queue, payload: dict) -> None:
        try:
            client.put_nowait(payload)
        except Full:
            try:
                client.get_nowait()
            except Empty:
                pass
            try:
                client.put_nowait(payload)
            except Full:
                pass

    def _watch(self) -> None:
        while not self._stop:
            if not self._stop:
                time.sleep(self.interval)
            current = _event_snapshot(self.config_path)
            previous = self._snapshot
            self._snapshot = current
            changed = sorted({*previous.keys(), *current.keys()} - {
                key for key in previous.keys() & current.keys()
                if previous[key] == current[key]
            })
            if not changed:
                continue
            payload = {"type": "project_changed", "projects": changed}
            with self._lock:
                clients = list(self._clients)
            for client in clients:
                self._offer(client, payload)


def _sse_event(event: str, data: dict, *, retry: int | None = None) -> bytes:
    lines = []
    if retry is not None:
        lines.append(f"retry: {retry}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def _process_record(config_path: Path, name: str) -> Path:
    return config_path.parent / "state" / name / "web-process.json"


def _persist_process(config_path: Path, name: str, process: subprocess.Popen) -> None:
    path = _process_record(config_path, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": process.pid, "project": name, "config": str(config_path)}) + "\n", encoding="utf-8")
    tmp.replace(path)


def _clear_process_record(config_path: Path, name: str) -> None:
    try:
        _process_record(config_path, name).unlink()
    except FileNotFoundError:
        pass


def _terminate_process(name: str, pid: int) -> None:
    process = _processes.get(name)
    if process is not None and process.poll() is None:
        process.terminate()
    else:
        os.kill(pid, 15)


def _process_status(config_path: Path, name: str) -> dict:
    with _process_lock:
        process = _processes.get(name)
        if process is not None:
            running = process.poll() is None
            if running:
                return {"running": True, "pid": process.pid}
            _processes.pop(name, None)
            _clear_process_record(config_path, name)
            return {"running": False, "pid": None, "returncode": process.returncode}
        try:
            record = json.loads(_process_record(config_path, name).read_text(encoding="utf-8"))
            pid = int(record["pid"])
            cmdline = Path("/proc/{}/cmdline".format(pid)).read_bytes().decode(errors="replace")
            expected = "-m\x00labos_agent.cli\x00supervise\x00{}".format(name)
            if record.get("project") == name and record.get("config") == str(config_path) and expected in cmdline:
                return {"running": True, "pid": pid}
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            pass
        _clear_process_record(config_path, name)
        return {"running": False, "pid": None}


def _ci_action(repository: str, run_id: int, action: str) -> None:
    if action not in {"rerun", "cancel"}:
        raise ValueError("unsupported CI action")
    subprocess.run(
        ["gh", "run", action, str(run_id), "--repo", repository],
        cwd=Path.cwd(), check=True, capture_output=True, text=True, timeout=20,
    )


def _ci_jobs(repository: str, run_id: int) -> list[dict]:
    data = _gh_json(["run", "view", str(run_id), "--repo", repository, "--json", "jobs"])
    return data.get("jobs", []) if isinstance(data, dict) else []


def _project_view(config_path: Path, name: str, project) -> dict:
    memory = _memory(config_path, name)
    analysis = memory.get("last_analysis") or {}
    process_status = _process_status(config_path, name)
    running = process_status["running"]
    return {
        "name": name,
        "repository": project.repository,
        "project_root": str(project.project_root),
        "project_name": project.project_name or name,
        "continuation_message": project.continuation_message,
        "initial_idea": project.initial_idea,
        "brainstorm_notes": project.brainstorm_notes,
        "project_mode": project.project_mode,
        "lifecycle_phase": project.lifecycle_phase.value,
        "lifecycle_approved": project.lifecycle_approved,
        "project_url": project.project_url,
        "new_chat_selector": project.new_chat_selector,
        "ci_stage": project.ci_stage,
        "running": running,
        "pid": process_status["pid"],
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
        "last_response": memory.get("last_response"),
        "analysis": analysis,
        "recent_events": _latest_run_events(config_path, name),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "LabOS-Web/0.3"
    protocol_version = "HTTP/1.1"

    @property
    def config_path(self) -> Path:
        return self.server.config_path  # type: ignore[attr-defined]

    def _send(self, status: int, body, content_type: str = "application/json") -> None:
        raw = body if isinstance(body, bytes) else (
            json.dumps(body, ensure_ascii=False).encode()
            if content_type == "application/json" else str(body).encode()
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

    def _project_name_from(self, suffix: str) -> str:
        prefix = "/api/projects/"
        return unquote(self.path.split("?", 1)[0].removeprefix(prefix).removesuffix(suffix)).strip("/")

    def _stream_events(self):
        hub: EventHub = self.server.event_hub  # type: ignore[attr-defined]
        client = hub.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(_sse_event("connected", {"projects": list(_event_snapshot(self.config_path).keys())}, retry=5000))
            self.wfile.flush()
            while not hub._stop:
                try:
                    payload = client.get(timeout=15)
                except Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    continue
                if payload.get("type") == "shutdown":
                    break
                self.wfile.write(_sse_event(payload["type"], payload))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            hub.unsubscribe(client)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/events":
            return self._stream_events()
        if parsed.path == "/":
            path = Path(__file__).parent / "web" / "index.html"
            return self._send(200, path.read_bytes(), "text/html; charset=utf-8")
        if parsed.path == "/api/projects":
            config = load_config(self.config_path)
            return self._send(200, {"projects": [_project_view(self.config_path, n, p) for n, p in config.projects.items()]})
        if parsed.path.startswith("/api/projects/"):
            tail = parsed.path.removeprefix("/api/projects/").strip("/")
            parts = [unquote(x) for x in tail.split("/") if x]
            name = parts[0] if parts else ""
            config = load_config(self.config_path)
            project = config.projects.get(name)
            if project is None:
                return self._send(404, {"error": "project not found"})
            if len(parts) == 2 and parts[1] == "runs":
                return self._send(200, {
                    "runs": _run_history(self.config_path, name),
                    "process": _process_status(self.config_path, name),
                })
            if len(parts) == 3 and parts[1] == "runs" and parts[2].isdigit():
                return self._send(200, {"events": _run_events(self.config_path, name, int(parts[2]))})
            if len(parts) == 2 and parts[1] == "ci":
                try:
                    return self._send(200, {"runs": _ci_runs(project.repository)})
                except Exception as exc:
                    return self._send(502, {"error": f"GitHub CLI unavailable: {exc}"})
            if len(parts) == 3 and parts[1] == "ci" and parts[2].isdigit():
                try:
                    return self._send(200, {"jobs": _ci_jobs(project.repository, int(parts[2]))})
                except Exception as exc:
                    return self._send(502, {"error": f"GitHub CLI unavailable: {exc}"})
            return self._send(200, _project_view(self.config_path, name, project))
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            body = self._json()
            if parsed.path == "/api/projects":
                return self._create_project(body)
            if parsed.path.startswith("/api/projects/") and parsed.path.endswith("/lifecycle"):
                name = unquote(parsed.path.removeprefix("/api/projects/").removesuffix("/lifecycle")).strip("/")
                return self._update_lifecycle(name, body)
            if parsed.path.startswith("/api/projects/") and parsed.path.endswith("/supervise"):
                name = unquote(parsed.path.removeprefix("/api/projects/").removesuffix("/supervise")).strip("/")
                return self._start_supervisor(name, body)
            if parsed.path.startswith("/api/projects/") and parsed.path.endswith("/stop"):
                name = unquote(parsed.path.removeprefix("/api/projects/").removesuffix("/stop")).strip("/")
                return self._stop_supervisor(name)
            if parsed.path.startswith("/api/projects/"):
                tail = parsed.path.removeprefix("/api/projects/").strip("/")
                parts = [unquote(x) for x in tail.split("/") if x]
                if len(parts) == 3 and parts[1] == "ci" and parts[2].isdigit():
                    name = parts[0]
                    action = str(body.get("action", "")).strip().lower()
                    return self._ci_action(name, int(parts[2]), action)
            return self._send(404, {"error": "not found"})
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})
        except Exception as exc:
            return self._send(500, {"error": str(exc)})

    def do_PUT(self):
        try:
            body = self._json()
            name = unquote(self.path.removeprefix("/api/projects/")).strip("/")
            return self._update_project(name, body)
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})
        except Exception as exc:
            return self._send(500, {"error": str(exc)})

    def do_DELETE(self):
        try:
            name = unquote(self.path.removeprefix("/api/projects/")).strip("/")
            return self._delete_project(name)
        except Exception as exc:
            return self._send(500, {"error": str(exc)})

    def _create_project(self, body: dict):
        name = str(body.get("name", "")).strip()
        repository = str(body.get("repository", "")).strip()
        root = str(body.get("project_root", "")).strip()
        if not _PROJECT_RE.fullmatch(name):
            return self._send(400, {"error": "invalid project name"})
        if not _REPO_RE.fullmatch(repository):
            return self._send(400, {"error": "repository must be owner/name"})
        if not root.startswith("/"):
            return self._send(400, {"error": "project_root must be an absolute path"})
        payload = _config_payload(self.config_path)
        projects = payload.setdefault("projects", {})
        if name in projects:
            return self._send(409, {"error": "project already exists"})
        create_repository = bool(body.get("create_repository", False))
        visibility = str(body.get("repository_visibility", "private")).strip().lower()
        if create_repository:
            target = Path(root).expanduser()
            if target.exists():
                if not target.is_dir():
                    return self._send(400, {"error": "project_root exists but is not a directory"})
                if any(target.iterdir()):
                    return self._send(409, {"error": "project_root must be empty when creating a new repository"})
            try:
                _create_github_repository(repository, visibility)
                _clone_github_repository(repository, root)
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or "").strip()
                return self._send(502, {"error": detail or "GitHub repository creation or clone failed"})
        _bootstrap_project_documents(root, name, str(body.get("initial_idea", "")))
        projects[name] = {
            "repository": repository,
            "project_root": root,
            "continuation_message": body.get("continuation_message") or f"Continue {name.title()}",
            "initial_idea": str(body.get("initial_idea", "")),
            "lifecycle": {
                "mode": str(body.get("project_mode", "guided")).strip().lower() or "guided",
                "phase": "BRAINSTORM" if str(body.get("project_mode", "guided")).strip().lower() == "guided" else "DOCUMENTATION",
                "approved": False,
            },
        }
        self._apply_optional(projects[name], body)
        _write_config(self.config_path, payload)
        config = load_config(self.config_path)
        return self._send(201, _project_view(self.config_path, name, config.projects[name]))

    @staticmethod
    def _apply_optional(project: dict, body: dict) -> None:
        for key in ("project_name", "project_url", "new_chat_selector", "ci_stage"):
            value = str(body.get(key, "")).strip()
            if value:
                project[key] = value
            elif key in project and key in body:
                project.pop(key, None)

    def _update_project(self, name: str, body: dict):
        if not _PROJECT_RE.fullmatch(name):
            return self._send(400, {"error": "invalid project name"})
        payload = _config_payload(self.config_path)
        projects = payload.setdefault("projects", {})
        project = projects.get(name)
        if project is None:
            return self._send(404, {"error": "project not found"})
        if _process_status(self.config_path, name)["running"]:
            return self._send(409, {"error": "stop the supervisor before editing the project"})
        if "repository" in body:
            repository = str(body["repository"]).strip()
            if not _REPO_RE.fullmatch(repository):
                return self._send(400, {"error": "repository must be owner/name"})
            project["repository"] = repository
        if "project_root" in body:
            root = str(body["project_root"]).strip()
            if not root.startswith("/"):
                return self._send(400, {"error": "project_root must be absolute"})
            project["project_root"] = root
        if "continuation_message" in body:
            project["continuation_message"] = str(body["continuation_message"]).strip()
        self._apply_optional(project, body)
        _write_config(self.config_path, payload)
        config = load_config(self.config_path)
        return self._send(200, _project_view(self.config_path, name, config.projects[name]))

    def _delete_project(self, name: str):
        payload = _config_payload(self.config_path)
        projects = payload.setdefault("projects", {})
        if name not in projects:
            return self._send(404, {"error": "project not found"})
        if _process_status(self.config_path, name)["running"]:
            return self._send(409, {"error": "stop the supervisor before archiving the project"})
        projects.pop(name)
        _write_config(self.config_path, payload)
        return self._send(200, {"archived": True, "project": name})

    def _ci_action(self, name: str, run_id: int, action: str):
        config = load_config(self.config_path)
        project = config.projects.get(name)
        if project is None:
            return self._send(404, {"error": "project not found"})
        if action not in {"rerun", "cancel"}:
            return self._send(400, {"error": "action must be rerun or cancel"})
        try:
            _ci_action(project.repository, run_id, action)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            return self._send(502, {"error": detail or f"GitHub CLI {action} failed"})
        except Exception as exc:
            return self._send(502, {"error": str(exc)})
        return self._send(202, {"action": action, "run_id": run_id, "project": name})


    def _update_lifecycle(self, name: str, body: dict):
        payload = _config_payload(self.config_path)
        projects = payload.setdefault("projects", {})
        project = projects.get(name)
        if project is None:
            return self._send(404, {"error": "project not found"})
        lifecycle = project.setdefault("lifecycle", {})
        current = normalize_phase(lifecycle.get("phase"))
        requested = body.get("phase")
        target = current if requested is None else normalize_phase(str(requested))
        approved = bool(body.get("approved", False))
        if "brainstorm_notes" in body:
            project["brainstorm_notes"] = str(body.get("brainstorm_notes", "")).strip()
        if target == current and not approved and "brainstorm_notes" not in body:
            return self._send(400, {"error": "no lifecycle change requested"})
        if target != current:
            expected = next_phase(current)
            if target != expected:
                return self._send(409, {"error": "invalid lifecycle transition"})
            lifecycle["phase"] = target.value
            lifecycle["approved"] = False
        if approved:
            if target.value != "DEVELOPMENT":
                return self._send(400, {"error": "human approval is required only for the DEVELOPMENT gate"})
            lifecycle["approved"] = True
        _write_config(self.config_path, payload)
        config = load_config(self.config_path)
        return self._send(200, _project_view(self.config_path, name, config.projects[name]))
    def _stop_supervisor(self, name: str):
        status = _process_status(self.config_path, name)
        if not status["running"]:
            return self._send(409, {"error": "supervisor is not running"})
        pid = status["pid"]
        with _process_lock:
            _terminate_process(name, pid)
            _processes.pop(name, None)
            _clear_process_record(self.config_path, name)
        return self._send(202, {"stopped": True, "project": name, "pid": pid})

    def _start_supervisor(self, name: str, body: dict):
        config = load_config(self.config_path)
        if name not in config.projects:
            return self._send(404, {"error": "project not found"})
        existing_status = _process_status(self.config_path, name)
        if existing_status["running"]:
            return self._send(409, {"error": "supervisor already running", "pid": existing_status["pid"]})
        with _process_lock:
            max_turns = int(body.get("max_turns", 0))
            if max_turns < 0 or max_turns > 1000:
                return self._send(400, {"error": "max_turns must be between 0 and 1000"})
            cmd = [sys.executable, "-m", "labos_agent.cli", "supervise", name,
                   "--config", str(self.config_path), "--max-turns", str(max_turns)]
            process = subprocess.Popen(cmd, cwd=str(self.config_path.parent))
            _processes[name] = process
            _persist_process(self.config_path, name, process)
        return self._send(202, {"started": True, "pid": process.pid, "project": name, "max_turns": max_turns})


def serve(config_path: Path, host: str = "127.0.0.1", port: int = 8080) -> None:
    config_path = config_path.expanduser().resolve()
    load_config(config_path)
    server = ThreadingHTTPServer((host, port), Handler)
    server.config_path = config_path  # type: ignore[attr-defined]
    server.event_hub = EventHub(config_path)  # type: ignore[attr-defined]
    server.event_hub.start()  # type: ignore[attr-defined]
    print(f"Lab OS UI: http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.event_hub.stop()  # type: ignore[attr-defined]
        server.server_close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    serve(Path(args.config), args.host, args.port)
