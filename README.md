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
