# LabOS-Agent Agent Rules

## Scope

This repository contains the autonomous development controller for Vadovsky Tech — Lab OS.

## Rules

- Do not modify project repositories as part of controller development unless explicitly required for an integration test.
- Do not store ChatGPT passwords, session tokens, cookies, or API credentials in source control.
- Treat the persistent browser profile as sensitive local state.
- Prefer explicit state transitions over implicit loops.
- Every autonomous continuation must have a reason recorded in controller state/logs.
- Fail closed on uncertainty.
- Never bypass a project-specific blocker.
- Never continue after the configured deadline.
- Never claim browser automation works until it has been tested against a real logged-in session.
- Keep Weather as the first target; generalize only after the Weather workflow works.
