"""Configuration for LabOS-Agent projects and browser control."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import yaml

from .lifecycle import ProjectPhase, load_lifecycle_state, normalize_phase, normalize_project_mode

@dataclass(frozen=True)
class BrowserConfig:
    cdp_url: str = "http://127.0.0.1:9222"
    profile_dir: Path = Path("./browser-profile")
    response_timeout_seconds: float = 1200.0
    quiet_seconds: float = 3.0

@dataclass(frozen=True)
class ProjectConfig:
    name: str
    repository: str
    project_root: Path
    continuation_message: str
    project_name: str | None = None
    project_url: str | None = None
    new_chat_selector: str | None = None
    rollover_after_iterations: int = 20
    rollover_after_response_chars: int = 120_000
    min_fresh_chat_delay_seconds: float = 420.0
    response_to_next_message_delay_seconds: float = 45.0
    max_no_progress_iterations: int = 5
    max_turns: int = 0
    response_timeout_seconds: float = 1200.0
    quiet_seconds: float = 3.0
    ci_timeout_seconds: float = 1800.0
    ci_stage: str | None = None
    ci_stages: dict[str, tuple[tuple[str, ...], ...]] = field(default_factory=dict)
    state_files: tuple[str, ...] = ("AGENTS.md","PLAN.md","TASKS.md","DECISIONS.md","progress.md","report.md")
    execution_enabled: bool = False
    execution_allowed_roots: tuple[Path, ...] = ()
    execution_command_timeout_seconds: float = 300.0
    remote_ci_timeout_seconds: float = 1200.0
    remote_ci_poll_seconds: float = 5.0
    lifecycle_phase: ProjectPhase = ProjectPhase.IDEA
    lifecycle_approved: bool = False
    lifecycle_approved_at: str | None = None
    project_mode: str = "guided"
    manual_lifecycle_advance_enabled: bool = True
    initial_idea: str = ""
    brainstorm_notes: str = ""

@dataclass(frozen=True)
class AppConfig:
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    projects: dict[str, ProjectConfig] = field(default_factory=dict)
    state_root: Path = Path("state")

def _parse_ci_stages(raw: object) -> dict[str, tuple[tuple[str, ...], ...]]:
    if raw is None: return {}
    if not isinstance(raw, dict): raise ValueError("ci must be a mapping of stage names to command lists")
    stages = {}
    for stage, commands in raw.items():
        if not isinstance(stage, str) or not isinstance(commands, list):
            raise ValueError("each ci stage must contain a list of argv commands")
        parsed = []
        for command in commands:
            if not isinstance(command, list) or not command or not all(isinstance(part, str) for part in command):
                raise ValueError(f"ci.{stage} commands must be non-empty argv lists of strings")
            parsed.append(tuple(command))
        stages[stage] = tuple(parsed)
    return stages

def _raise_invalid_manual_advance(project: str) -> bool:
    raise ValueError(
        f"lifecycle.manual_lifecycle_advance_enabled for project {project} must be a boolean"
    )

def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    br = raw.get("browser", {})
    browser = BrowserConfig(
        cdp_url=br.get("cdp_url","http://127.0.0.1:9222"),
        profile_dir=Path(br.get("profile_dir","./browser-profile")).expanduser(),
        response_timeout_seconds=float(br.get("response_timeout_seconds",1200)),
        quiet_seconds=float(br.get("quiet_seconds",3)),
    )
    projects = {}
    for key, value in raw.get("projects", {}).items():
        root = Path(value["project_root"]).expanduser()
        allowed = tuple(Path(p).expanduser() for p in value.get("execution", {}).get("allowed_roots", [str(root)]))
        lifecycle = value.get("lifecycle", {})
        legacy_phase = normalize_phase(lifecycle.get("phase"))
        legacy_approved = lifecycle.get("approved", False)
        if not isinstance(legacy_approved, bool):
            raise ValueError(f"lifecycle.approved for project {key} must be a boolean")
        legacy_approved_at = lifecycle.get("approved_at")
        if legacy_approved_at is not None and not isinstance(legacy_approved_at, str):
            raise ValueError(f"lifecycle.approved_at for project {key} must be a string or null")
        lifecycle_state = load_lifecycle_state(
            path.parent / "state",
            key,
            fallback_phase=legacy_phase,
            fallback_approved=legacy_approved,
            fallback_approved_at=legacy_approved_at,
        )
        projects[key] = ProjectConfig(
            name=key, repository=value["repository"], project_root=root,
            continuation_message=value.get("continuation_message",f"Continue {key.title()}"),
            project_name=value.get("project_name"), project_url=value.get("project_url"),
            new_chat_selector=value.get("new_chat_selector"),
            rollover_after_iterations=int(value.get("rollover_after_iterations",20)),
            rollover_after_response_chars=int(value.get("rollover_after_response_chars",120000)),
            min_fresh_chat_delay_seconds=float(value.get("min_fresh_chat_delay_seconds",420)),
            response_to_next_message_delay_seconds=float(value.get("response_to_next_message_delay_seconds",45)),
            max_no_progress_iterations=int(value.get("max_no_progress_iterations",5)),
            max_turns=int(value.get("max_turns",0)),
            response_timeout_seconds=float(value.get("response_timeout_seconds",1200)),
            quiet_seconds=float(value.get("quiet_seconds",3)),
            ci_timeout_seconds=float(value.get("ci_timeout_seconds",1800)),
            ci_stage=value.get("ci_stage"), ci_stages=_parse_ci_stages(value.get("ci")),
            state_files=tuple(value.get("state_files",ProjectConfig.state_files)),
            execution_enabled=bool(value.get("execution",{}).get("enabled",False)),
            execution_allowed_roots=allowed,
            execution_command_timeout_seconds=float(value.get("execution",{}).get("command_timeout_seconds",300)),
            remote_ci_timeout_seconds=float(value.get("remote_ci",{}).get("timeout_seconds",1200)),
            remote_ci_poll_seconds=float(value.get("remote_ci",{}).get("poll_seconds",5)),
            lifecycle_phase=lifecycle_state.phase,
            lifecycle_approved=lifecycle_state.approved,
            lifecycle_approved_at=lifecycle_state.approved_at,
            project_mode=normalize_project_mode(lifecycle.get("mode", "guided")),
            manual_lifecycle_advance_enabled=(
                lifecycle.get("manual_lifecycle_advance_enabled", True)
                if isinstance(lifecycle.get("manual_lifecycle_advance_enabled", True), bool)
                else (_raise_invalid_manual_advance(key))
            ),
            initial_idea=str(value.get("initial_idea", "")),
            brainstorm_notes=str(value.get("brainstorm_notes", "")),
        )
    return AppConfig(browser=browser, projects=projects, state_root=path.parent / "state")
