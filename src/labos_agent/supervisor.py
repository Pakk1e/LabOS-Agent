"""Minimal ChatGPT conversation supervisor.

The supervisor does not execute repository commands. ChatGPT remains responsible
for the coding work. LabOS only drives the conversation, parses the final state
marker, and watches GitHub Actions when requested.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .browser.chatgpt import ChatGPTPage
from .browser.session import BrowserSession
from .config import AppConfig, ProjectConfig
from .state_protocol import ChatState, STATE_INSTRUCTION, parse_state
from .run_summary import RunTracker
from .trace import trace, trace_summary


_RUN_NUMBER = 0


def _next_run_number() -> int:
    global _RUN_NUMBER
    _RUN_NUMBER += 1
    return _RUN_NUMBER


REMIND_MESSAGE = (
    "[LAB OS — STATE REMINDER]\n"
    "Continue your work. Your response did not end with a valid state marker. "
    "Do not forget to always reply with (STATE <state> STATE) at the very end "
    "of every response. Use only CONTINUE, WAIT_CI, FIX_CI, or DONE."
)

CONTINUE_MESSAGE = (
    "[LAB OS — CONTINUE]\n"
    "Continue the implementation from the current repository state. Do the actual "
    "repository work rather than only describing what should be changed. Run the "
    "relevant tests, and if the change is ready for validation, commit and push it "
    "so GitHub Actions can run. Remember to finish your response with "
    "(STATE <state> STATE)."
)

CI_PASSED_MESSAGE = (
    "[LAB OS — CI RESULT]\n"
    "GitHub Actions CI has passed for this repository. The CI result has already "
    "been checked and reported to you. Continue the actual engineering work now. "
    "Do not request WAIT_CI again for this same CI result. Use WAIT_CI only when "
    "you have a newly pushed commit or are genuinely waiting for a newly started "
    "CI run. Otherwise make the next useful change or, if the requested work is "
    "genuinely complete and validated, use DONE. Remember to finish your response "
    "with (STATE <state> STATE)."
)

CI_FAILED_MESSAGE = (
    "[LAB OS — CI FAILED]\n"
    "GitHub Actions CI failed for this repository. Inspect the actual CI failure, "
    "fix the problem in the repository, run relevant tests, then commit and push "
    "the fix so GitHub Actions can validate it again. Remember to finish your "
    "response with (STATE <state> STATE)."
)


@dataclass(frozen=True)
class CIRun:
    id: int
    sha: str
    status: str
    conclusion: str | None
    created_at: str
    url: str
    name: str


class SupervisorError(RuntimeError):
    pass


def _github_get(repository: str, *, per_page: int = 30) -> list[CIRun]:
    owner, name = repository.split("/", 1)
    url = (
        f"https://api.github.com/repos/{owner}/{name}/actions/runs"
        f"?per_page={per_page}"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": "LabOS-Agent",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        try:
            token = subprocess.run(
                ["gh", "auth", "token"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            token = ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    return tuple(
        CIRun(
            id=int(run["id"]),
            sha=str(run.get("head_sha", "")),
            status=str(run.get("status", "")),
            conclusion=run.get("conclusion"),
            created_at=str(run.get("created_at", "")),
            url=str(run.get("html_url", "")),
            name=str(run.get("name", "workflow")),
        )
        for run in payload.get("workflow_runs", [])
    )


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _runs_relevant_to_wait(runs: list[CIRun], started_at: datetime) -> list[CIRun]:
    # Include runs created shortly before the state was observed so a fast
    # ChatGPT response cannot race the Actions API timestamp by a few seconds.
    floor = started_at.timestamp() - 120
    return [
        run for run in runs
        if (_parse_time(run.created_at) or datetime.fromtimestamp(0, timezone.utc)).timestamp() >= floor
    ]


def wait_for_ci(
    repository: str,
    started_at: datetime,
    *,
    baseline_run_ids: set[int] | None = None,
    timeout_seconds: float,
    poll_seconds: float,
    request_fn=_github_get,
    sleep_fn=time.sleep,
) -> tuple[bool, str]:
    """Wait for a relevant Actions run and return (passed, summary)."""
    deadline = time.monotonic() + timeout_seconds
    baseline = baseline_run_ids or set()
    last = "waiting for a GitHub Actions run"

    while True:
        try:
            all_runs = list(request_fn(repository))
            runs = [
                run for run in _runs_relevant_to_wait(all_runs, started_at)
                if run.id not in baseline
            ]
            # ChatGPT may request WAIT_CI before pushing a new commit. Report
            # the latest existing run rather than waiting for a nonexistent
            # turn-specific run.
            if not runs and baseline:
                existing = sorted(
                    all_runs,
                    key=lambda run: _parse_time(run.created_at)
                    or datetime.fromtimestamp(0, timezone.utc),
                    reverse=True,
                )
                if existing:
                    runs = [existing[0]]
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            last = f"GitHub Actions query failed: {type(exc).__name__}: {exc}"
            runs = []

        failures = [
            run for run in runs
            if run.status == "completed" and run.conclusion != "success"
        ]
        if failures:
            details = ", ".join(f"{run.name}={run.conclusion}" for run in failures)
            return False, f"GitHub CI failed: {details}"

        completed = [run for run in runs if run.status == "completed"]
        running = [run for run in runs if run.status != "completed"]
        if completed and not running:
            details = ", ".join(f"{run.name}=success" for run in completed)
            return True, f"GitHub CI passed: {details}"
        if running:
            last = "GitHub CI still running: " + ", ".join(
                f"{run.name}={run.status}" for run in running
            )

        if time.monotonic() >= deadline:
            return False, last + "; timeout reached"
        sleep_fn(min(poll_seconds, max(0.1, deadline - time.monotonic())))


class ConversationSupervisor:
    def __init__(
        self,
        config: AppConfig,
        project_name: str,
        *,
        max_turns: int = 0,
        ci_timeout_seconds: float | None = None,
        ci_poll_seconds: float | None = None,
    ):
        project = config.projects.get(project_name)
        if project is None:
            raise SupervisorError(f"unknown project: {project_name}")
        self.config = config
        self.project: ProjectConfig = project
        self.project_name = project_name
        self.max_turns = max_turns
        self.ci_timeout_seconds = (
            ci_timeout_seconds
            if ci_timeout_seconds is not None
            else project.remote_ci_timeout_seconds
        )
        self.ci_poll_seconds = (
            ci_poll_seconds
            if ci_poll_seconds is not None
            else project.remote_ci_poll_seconds
        )
        self.turns = 0

    def _bootstrap_prompt(self) -> str:
        project_name = self.project.project_name or self.project_name
        repository = self.project.repository
        project_root = str(self.project.project_root)
        task = self.project.continuation_message.strip()

        return f"""You are the coding agent for this LabOS session.

