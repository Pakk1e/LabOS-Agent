"""Persistent Chromium session management."""
from __future__ import annotations
from pathlib import Path
from playwright.sync_api import BrowserContext, Playwright, sync_playwright

class BrowserSession:
    def __init__(self, profile_dir: Path, *, headless: bool = False) -> None:
        self.profile_dir = profile_dir
        self.headless = headless
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None

    def start(self) -> BrowserContext:
        if self._context is not None:
            return self._context
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self.profile_dir), headless=self.headless,
            viewport={"width": 1440, "height": 1000})
        return self._context

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("Browser session has not been started")
        return self._context

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def __enter__(self) -> "BrowserSession":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
