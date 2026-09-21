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
    state_files: tuple[str, ...] = ("AGENTS.md","PLAN.md","TASKS.md","DECISIONS.md","progress.md","report.md")

@dataclass(frozen=True)
class AppConfig:
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    projects: dict[str, ProjectConfig] = field(default_factory=dict)

def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    br = raw.get("browser", {})
    browser = BrowserConfig(
        cdp_url=br.get("cdp_url","http://127.0.0.1:9222"),
        profile_dir=Path(br.get("profile_dir","./browser-profile")).expanduser(),
        response_timeout_seconds=float(br.get("response_timeout_seconds",300)),
        quiet_seconds=float(br.get("quiet_seconds",3)),
    )
    projects={}
    for key,value in raw.get("projects",{}).items():
        projects[key]=ProjectConfig(
            name=key, repository=value["repository"],
            project_root=Path(value["project_root"]).expanduser(),
            continuation_message=value.get("continuation_message",f"Continue {key.title()}"),
            project_name=value.get("project_name"),
            project_url=value.get("project_url"),
            new_chat_selector=value.get("new_chat_selector"),
            rollover_after_iterations=int(value.get("rollover_after_iterations",20)),
            rollover_after_response_chars=int(value.get("rollover_after_response_chars",120000)),
            state_files=tuple(value.get("state_files",ProjectConfig.state_files)),
        )
    return AppConfig(browser=browser,projects=projects)