PROJECT:
{project_name}

GITHUB REPOSITORY:
{repository}

LOCAL REPOSITORY:
{project_root}

ROLE AND OPERATING MODEL:
- You are responsible for the actual engineering work in this repository.
- LabOS is supervising this conversation and observing GitHub Actions.
- LabOS does not execute shell commands or file edits on your behalf.
- Do not merely describe a change that should be made. Perform the actual work
  using the repository/project tools available in this conversation.
- Treat the repository's current state as authoritative.

REPOSITORY SAFETY:
- First inspect the current repository state and understand the existing
  implementation before changing anything.
- Work only in the repository identified above.
- Do not reset, clean, stash, discard, or overwrite existing work unless the
  task explicitly requires it.
- Preserve intentional existing changes.

IMPLEMENTATION AND CI WORKFLOW:
1. Inspect the current state and continue the requested task.
2. Make the actual implementation changes.
3. Run the relevant local tests/checks when available.
4. When a meaningful change is ready for validation, commit it and push it to
   {repository} so GitHub Actions can validate the pushed commit.
5. Do not claim that a file was changed, a commit was created, a push happened,
   or CI passed unless you actually performed/observed that action.
6. When CI fails, inspect the real failure, fix it, commit/push the fix, and
   wait for CI again.
7. When the requested work is genuinely complete and validated, use DONE.

CURRENT TASK:
{task}

