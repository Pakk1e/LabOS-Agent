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
- controlled server execution for project-scoped file reads/writes and argv commands;
- explicit execution results returned to the ChatGPT loop before it can claim a command ran;
- conservative response completion detection;
- safety limits and fail-closed BLOCKED/STOPPED states;
- conversation handoff generation and Project-aware rollover hooks.

## ChatGPT Projects

Project context is part of the execution contract. The controller must not silently roll a chat over into a global conversation because that would lose Project files/instructions/memory. Configure `project_name`, `project_url`, and a verified `new_chat_selector` before enabling rollover.

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

When enabled for a project, ChatGPT may request controlled operations using fenced `labos-exec` JSON blocks. LabOS executes them on the configured project host, validates paths against configured execution roots, runs commands without a shell, captures stdout/stderr/exit code, and returns the real result to the agent.

Supported operations are `read_file`, `write_file`, and `run_command`. Controller-owned Git operations and privileged infrastructure commands are rejected by the execution layer. Commit/push remains exclusively in the Git gate after LocalCI passes.

Example:

```text
```labos-exec
{"action":"run_command","command":["python3","-m","pytest"],"cwd":"."}
```
```

Execution is disabled by setting `execution.enabled: false` in the project configuration.
