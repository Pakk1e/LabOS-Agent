"""Chromium session management, including attachment to an existing browser."""
from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
from threading import RLock
from typing import Callable, TypeVar
import uuid

T = TypeVar("T")


@dataclass(frozen=True)
class BrowserRequest:
    request_id: str
    attempt: int

from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright


class BrowserSession:
    def __init__(
        self,
        profile_dir: Path,
        *,
        headless: bool = False,
        cdp_url: str | None = None,
    ) -> None:
        self.profile_dir = profile_dir
        self.headless = headless
        self.cdp_url = cdp_url
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._browser: Browser | None = None
        self._attached = False
        self._request_lock = RLock()
        self._active_request_id: str | None = None

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

    def request(
        self,
        operation: Callable[[BrowserRequest], T],
        *,
        retry_limit: int = 1,
        is_transient: Callable[[Exception], bool],
    ) -> T:
        """Serialize one browser operation and attach a stable request identity."""
        from .transport import BrowserTransport

        request_id = uuid.uuid4().hex
        with self._request_lock:
            self._active_request_id = request_id
            try:
                attempt = 0

                def invoke() -> T:
                    nonlocal attempt
                    attempt += 1
                    return operation(BrowserRequest(request_id, attempt))

                return BrowserTransport(retry_limit=retry_limit).request(
                    request_id, invoke, is_transient=is_transient
                )
            finally:
                if self._active_request_id == request_id:
                    self._active_request_id = None

    def assert_current(self, request_id: str) -> None:
        with self._request_lock:
            if self._active_request_id != request_id:
                raise RuntimeError(f"stale browser request: {request_id}")

    @property
    def active_request_id(self) -> str | None:
        with self._request_lock:
            return self._active_request_id

    def reconnect(self) -> BrowserContext:
        """Reattach after a transient CDP/browser disconnect."""
        with self._request_lock:
            if not self.cdp_url:
                raise RuntimeError("browser session has no CDP URL to reconnect")
            self._active_request_id = None
            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
            self._playwright = None
            self._browser = None
            self._context = None
            self._attached = False
            return self.connect_over_cdp(self.cdp_url)

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
            if self.cdp_url is not None:
                self.connect_over_cdp(self.cdp_url)
            else:
                self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
