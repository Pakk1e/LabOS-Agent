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


## Minimal state-marker supervisor

The preferred experimental loop is the small `lab-agent supervise <project>` supervisor.

ChatGPT remains responsible for repository work. LabOS does not execute ChatGPT-issued shell/file commands. Every assistant response must end with exactly one state marker:

- `(STATE CONTINUE STATE)` — send a continuation prompt.
- `(STATE WAIT_CI STATE)` — wait for the relevant GitHub Actions run, then tell ChatGPT to continue or fix CI.
- `(STATE FIX_CI STATE)` — tell ChatGPT to investigate and fix the CI failure.
- `(STATE DONE STATE)` — stop.

Every response now also carries a strict `<LABOS_STATE>` block containing the reported task state, current commit, commit status, local test result, CI run/status, and next action. The legacy state marker remains as a compatibility signal.

LabOS persists the latest parsed response and independently observed GitHub state under `state/<project>/supervisor_state.json`. At the start of a new supervisor run, it reads that memory and reconciles it against the current GitHub HEAD and latest Actions result before prompting ChatGPT. Reported state is evidence, not truth: repository and CI observations can produce a verified completion or a conflict.

If the structured block is missing or invalid, LabOS sends a reminder and waits for a corrected response. The supervisor's reconciliation state machine distinguishes WORKING, WAITING_CI, FIXING_CI, DONE, and CONFLICT. This protocol remains independent of repository contents and local shell execution.

## Lab OS control UI

A lightweight local web control plane is available without adding a frontend
build system:

```bash
lab-agent web --config config.yaml --host 127.0.0.1 --port 8080
```

Open `http://127.0.0.1:8080`. The dashboard provides:

- project list and live supervisor/CI state;
- repository, branch, commit and ChatGPT Project context;
- supervisor start control;
- project creation, persisted into the configured `config.yaml`;
- event-driven supervisor state updates over Server-Sent Events (SSE);
- adaptive GitHub Actions refresh only while a run is active;
- a 60-second refresh fallback when the SSE connection is unavailable;

The web service is intended for the trusted Lab OS server and binds to
localhost by default. Put an authenticated reverse proxy in front of it before
exposing it beyond the server.

### Persistent server service

For an unattended server, install the supplied systemd unit:

```bash
sudo install -m 0644 deploy/labos-web.service /etc/systemd/system/labos-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now labos-web
sudo systemctl status labos-web
```

The unit runs as `park-pro`, uses the repository virtual environment, restarts
after an unexpected web-process failure, and writes its output to the systemd
journal. Once installed, the web service does not depend on an SSH session or
an open browser window. The ChatGPT supervisor still requires the persistent
Chromium/CDP service to be available when a supervisor is started.

Useful commands:

```bash
sudo systemctl restart labos-web
sudo journalctl -u labos-web -f
```

The UI deliberately uses the existing Python runtime and browser/GitHub
supervisor state. It is a control surface, not a second implementation of the
supervisor state machine.

### Control UI milestones

The control UI now includes the next operations-console layers:

- **Milestone 2 — Operations:** persisted supervisor run history, run outcomes and iteration summaries; GitHub Actions run history; workflow job/step inspection; live supervisor/CI state.
- **Milestone 3 — Management:** start/stop supervisor controls with a configurable turn limit; project settings editing; project archiving; project creation remains available from the UI.

The UI exposes these through the **Overview**, **Runs**, **CI / Actions**, and **Settings** views. GitHub Actions data is read through the authenticated `gh` CLI on the Lab OS server, so the UI does not maintain a second GitHub authentication mechanism.

Completed supervisor runs are persisted under `state/<project>/runs/`. Existing state is preserved when a project is archived from `config.yaml`.

### Milestone 4 — Live operations

The UI also exposes live operational state:

- supervisor process state and PID;
- persisted per-run event timelines;
- run timeline inspection from the Runs view;
- live workflow/job inspection through the authenticated GitHub CLI;
- completed-run artifacts remain available after the supervisor exits.

Run events are stored alongside each run as state/<project>/runs/<run>.events.jsonl. The event stream is intentionally append-only and contains operational metadata rather than ChatGPT prompt contents.


### Parallel supervisors

Supervisor execution is isolated per project. A filesystem lock at
`state/<project>/supervisor.lock` prevents two supervisor processes from
operating the same project at once, while supervisors for different projects
may run concurrently.

For example, Weather and Worlds can run at the same time:

```text
Weather supervisor  -> Weather Project/page -> VilaPro-Weather CI
Worlds supervisor   -> Worlds Project/page  -> VilaPro-Worlds CI
```

Each concurrently running project must have its own identifiable ChatGPT
Project page in the attached Chromium session. The browser adapter selects a
page using the configured Project context and fails closed if it cannot find a
matching page. Two supervisors must not share the same project.

The per-project lock is a process-level safety boundary: if a second supervisor
for the same project is started, it exits with a clear "supervisor already
running" error instead of competing for the same ChatGPT conversation, state,
or CI result.


### New project repository creation

The New Project wizard can create a GitHub repository as part of project setup.
Enable **Create GitHub repository and clone it**, choose Private or Public, and
provide the repository name plus the desired local path.

The server uses the authenticated `gh` CLI to:

1. create the GitHub repository;
2. clone it into the requested local path;
3. only after both operations succeed, add the project to `config.yaml`.

For safety, the local target must either not exist or be empty. LabOS never
overwrites an existing non-empty directory during this flow.

If the repository already exists, leave repository creation disabled and use
the existing repository normally.


## Project lifecycle

New projects use a documented lifecycle instead of going directly from an idea
to implementation:

```text
IDEA
  -> BRAINSTORM
  -> DOCUMENTATION
  -> PLANNING
  -> DEVELOPMENT
  -> VALIDATION
  -> MAINTENANCE
```

The **guided** project mode starts in BRAINSTORM. The initial idea is captured
in `docs/IDEA.md`, and the repository is bootstrapped with durable
documentation files:

- `docs/IDEA.md`
- `docs/PRODUCT.md`
- `docs/REQUIREMENTS.md`
- `docs/ARCHITECTURE.md`
- `docs/DECISIONS.md`
- `docs/ROADMAP.md`
- `AGENTS.md`

During BRAINSTORM the supervisor is instructed to explore the idea and open
questions rather than implement product features. During DOCUMENTATION it
turns the agreed discussion into requirements, product definition,
architecture, decisions and roadmap. PLANNING converts that documentation
into implementation tasks and acceptance criteria.

Lifecycle transitions are evidence-gated. Moving into PLANNING requires the
durable documentation set to exist and contain non-placeholder content.
Moving into DEVELOPMENT requires a requirements document plus an implementation
plan containing an **Acceptance Criteria** section. The autonomous supervisor
advances when that evidence gate passes; persisted approval fields are retained
only for backwards compatibility. Moving into MAINTENANCE requires a validation
report containing **Validation Results** and **Acceptance Criteria** sections.
Lifecycle transitions are recorded in `state/<project>/lifecycle_history.jsonl`.

The LabOS UI exposes the current lifecycle phase, next phase, approval status,
and the evidence gate with missing items. The low-level supervisor state
protocol (CONTINUE, WAIT_CI, FIX_CI, DONE) remains separate from the project
lifecycle.

Projects may also use **specification** mode when the human already has a
sufficiently complete specification; this starts at DOCUMENTATION rather than
BRAINSTORM.
