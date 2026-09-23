"""Configuration for LabOS-Agent projects and browser control."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import yaml

@dataclass(frozen=True)
class BrowserConfig:
    cdp_url: str = "http://127.0.0.1:9222"
    profile_dir: Path = Path("./browser-profile")
    response_timeout_seconds: float = 300.0
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
    ci_timeout_seconds: float = 1800.0
    ci_stage: str | None = None
    ci_stages: dict[str, tuple[tuple[str, ...], ...]] = field(default_factory=dict)
    state_files: tuple[str, ...] = ("AGENTS.md","PLAN.md","TASKS.md","DECISIONS.md","progress.md","report.md")
    execution_enabled: bool = True
    execution_allowed_roots: tuple[Path, ...] = ()
    execution_command_timeout_seconds: float = 300.0

@dataclass(frozen=True)
class AppConfig:
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    projects: dict[str, ProjectConfig] = field(default_factory=dict)

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

def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    br = raw.get("browser", {})
    browser = BrowserConfig(
        cdp_url=br.get("cdp_url","http://127.0.0.1:9222"),
        profile_dir=Path(br.get("profile_dir","./browser-profile")).expanduser(),
        response_timeout_seconds=float(br.get("response_timeout_seconds",300)),
        quiet_seconds=float(br.get("quiet_seconds",3)),
    )
    projects = {}
    for key, value in raw.get("projects", {}).items():
        root = Path(value["project_root"]).expanduser()
        allowed = tuple(Path(p).expanduser() for p in value.get("execution", {}).get("allowed_roots", [str(root)]))
        projects[key] = ProjectConfig(
            name=key, repository=value["repository"], project_root=root,
            continuation_message=value.get("continuation_message",f"Continue {key.title()}"),
            project_name=value.get("project_name"), project_url=value.get("project_url"),
            new_chat_selector=value.get("new_chat_selector"),
            rollover_after_iterations=int(value.get("rollover_after_iterations",20)),
            rollover_after_response_chars=int(value.get("rollover_after_response_chars",120000)),
            ci_timeout_seconds=float(value.get("ci_timeout_seconds",1800)),
            ci_stage=value.get("ci_stage"), ci_stages=_parse_ci_stages(value.get("ci")),
            state_files=tuple(value.get("state_files",ProjectConfig.state_files)),
            execution_enabled=bool(value.get("execution",{}).get("enabled",True)),
            execution_allowed_roots=allowed,
            execution_command_timeout_seconds=float(value.get("execution",{}).get("command_timeout_seconds",300)),
        )
    return AppConfig(browser=browser, projects=projects)
