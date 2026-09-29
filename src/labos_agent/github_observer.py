"""Read-only GitHub observations used by the supervisor state machine."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class GitHubObservation:
    branch: str
    commit_sha: str
    ci_run_id: int | None
    ci_status: str | None
    ci_conclusion: str | None
    ci_sha: str | None
    ci_name: str | None
    ci_created_at: str | None
    ci_url: str | None


def _gh_auth_token() -> str:
    try:
        return subprocess.run(
            ["gh", "auth", "token"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""


def _headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": "LabOS-Agent",
    }
    if token is None:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or _gh_auth_token()
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def _get_json(url: str) -> dict:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    request = Request(url, headers=_headers(token), method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        # A stale/under-scoped environment token can make a private repository
        # look like it does not exist. Retry once with the authenticated gh
        # account, which is the same fallback used elsewhere by LabOS.
        if exc.code not in (401, 403, 404):
            raise
        gh_token = _gh_auth_token()
        if not gh_token or gh_token == token:
            raise
        request = Request(url, headers=_headers(gh_token), method="GET")
        with urlopen(request, timeout=30) as response:
            return json.load(response)


def observe_github(repository: str, *, ci_run_id: int | None = None) -> GitHubObservation:
    owner, name = repository.split("/", 1)
    repo = _get_json(f"https://api.github.com/repos/{owner}/{name}")
    branch = str(repo["default_branch"])
    ref = _get_json(f"https://api.github.com/repos/{owner}/{name}/git/ref/heads/{branch}")
    commit_sha = str(ref["object"]["sha"])
    if ci_run_id is not None:
        latest = _get_json(
            f"https://api.github.com/repos/{owner}/{name}/actions/runs/{ci_run_id}"
        )
    else:
        runs = _get_json(
            f"https://api.github.com/repos/{owner}/{name}/actions/runs?branch={branch}&per_page=20"
        ).get("workflow_runs", [])
        latest = runs[0] if runs else {}
    return GitHubObservation(
        branch=branch,
        commit_sha=commit_sha,
        ci_run_id=int(latest["id"]) if latest.get("id") is not None else None,
        ci_status=str(latest["status"]) if latest.get("status") is not None else None,
        ci_conclusion=str(latest["conclusion"]) if latest.get("conclusion") is not None else None,
        ci_sha=str(latest["head_sha"]) if latest.get("head_sha") else None,
        ci_name=str(latest["name"]) if latest.get("name") else None,
        ci_created_at=str(latest["created_at"]) if latest.get("created_at") else None,
        ci_url=str(latest["html_url"]) if latest.get("html_url") else None,
    )
