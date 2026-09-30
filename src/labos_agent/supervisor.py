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
import fcntl
from pathlib import Path
from contextlib import contextmanager
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .browser.chatgpt import ChatGPTPage
from .browser.session import BrowserSession
from .config import AppConfig, ProjectConfig
from .state_protocol import ChatState
from .response_protocol import LabOSResponse, STATE_BLOCK_INSTRUCTION, parse_labos_response
from .run_summary import RunTracker
from .github_observer import GitHubObservation, observe_github
from .supervisor_memory import SupervisorMemory, load_memory, memory_path, save_memory
from .supervisor_state_machine import reconcile
from .trace import trace, trace_summary
from .lifecycle import (
    LifecycleState,
    can_advance,
    can_start_supervisor,
    next_phase,
    phase_instruction,
    save_lifecycle_state,
)

def _save_run_summary(project: str, tracker: RunTracker) -> None:
    path = Path("state") / project / "runs" / f"{tracker.summary.run_number:06d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(tracker.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)

def _save_run_event(project: str, tracker: RunTracker, event: str, **fields: object) -> None:
    path = Path("state") / project / "runs" / f"{tracker.summary.run_number:06d}.events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "run": tracker.summary.run_number,
        "project": project,
        **fields,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


_RUN_NUMBER = 0


def _next_run_number(project: str) -> int:
    """Return a run number that survives supervisor process restarts."""
    global _RUN_NUMBER
    root = Path("state") / project / "runs"
    existing = []
    if root.exists():
        for path in root.iterdir():
            if path.suffix == ".json":
                candidate = path.stem
            elif path.name.endswith(".events.jsonl"):
                candidate = path.name.removesuffix(".events.jsonl")
            else:
                continue
            try:
                existing.append(int(candidate))
            except ValueError:
                continue
    _RUN_NUMBER = max(_RUN_NUMBER, max(existing, default=0)) + 1
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


REMIND_MESSAGE += "\n\n" + STATE_BLOCK_INSTRUCTION
CONTINUE_MESSAGE += "\n\n" + STATE_BLOCK_INSTRUCTION
CI_PASSED_MESSAGE += "\n\n" + STATE_BLOCK_INSTRUCTION
CI_FAILED_MESSAGE += "\n\n" + STATE_BLOCK_INSTRUCTION

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


