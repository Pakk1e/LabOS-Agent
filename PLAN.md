# LabOS-Agent Plan

## Phase 0 — Repository bootstrap
- [x] Create dedicated repository.
- [x] Establish project purpose and safety rules.
- [ ] Add Python package skeleton.
- [ ] Add configuration/state model.
- [ ] Add tests for controller state transitions.

## Phase 1 — Local controller
- [ ] Implement run lifecycle.
- [ ] Implement deadline handling.
- [ ] Implement iteration and rollover limits.
- [ ] Implement persistent state.
- [ ] Implement structured logs.

## Phase 2 — Browser
- [ ] Add Playwright.
- [ ] Launch persistent Chromium profile.
- [ ] Manually authenticate to ChatGPT.
- [ ] Detect active conversation.
- [ ] Detect response generation/completion.
- [ ] Submit a continuation message.
- [ ] Capture the completed response safely.

## Phase 3 — Weather integration
- [ ] Add Weather project configuration.
- [ ] Read Weather project state files.
- [ ] Define continuation prompt.
- [ ] Validate repository/test state before continuation.
- [ ] Add manual-confirmation mode.

## Phase 4 — Chat rollover
- [ ] Define conservative context rollover threshold.
- [ ] Request a new-chat handoff.
- [ ] Capture and persist the handoff.
- [ ] Open a new conversation.
- [ ] Paste handoff and resume.
- [ ] Test recovery after browser interruption.

## Phase 5 — Autonomous mode
- [ ] Add safety gates.
- [ ] Add repeated-failure detection.
- [ ] Add hard deadline.
- [ ] Add BLOCKED handling.
- [ ] Add overnight run command.
- [ ] Produce morning report.

## Phase 6 — Generalization
- [ ] Support additional Lab OS projects.
- [ ] Keep project-specific rules/configuration isolated.
