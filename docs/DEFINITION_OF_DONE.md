# LabOS Definition of Done

LabOS is release-ready when the engineering control plane can safely create or adopt a project, enforce the documented lifecycle, recover from ordinary failures, and provide auditable state.

## Lifecycle
- All lifecycle phases and sequential transitions are enforced.
- DEVELOPMENT requires explicit human approval.
- Evidence gates reject missing or placeholder documentation.
- Validation requires every documented acceptance criterion to have a line-level PASS result.
- Runtime lifecycle state survives configuration changes and restart.
- Corrupt runtime state can recover from its last known-good backup.

## Project modes
- Guided projects start in BRAINSTORM.
- Specification projects start in DOCUMENTATION.
- Existing repositories are inspected rather than bootstrapped or overwritten.
- Existing-repository adoption requires a local Git repository.

## Reliability
- Configuration and lifecycle writes are atomic.
- Concurrent lifecycle requests are serialized.
- Stale supervisor records are detected and cleared.
- Run/event history tolerates missing or malformed individual records.
- Browser and API flows are covered by automated tests.

## Security
- The web server binds to loopback by default.
- Remote binding requires explicit opt-in.
- JSON request bodies are bounded.
- JSON content type and user-controlled URL/input fields are validated.
- Project names cannot escape lifecycle state directories.
- DEVELOPMENT approval is enforced at the API and supervisor layers.

## Quality
- API error paths have deterministic status codes.
- UI workspaces expose lifecycle, documentation, planning, validation, runs, CI, and repository assessment.
- Browser dialogs use accessible semantics and non-blocking toast feedback.
- CI is green before a release is declared.

This document is the finite acceptance contract for LabOS engineering completeness. New features belong on the roadmap rather than silently expanding the definition of done.
