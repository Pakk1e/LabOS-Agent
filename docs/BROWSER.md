# Browser Layer

LabOS-Agent uses a persistent local Chromium profile and attaches to an already-running Chromium over CDP.

## Runtime contract

The server-side browser stack is managed independently of SSH:

- Xvfb provides the persistent display.
- Chromium uses the persistent LabOS profile.
- Chromium exposes CDP on 127.0.0.1:9222.
- x11vnc/noVNC provide optional visual access.
- LabOS-Agent attaches to Chromium and never owns or closes the remote browser.

## Authentication

A human authenticates to ChatGPT once in the persistent Chromium profile. LabOS-Agent checks that the ChatGPT origin, composer, and absence of a verification challenge are present. It never stores passwords, cookies, or session tokens.

If authentication expires or a verification challenge appears, autonomous execution must stop and require human intervention.

## Response lifecycle

A response is considered complete only after:

1. a new or changed assistant response is observed;
2. the response text has remained unchanged for the configured quiet period;
3. no known Stop control is visible;
4. the composer is available.

The DOM of ChatGPT can change. Stop-control selectors are therefore intentionally conservative and the controller fails closed if completion cannot be established.

## ChatGPT Projects

Project context is mandatory for Project-scoped runs. The controller can verify a configured Project name from visible page text.

Rollover never falls back to the global New Chat action. A configured Project name uses the visible Project-home UI; alternatively, an explicit Project URL or verified selector can be supplied. If the configured Project route cannot be established, the controller writes a handoff and enters BLOCKED instead of risking loss of Project context.

## Commands

Manual browser smoke test:

    lab-agent browser-attach --cdp http://127.0.0.1:9222

One controlled project iteration:

    lab-agent continue weather

Autonomous loop:

    lab-agent run weather --until 10:00

Do not enable unattended runs until the actual Project rollover path has been manually verified.
