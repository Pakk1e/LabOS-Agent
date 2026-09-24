# LabOS-Agent

Lab OS autonomous development controller.

LabOS-Agent orchestrates persistent ChatGPT browser sessions for project development workflows. It is orchestration infrastructure, not a dependency of Weather, Worlds, or other project repositories.

## Current capability

The controller now supports:

- persistent Chromium attached over CDP;
- one controlled project iteration with `lab-agent continue <project>`;
- repeated development with `lab-agent run <project> --until HH:MM`;
- persistent controller state under `state/<project>/`;
- project-state snapshots from the repository;
- controlled server execution for project-scoped file reads/writes and read-only Git inspection;
- explicit execution results returned to the ChatGPT loop before it can claim a command ran;
- per-project process locking and stale-run recovery;
- CI-failure recovery that preserves failed-iteration changes, including untracked files;
- conservative response completion detection;
- safety limits and fail-closed BLOCKED/STOPPED states;
- conversation handoff generation and Project-aware rollover hooks.

## ChatGPT Projects

Project context is part of the execution contract. The controller must not silently roll a chat over into a global conversation because that would lose Project files/instructions/memory. Configure a verified `project_name` for Project-aware UI navigation; use `project_url` or `new_chat_selector` only when the named Project-home route is not available.

Projects are designed to keep related chats, files, and instructions together, so keeping the agent inside the same Project is intentional.

## Commands

One controlled iteration:

```bash
lab-agent continue weather
```

Autonomous loop:

```bash
lab-agent run weather --until 10:00 --max-iterations 50 --max-rollovers 10
```

The autonomous command stops at the deadline, safety limits, repeated failures, authentication/challenge blockers, or rollover failures.

## Controlled rollover smoke test

To validate same-Project rollover without waiting for the autonomous loop, send a supplied handoff into a fresh Project chat:

```bash
lab-agent browser-project-rollover-test \\
  --cdp http://127.0.0.1:9222 \\
  --project-name "Vadovsky Tech — Lab OS" \\
  --handoff-message "LABOS_ROLLOVER_TEST"
```

Use `--require-rollover` when testing against ChatGPT's explicit maximum-length UI. The test never launches a second Chromium instance and requires the target Project context.

The maximum-length detector is a fail-closed signal. Automatic handoff generation must happen before ChatGPT reaches the hard limit; once the hard-limit banner is displayed, the controller must not assume it can still send a handoff request in the exhausted conversation.

## State

The repository is authoritative. The controller records its own state separately:

- `state/<project>/current.json`
- `state/<project>/last_response.md`
- `state/<project>/handoff.md`

The persistent browser profile is sensitive local state and must never be committed.

## Safety

- No credentials, cookies, or ChatGPT session tokens are stored by LabOS-Agent.
- Autonomous runs attach to an already-running Chromium instance; they do not launch a second browser.
- Authentication or verification failures stop the run.
- Project rollover fails closed unless the Project route and selector are explicitly configured.
- The controller never claims a response is complete solely because a new assistant DOM node appeared; it also requires a conservative quiet period and no detected stop control.


## Server execution

When enabled for a project, ChatGPT may request controlled operations using fenced `labos-exec` JSON blocks. LabOS validates file paths against configured execution roots, rejects controller-owned paths at any depth, and returns real stdout/stderr/exit codes to the agent.

Supported operations are `read_file`, `write_file`, and a tightly restricted `run_command` surface. The latter permits only read-only Git inspection rooted to the configured repository; Git paths must remain inside the project root. Hooks and fsmonitor are disabled for controller and agent Git calls. Commit/push remains exclusively in the Git gate after LocalCI passes.

The execution feature is disabled by default. Project CI commands are trusted controller configuration and are separate from the agent execution protocol.

## Branch and recovery model

Each autonomous run uses an `agent/<run-id>` branch. The controller never commits directly to `main`. A successful LocalCI result is required before the controller commits and pushes an agent branch.

If LocalCI fails, the controller persists the failed-iteration baseline and `pending_ci_fix`. A later `continue` can recover that state and includes changes made during the failed iteration, including newly created or subsequently edited untracked files.

A per-project filesystem lock prevents concurrent controllers. If a previous controller crashed while the state was `WORKING`, the next owner safely resets the stale execution state while preserving CI-recovery state.
