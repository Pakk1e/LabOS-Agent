# LabOS-Agent Plan

## Phase 0 — Repository bootstrap
- [x] Create dedicated repository.
- [x] Establish project purpose and safety rules.
- [x] Add Python package skeleton.
- [x] Add configuration/state model.
- [x] Add tests for controller state transitions.

## Phase 1 — Local controller
- [x] Implement run lifecycle.
- [x] Implement deadline handling.
- [x] Implement iteration and rollover limits.
- [x] Implement persistent state.
- [ ] Add structured logs beyond persistent state.

## Phase 2 — Browser
- [x] Add Playwright.
- [x] Launch persistent Chromium profile.
- [x] Attach to an existing Chromium over CDP.
- [x] Manually authenticate to ChatGPT.
- [x] Detect active ChatGPT page.
- [x] Detect response generation/completion conservatively.
- [x] Submit a continuation message.
- [x] Capture the completed response safely.

## Phase 3 — Weather integration
- [x] Add Weather project configuration.
- [x] Read Weather project state files.
- [x] Define continuation prompt.
- [x] Validate repository state before continuation.
- [x] Add one-iteration manual mode.

## Phase 4 — Chat rollover
- [x] Define conservative rollover thresholds.
- [x] Request a new-chat handoff.
- [x] Capture and persist the handoff.
- [x] Preserve Project context as a hard requirement.
- [x] Provide configurable Project new-chat navigation.
- [ ] Run a live rollover test against the user's actual Project UI.

## Phase 5 — Autonomous mode
- [x] Add safety gates.
- [x] Add repeated-failure detection.
- [x] Add hard deadline.
- [x] Add BLOCKED handling.
- [x] Add overnight/run command.
- [ ] Produce a structured morning report.

## Phase 6 — Generalization
- [ ] Support additional Lab OS projects.
- [x] Keep project-specific rules/configuration isolated.
- [ ] Add project adapters only where required.
- [x] Add provider-independent local CI contract and LocalCI executor.
- [ ] Connect LocalCI results to autonomous project-loop state transitions.
- [ ] Add GitHub Actions as an optional CI provider/fallback.
