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
        if exc.code not in (401, 403, 404):
            raise
        gh_token = _gh_auth_token()
        if not gh_token or gh_token == token:
            raise
        request = Request(url, headers=_headers(gh_token), method="GET")
        with urlopen(request, timeout=30) as response:
            return json.load(response)


def _gh_run_json(
    repository: str,
    ci_run_id: int | None = None,
    *,
    head_sha: str | None = None,
) -> dict | None:
    fields = "databaseId,number,status,conclusion,headSha,name,createdAt,url"
    command = [
        "gh", "run", "list", "-R", repository,
        "--limit", "20" if ci_run_id is None else "100",
        "--json", fields,
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    runs = json.loads(result.stdout)
    if ci_run_id is None:
        return runs[0] if runs else None
    # LABOS_STATE.CI_RUN is the workflow run number, not GitHub's database ID.
    matches = [
        run for run in runs
        if run.get("number") == ci_run_id
        and (head_sha is None or run.get("headSha") == head_sha)
    ]
    return matches[0] if matches else None


def observe_github(repository: str, *, ci_run_id: int | None = None) -> GitHubObservation:
    owner, name = repository.split("/", 1)
    repo = _get_json(f"https://api.github.com/repos/{owner}/{name}")
    branch = str(repo["default_branch"])
    ref = _get_json(f"https://api.github.com/repos/{owner}/{name}/git/ref/heads/{branch}")
    commit_sha = str(ref["object"]["sha"])

    latest = _gh_run_json(repository, ci_run_id, head_sha=commit_sha)
    return GitHubObservation(
        branch=branch,
        commit_sha=commit_sha,
        ci_run_id=int(latest["databaseId"]) if latest and latest.get("databaseId") is not None else None,
        ci_status=str(latest["status"]) if latest and latest.get("status") is not None else None,
        ci_conclusion=str(latest["conclusion"]) if latest and latest.get("conclusion") is not None else None,
        ci_sha=str(latest["headSha"]) if latest and latest.get("headSha") else None,
        ci_name=str(latest["name"]) if latest and latest.get("name") else None,
        ci_created_at=str(latest["createdAt"]) if latest and latest.get("createdAt") else None,
        ci_url=str(latest["url"]) if latest and latest.get("url") else None,
    )