@contextmanager
def _project_execution_lock(project: str):
    """Allow one supervisor process per project while permitting other projects to run."""
    path = Path("state") / project / "supervisor.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SupervisorError(f"supervisor already running for project: {project}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _github_get(repository: str, *, per_page: int = 30) -> list[CIRun]:
    """Read Actions runs using the authenticated gh CLI."""
    result = subprocess.run(
        [
            "gh", "run", "list", "-R", repository,
            "--limit", str(per_page),
            "--json", "databaseId,status,conclusion,headSha,name,createdAt,url",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    runs = json.loads(result.stdout)
    return tuple(
        CIRun(
            id=int(run["databaseId"]),
            sha=str(run.get("headSha", "")),
            status=str(run.get("status", "")),
            conclusion=run.get("conclusion"),
            created_at=str(run.get("createdAt", "")),
            url=str(run.get("url", "")),
            name=str(run.get("name", "workflow")),
        )
        for run in runs
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
    target_sha: str | None = None,
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
                and (target_sha is None or run.sha == target_sha)
            ]
            # ChatGPT may request WAIT_CI before pushing a new commit. Report
            # the latest existing run rather than waiting for a nonexistent
            # turn-specific run.
            if not runs and baseline and target_sha is None:
                existing = sorted(
                    all_runs,
                    key=lambda run: _parse_time(run.created_at)
                    or datetime.fromtimestamp(0, timezone.utc),
                    reverse=True,
                )
                if existing:
                    runs = [existing[0]]
        except (HTTPError, URLError, TimeoutError, ValueError, subprocess.CalledProcessError, OSError) as exc:
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


def _start_fresh_chat(chat: ChatGPTPage, project: ProjectConfig) -> None:
    """Start a fresh ChatGPT conversation for a new supervisor run."""
    if project.project_name:
        chat.start_new_project_chat(
            project_name=project.project_name,
            project_url=project.project_url,
            selector=project.new_chat_selector,
        )
        if not chat.project_context_present(project.project_name):
            raise SupervisorError(
                f"required ChatGPT Project context not detected after new chat: {project.project_name}"
            )
        chat.assert_ready()
    else:
        chat.assert_ready()


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
        self.current_phase = project.lifecycle_phase

    def _lifecycle_context(self) -> str:
        phase = self.current_phase
        return (
            f"PROJECT LIFECYCLE PHASE: {phase.value}\n"
            f"PROJECT MODE: {self.project.project_mode}\n"
            "HUMAN APPROVAL: NOT REQUIRED\n"
            f"PHASE RULE: {phase_instruction(phase)}\n"
            "LIFECYCLE RULE: complete the current phase when its repository evidence "
            "is ready; LabOS will automatically start the next phase in a fresh Project chat."
        )

    def _recovery_context(self, memory: SupervisorMemory, observation: GitHubObservation) -> str:
        if not memory.last_analysis:
            return (
                "No previous structured supervisor state exists. Treat this as a fresh run "
                "and inspect the repository before making changes."
            )
        previous = memory.last_analysis
        lines = [
            "RECOVERED SUPERVISOR STATE:",
            f"- Previous reported state: {previous.get('state', 'UNKNOWN')}",
            f"- Previous reported commit: {previous.get('current_commit', 'UNKNOWN')}",
            f"- Previous reported CI: {previous.get('ci_run', 'NONE')} / {previous.get('ci_status', 'NONE')}",
            f"- GitHub observed branch: {observation.branch}",
            f"- GitHub observed HEAD: {observation.commit_sha}",
            f"- Latest GitHub CI: {observation.ci_run_id or 'NONE'} / {observation.ci_status or 'NONE'} / {observation.ci_conclusion or 'NONE'}",
        ]
        if previous.get("current_commit") and previous.get("current_commit") != observation.commit_sha:
            lines.append("RECONCILIATION: the reported commit does not match the current GitHub HEAD; inspect before assuming completion.")
        elif previous.get("current_commit"):
            lines.append("RECONCILIATION: the reported commit matches the current GitHub HEAD.")
        if previous.get("state") == "WAIT_CI" and observation.ci_conclusion == "success" and observation.ci_sha == observation.commit_sha:
            lines.append("RECOVERY ACTION: the previously awaited CI is now successful; continue the engineering task without repeating the completed CI wait.")
        elif previous.get("state") == "WAIT_CI":
            lines.append("RECOVERY ACTION: verify the awaited commit and CI before deciding the next action.")
        return "\n".join(lines)

    def _bootstrap_prompt(self, recovery_context: str = "") -> str:
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

LIFECYCLE:
{self._lifecycle_context()}

{recovery_context}

Start now by inspecting the current repository state and continue the task.
{STATE_BLOCK_INSTRUCTION}
"""

    def _prompt(self, message: str) -> str:
        return message.rstrip() + "\n\n" + STATE_BLOCK_INSTRUCTION

    def _stop_if_turn_limit(self, tracker: RunTracker, response: str) -> str | None:
        if not self.max_turns or self.turns < self.max_turns:
            return None
        tracker.finish_run("STOPPED", datetime.now(timezone.utc))
        _save_run_summary(self.project_name, tracker)
        trace_summary(tracker.box(), project=self.project_name)
        trace("run.stop", project=self.project_name, run=tracker.summary.run_number)
        return response

    def run(self) -> str:
        with _project_execution_lock(self.project_name):
            return self._run_locked()

    def _run_locked(self) -> str:
        run_started_at = datetime.now(timezone.utc)
        tracker = RunTracker(self.project_name, _next_run_number(self.project_name), run_started_at)
        trace("run.start", project=self.project_name, run=tracker.summary.run_number)
        _save_run_event(self.project_name, tracker, "run.start")
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
            if not status.is_chatgpt:
                raise SupervisorError("attached page is not ChatGPT")
            # _start_fresh_chat may need to navigate from an unrelated
            # ChatGPT conversation into the configured Project before creating
            # the fresh conversation.
            _start_fresh_chat(chat, self.project)

            try:
                ci_baseline = set(run.id for run in _github_get(self.project.repository))
            except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                raise SupervisorError(
                    "cannot access GitHub Actions for the configured repository; "
                    "set GITHUB_TOKEN/GH_TOKEN or authenticate GitHub CLI with 'gh auth login'"
                ) from exc
            memory_file = memory_path(self.project_name)
            memory = load_memory(memory_file, self.project_name)
            try:
                observation = observe_github(self.project.repository)
            except Exception as exc:
                raise SupervisorError(f"cannot observe GitHub repository state: {exc}") from exc

            recovery_context = self._recovery_context(memory, observation)
            bootstrap_prompt = self._bootstrap_prompt(recovery_context)
            if self.project.project_name:
                response = chat.send_project_message_and_wait_for_response(
                    self.project.project_name,
                    bootstrap_prompt,
                    timeout_seconds=self.config.browser.response_timeout_seconds,
                    quiet_seconds=self.config.browser.quiet_seconds,
                )
            else:
                response = chat.send_and_wait_for_response(
                    bootstrap_prompt,
                    timeout_seconds=self.config.browser.response_timeout_seconds,
                    quiet_seconds=self.config.browser.quiet_seconds,
                )

            tracker.start_iteration(run_started_at)
            while True:
                self.turns += 1
                iteration = tracker.iteration
                tracker.record(chatgpt_work=True)
                trace("iteration.start", project=self.project_name, iteration=iteration.number)
                _save_run_event(self.project_name, tracker, "iteration.start", iteration=iteration.number)
                trace("chat.response", project=self.project_name, iteration=iteration.number, response_chars=len(response))
                _save_run_event(self.project_name, tracker, "chat.response", iteration=iteration.number, response_chars=len(response))
                analysis = parse_labos_response(response)
                state = analysis.state
                tracker.record(state=state.value if state else "INVALID")
                try:
                    observed = observe_github(self.project.repository)
                    memory.last_response = response
                    memory.last_analysis = analysis.to_dict()
                    memory.last_observed_commit = observed.commit_sha
                    memory.last_observed_branch = observed.branch
                    memory.last_observed_ci_run = observed.ci_run_id
                    memory.last_observed_ci_status = observed.ci_status
                    memory.last_observed_ci_conclusion = observed.ci_conclusion
                    memory.conversation_url = page.url
                    memory.updated_at = datetime.now(timezone.utc).isoformat()
                    save_memory(memory_file, memory)
                except Exception as exc:
                    trace("run.error", project=self.project_name, error=f"GitHub observation failed after response: {exc}")

                if analysis.structured and not analysis.valid:
                    tracker.finish_iteration("INVALID", datetime.now(timezone.utc), "invalid LABOS_STATE: " + "; ".join(analysis.errors))
                    trace("iteration.complete", project=self.project_name, iteration=iteration.number, success=False, state="INVALID")
                    response = chat.send_and_wait_for_response(
                        REMIND_MESSAGE + "\n\nYour LABOS_STATE block was invalid:\n- " + "\n- ".join(analysis.errors),
                        timeout_seconds=self.config.browser.response_timeout_seconds,
                        quiet_seconds=self.config.browser.quiet_seconds,
                    )
                    stopped = self._stop_if_turn_limit(tracker, response)
                    if stopped is not None:
                        return stopped
                    tracker.start_iteration(datetime.now(timezone.utc))
                elif state is None:
                    tracker.finish_iteration("INVALID", datetime.now(timezone.utc), "missing or invalid state marker")
                    trace("iteration.complete", project=self.project_name, iteration=iteration.number, success=False, state="INVALID")
                    response = chat.send_and_wait_for_response(
                        REMIND_MESSAGE,
                        timeout_seconds=self.config.browser.response_timeout_seconds,
                        quiet_seconds=self.config.browser.quiet_seconds,
                    )
                    stopped = self._stop_if_turn_limit(tracker, response)
                    if stopped is not None:
                        return stopped
                    tracker.start_iteration(datetime.now(timezone.utc))
                elif state == ChatState.DONE:
                    observed = observe_github(
                        self.project.repository,
                        ci_run_id=analysis.ci_run if analysis.structured else None,
                    )
                    reconciliation = reconcile(
                        analysis,
                        observed,
                        previous_commit=memory.last_observed_commit,
                    )
                    verified = reconciliation.verified
                    result = "DONE_VERIFIED" if verified else "DONE_UNVERIFIED"
                    reason = None if verified else reconciliation.reason

                    # DONE completes the current lifecycle phase. If the
                    # repository contains the evidence required by the next
                    # phase, advance automatically and start a fresh Project chat.
                    target_phase = next_phase(self.current_phase)
                    if verified and target_phase is not None:
                        can_transition, missing = can_advance(
                            self.project.project_root,
                            self.current_phase,
                            target_phase,
                        )
                        if can_transition:
                            previous_phase = self.current_phase
                            self.current_phase = target_phase
                            save_lifecycle_state(
                                self.config.state_root,
                                self.project_name,
                                LifecycleState(phase=target_phase, approved=False),
                            )
                            tracker.finish_iteration(
                                "PHASE_COMPLETE",
                                datetime.now(timezone.utc),
                                f"{previous_phase.value} -> {target_phase.value}",
                            )
                            trace(
                                "phase.transition",
                                project=self.project_name,
                                iteration=iteration.number,
                                from_phase=previous_phase.value,
                                to_phase=target_phase.value,
                            )
                            _save_run_event(
                                self.project_name,
                                tracker,
                                "phase.transition",
                                iteration=iteration.number,
                                from_phase=previous_phase.value,
                                to_phase=target_phase.value,
                            )
                            _save_run_summary(self.project_name, tracker)

                            _start_fresh_chat(chat, self.project)
                            response = chat.send_project_message_and_wait_for_response(
                                self.project.project_name,
                                self._bootstrap_prompt(
                                    f"Automatically advanced from {previous_phase.value} to "
                                    f"{target_phase.value}. Continue the new phase now. "
                                    "No human approval is required."
                                ),
                                timeout_seconds=self.config.browser.response_timeout_seconds,
                                quiet_seconds=self.config.browser.quiet_seconds,
                            )
                            tracker.start_iteration(datetime.now(timezone.utc))
                            continue

                        # DONE was reported, but the repository is not yet ready
                        # to enter the next phase. Keep working in this conversation.
                        missing_text = "\n".join(f"- {item}" for item in missing)
                        tracker.finish_iteration(
                            "PHASE_INCOMPLETE",
                            datetime.now(timezone.utc),
                            "missing phase evidence",
                        )
                        trace(
                            "phase.incomplete",
                            project=self.project_name,
                            iteration=iteration.number,
                            phase=self.current_phase.value,
                            missing=missing,
                        )
                        response = chat.send_and_wait_for_response(
                            self._prompt(
                                f"""[LAB OS — PHASE EVIDENCE INCOMPLETE]
You reported DONE for {self.current_phase.value}, but LabOS cannot advance yet.

The repository evidence required for the next phase ({target_phase.value}) is incomplete:
{missing_text}

Continue the current phase. Create or complete the missing evidence in the repository,
run relevant checks, and only report DONE again when the phase is genuinely complete.
No human approval is required."""),
                            timeout_seconds=self.config.browser.response_timeout_seconds,
                            quiet_seconds=self.config.browser.quiet_seconds,
                        )
                        tracker.start_iteration(datetime.now(timezone.utc))
                        continue

                    # No next phase means the lifecycle is genuinely complete.
                    tracker.finish_iteration("DONE", datetime.now(timezone.utc), reason)
                    tracker.finish_run(result, datetime.now(timezone.utc), reason)
                    trace("iteration.complete", project=self.project_name, iteration=iteration.number, success=verified, state=result)
                    _save_run_summary(self.project_name, tracker)
                    trace_summary(tracker.box(), project=self.project_name, success=verified)
                    trace("run.complete", project=self.project_name, run=tracker.summary.run_number, success=verified)
                    return response
                elif state == ChatState.WAIT_CI:
                    observed = observe_github(
                        self.project.repository,
                        ci_run_id=analysis.ci_run if analysis.structured else None,
                    )
                    reconciliation = reconcile(
                        analysis,
                        observed,
                        previous_commit=memory.last_observed_commit,
                    )
                    trace("remote_ci.start", project=self.project_name, iteration=iteration.number)
                    if reconciliation.ci_verified:
                        passed, summary = True, (
                            f"GitHub CI already verified: run={observed.ci_run_id or 'unknown'} "
                            f"conclusion={observed.ci_conclusion}"
                        )
                    else:
                        passed, summary = wait_for_ci(
                            self.project.repository,
                            datetime.now(timezone.utc),
                            baseline_run_ids=ci_baseline,
                            timeout_seconds=self.ci_timeout_seconds,
                            poll_seconds=self.ci_poll_seconds,
                            target_sha=analysis.current_commit if analysis.structured else None,
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
                    _save_run_event(self.project_name, tracker, "iteration.complete", iteration=iteration.number, success=passed, state="WAIT_CI")
                    trace_summary(tracker.box(), project=self.project_name, success=passed)
                    stopped = self._stop_if_turn_limit(tracker, response)
                    if stopped is not None:
                        return stopped
                    response = chat.send_and_wait_for_response(
                        CI_PASSED_MESSAGE if passed else CI_FAILED_MESSAGE,
                        timeout_seconds=self.config.browser.response_timeout_seconds,
                        quiet_seconds=self.config.browser.quiet_seconds,
                    )
                    stopped = self._stop_if_turn_limit(tracker, response)
                    if stopped is not None:
                        return stopped
                    tracker.start_iteration(datetime.now(timezone.utc))
                elif state == ChatState.FIX_CI:
                    tracker.finish_iteration("FIX_CI", datetime.now(timezone.utc))
                    trace("iteration.complete", project=self.project_name, iteration=iteration.number, success=False, state="FIX_CI")
                    stopped = self._stop_if_turn_limit(tracker, response)
                    if stopped is not None:
                        return stopped
                    response = chat.send_and_wait_for_response(
                        CI_FAILED_MESSAGE,
                        timeout_seconds=self.config.browser.response_timeout_seconds,
                        quiet_seconds=self.config.browser.quiet_seconds,
                    )
                    stopped = self._stop_if_turn_limit(tracker, response)
                    if stopped is not None:
                        return stopped
                    tracker.start_iteration(datetime.now(timezone.utc))
                else:
                    tracker.finish_iteration("CONTINUE", datetime.now(timezone.utc))
                    trace("iteration.complete", project=self.project_name, iteration=iteration.number, success=True, state="CONTINUE")
                    stopped = self._stop_if_turn_limit(tracker, response)
                    if stopped is not None:
                        return stopped
                    response = chat.send_and_wait_for_response(
                        CONTINUE_MESSAGE,
                        timeout_seconds=self.config.browser.response_timeout_seconds,
                        quiet_seconds=self.config.browser.quiet_seconds,
                    )
                    tracker.start_iteration(datetime.now(timezone.utc))