Start now by inspecting the current repository state and continue the task.
{STATE_INSTRUCTION}
"""

    def _prompt(self, message: str) -> str:
        return message.rstrip() + "\n\n" + STATE_INSTRUCTION

    def run(self) -> str:
        run_started_at = datetime.now(timezone.utc)
        tracker = RunTracker(self.project_name, _next_run_number(), run_started_at)
        trace("run.start", project=self.project_name, run=tracker.summary.run_number)
        with BrowserSession(
            self.config.browser.profile_dir,
            cdp_url=self.config.browser.cdp_url,
        ) as session:
            page = ChatGPTPage.select_page(
                session.context,
                project_name=self.project.project_name,
                project_url=self.project.project_url,
            )
            chat = ChatGPTPage(page)
            status = chat.status()
            if status.is_challenge:
                raise SupervisorError("ChatGPT verification challenge is active")
            if not status.is_chatgpt or not status.has_input:
                raise SupervisorError("an active ChatGPT conversation with an input is required")
            if self.project.project_name and not chat.project_context_present(self.project.project_name):
                raise SupervisorError(
                    f"required ChatGPT Project context not detected: {self.project.project_name}"
                )

            try:
                ci_baseline = set(run.id for run in _github_get(self.project.repository))
            except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                raise SupervisorError(
                    "cannot access GitHub Actions for the configured repository; "
                    "set GITHUB_TOKEN/GH_TOKEN or authenticate GitHub CLI with 'gh auth login'"
                ) from exc
            response = chat.send_and_wait_for_response(
                self._bootstrap_prompt(),
                timeout_seconds=self.config.browser.response_timeout_seconds,
                quiet_seconds=self.config.browser.quiet_seconds,
            )

            tracker.start_iteration(run_started_at)
            while True:
                self.turns += 1
                iteration = tracker.iteration
                tracker.record(chatgpt_work=True)
                trace("iteration.start", project=self.project_name, iteration=iteration.number)
                trace("chat.response", project=self.project_name, iteration=iteration.number, response_chars=len(response))
                state = parse_state(response)
                tracker.record(state=state.value if state else "INVALID")

                if state is None:
                    response = chat.send_and_wait_for_response(
                        REMIND_MESSAGE,
                        timeout_seconds=self.config.browser.response_timeout_seconds,
                        quiet_seconds=self.config.browser.quiet_seconds,
                    )
                elif state == ChatState.DONE:
                    tracker.finish_iteration("DONE", datetime.now(timezone.utc))
                    tracker.finish_run("DONE", datetime.now(timezone.utc))
                    trace("iteration.complete", project=self.project_name, iteration=iteration.number, success=True, state="DONE")
                    trace_summary(tracker.box(), project=self.project_name, success=True)
                    trace("run.complete", project=self.project_name, run=tracker.summary.run_number, success=True)
                    return response
                elif state == ChatState.WAIT_CI:
                    trace("remote_ci.start", project=self.project_name, iteration=iteration.number)
                    passed, summary = wait_for_ci(
                        self.project.repository,
                        datetime.now(timezone.utc),
                        baseline_run_ids=ci_baseline,
                        timeout_seconds=self.ci_timeout_seconds,
                        poll_seconds=self.ci_poll_seconds,
                    )
                    tracker.record(
                        remote_ci_passed=passed,
                        remote_ci_failed=not passed,
                        reason=None if passed else summary,
                    )
                    ci_baseline = set(run.id for run in _github_get(self.project.repository))
                    tracker.finish_iteration("WAIT_CI", datetime.now(timezone.utc), None if passed else summary)
                    trace(
                        "remote_ci.complete" if passed else "remote_ci.error",
                        project=self.project_name,
                        iteration=iteration.number,
                        success=passed,
                        summary=summary,
                    )
                    trace("iteration.complete", project=self.project_name, iteration=iteration.number, success=passed, state="WAIT_CI")
                    trace_summary(tracker.box(), project=self.project_name, success=passed)
                    response = chat.send_and_wait_for_response(
                        CI_PASSED_MESSAGE if passed else CI_FAILED_MESSAGE,
                        timeout_seconds=self.config.browser.response_timeout_seconds,
                        quiet_seconds=self.config.browser.quiet_seconds,
                    )
                    tracker.start_iteration(datetime.now(timezone.utc))
                elif state == ChatState.FIX_CI:
                    tracker.finish_iteration("FIX_CI", datetime.now(timezone.utc))
                    trace("iteration.complete", project=self.project_name, iteration=iteration.number, success=false, state="FIX_CI")
                    response = chat.send_and_wait_for_response(
                        CI_FAILED_MESSAGE,
                        timeout_seconds=self.config.browser.response_timeout_seconds,
                        quiet_seconds=self.config.browser.quiet_seconds,
                    )
                    tracker.start_iteration(datetime.now(timezone.utc))
                else:
                    tracker.finish_iteration("CONTINUE", datetime.now(timezone.utc))
                    trace("iteration.complete", project=self.project_name, iteration=iteration.number, success=True, state="CONTINUE")
                    response = chat.send_and_wait_for_response(
                        CONTINUE_MESSAGE,
                        timeout_seconds=self.config.browser.response_timeout_seconds,
                        quiet_seconds=self.config.browser.quiet_seconds,
                    )
                    tracker.start_iteration(datetime.now(timezone.utc))

                if self.max_turns and self.turns >= self.max_turns:
                    tracker.finish_run("STOPPED", datetime.now(timezone.utc))
                    trace_summary(tracker.box(), project=self.project_name)
                    trace("run.stop", project=self.project_name, run=tracker.summary.run_number)
                    return response
