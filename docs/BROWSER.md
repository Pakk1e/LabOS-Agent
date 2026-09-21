# Browser Layer

LabOS-Agent uses a persistent local Chromium profile so a human can authenticate
to ChatGPT once and the controller can later reuse the session.

## Security

- Do not put credentials in configuration files.
- Do not commit the browser profile.
- Treat the profile directory as sensitive.
- Keep the browser visible while developing the automation.
- Autonomous message submission is disabled until response detection is validated.

## First manual test

    python -m pip install -e .
    python -m playwright install chromium

On first launch, log into ChatGPT manually. The session is stored in the local
profile directory.

## Next browser milestones

1. Verify persistent login survives browser restart.
2. Identify a stable way to locate the active conversation.
3. Measure the DOM while ChatGPT is generating.
4. Detect generation completion conservatively.
5. Capture the assistant response.
6. Only then enable automatic Continue Weather.
