# LabOS-Agent

Lab OS autonomous development controller.

The controller orchestrates persistent ChatGPT browser sessions for project development workflows. It is designed to support multiple Lab OS projects without embedding orchestration code inside any individual project.

## Goals

- Run a development session until a configured deadline.
- Continue a project automatically after a completed ChatGPT response.
- Persist project state outside the chat.
- Detect when a conversation should roll over and create a handoff for a new chat.
- Stop safely on blockers, repeated failures, browser errors, or time limits.
- Keep project repositories isolated from the controller.

## Current phase

Bootstrap only. The first implementation target is the Weather project (Pakk1e/VilaPro-Weather).

## Design principles

1. Project repositories remain independent.
2. ChatGPT credentials are never stored by the controller.
3. Browser authentication uses a persistent local browser profile.
4. Persistent project state is authoritative; chat history is not.
5. The controller must fail closed rather than blindly continue.
6. Autonomous mode is introduced only after a manual-confirmation mode is proven.

## Planned loop

START -> OPEN_CHAT -> SEND_CONTINUE -> WAIT_FOR_RESPONSE -> VALIDATE_STATE -> CONTINUE

Special states:

- ROLLOVER: generate a handoff and start a new chat.
- BLOCKED: stop and preserve state.
- STOPPED: stop because the configured deadline or safety limit was reached.

## Repository relationship

LabOS-Agent is orchestration infrastructure. It does not become a dependency of Weather, Worlds, or other Lab OS applications.
