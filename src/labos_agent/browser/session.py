"""Chromium session management, including attachment to an existing browser."""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright


class BrowserSession:
    def __init__(self, profile_dir: Path, *, headless: bool = False) -> None:
        self.profile_dir = profile_dir
        self.headless = headless
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._browser: Browser | None = None
        self._attached = False

    def start(self) -> BrowserContext:
        if self._context is not None:
            return self._context
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=self.headless,
            viewport={"width": 1440, "height": 1000},
        )
        return self._context

    def connect_over_cdp(self, cdp_url: str) -> BrowserContext:
        """Attach to an already-running Chromium without taking ownership of it."""
        if self._context is not None:
            return self._context
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(cdp_url)
        contexts = self._browser.contexts
        if not contexts:
            self._playwright.stop()
            self._playwright = None
            self._browser = None
            raise RuntimeError("Connected Chromium has no browser contexts")
        self._context = contexts[0]
        self._attached = True
        return self._context

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("Browser session has not been started")
        return self._context

    def close(self) -> None:
        if self._attached:
            # Detach Playwright without closing the user's Chromium.
            self._context = None
            self._browser = None
            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None
            self._attached = False
            return

        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def __enter__(self) -> "BrowserSession":
        if self._context is None:
            self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
