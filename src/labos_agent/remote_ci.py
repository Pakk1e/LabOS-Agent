"""GitHub Actions verification for the exact pushed commit SHA."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class RemoteCIResult:
    success: bool
    found_runs: bool
    completed: bool
    summary: str
    run_urls: tuple[str, ...] = ()


def _request(url: str) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": "LabOS-Agent",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url,headers=headers,method="GET")
    with urlopen(request,timeout=30) as response:
        return json.load(response)


def verify_github_actions(
    repository: str,
    sha: str,
    *,
    timeout_seconds: float = 1200.0,
    poll_seconds: float = 5.0,
) -> RemoteCIResult:
    """Require a completed successful push-triggered Actions run for exactly sha."""
    owner, name = repository.split("/",1)
    url = f"https://api.github.com/repos/{owner}/{name}/actions/runs?head_sha={sha}&per_page=100"
    deadline = time.monotonic() + timeout_seconds
    last_summary = "waiting for a GitHub Actions run for the exact SHA"

    while True:
        try:
            payload = _request(url)
        except (HTTPError, URLError, TimeoutError) as exc:
            last_summary = f"GitHub Actions query failed: {type(exc).__name__}: {exc}"
            if time.monotonic() >= deadline:
                return RemoteCIResult(False,False,False,last_summary)
            time.sleep(min(poll_seconds, max(0.1, deadline-time.monotonic())))
            continue

        runs = [
            run for run in payload.get("workflow_runs", [])
            if run.get("head_sha") == sha and run.get("event") == "push"
        ]
        if not runs:
            last_summary = "no push-triggered GitHub Actions run exists for the exact SHA yet"
        else:
            urls = tuple(run.get("html_url","") for run in runs if run.get("html_url"))
            incomplete = [run for run in runs if run.get("status") != "completed"]
            failures = [run for run in runs if run.get("status") == "completed" and run.get("conclusion") != "success"]
            if failures:
                details = ", ".join(
                    f"{run.get('name','workflow')}={run.get('conclusion')}"
                    for run in failures
                )
                return RemoteCIResult(False,True,True,f"exact-SHA GitHub Actions failed: {details}",urls)
            if not incomplete:
                details = ", ".join(f"{run.get('name','workflow')}=success" for run in runs)
                return RemoteCIResult(True,True,True,f"exact-SHA GitHub Actions passed: {details}",urls)
            last_summary = "exact-SHA GitHub Actions still running: " + ", ".join(
                f"{run.get('name','workflow')}={run.get('status')}" for run in incomplete
            )

        if time.monotonic() >= deadline:
            return RemoteCIResult(False,bool(runs),False,last_summary,tuple(run.get("html_url","") for run in runs if run.get("html_url")))
        time.sleep(min(poll_seconds, max(0.1, deadline-time.monotonic())))
