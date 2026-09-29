# Testing Strategy

## Unit tests
Lifecycle rules, configuration compatibility, evidence gates, state persistence/recovery, input validation, process-state handling, and API helpers are covered by Python tests.

## Browser tests
Playwright/Chromium covers the control-plane UI and the complete lifecycle path. Browser tests use an isolated temporary project and an EventHub instance matching production.

## CI
Every commit is validated by GitHub Actions. A green run is required before treating an implementation change as complete.

## Test principles
- Tests use temporary state and do not depend on developer repositories.
- Negative paths are first-class tests.
- Lifecycle tests verify both API-level enforcement and supervisor-level enforcement.
- Evidence tests use realistic project documents.
- Browser tests wait for actual HTTP responses when asynchronous state changes are involved.

## Coverage focus
The suite covers project lifecycle transitions, approval, documentation and validation gates, malformed state, recovery, stale process records, API input boundaries, local-only binding, UI workspaces, and full lifecycle browser behavior.
