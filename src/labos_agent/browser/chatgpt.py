"""Conservative ChatGPT browser adapter."""
from __future__ import annotations
from dataclasses import dataclass
from playwright.sync_api import Page


@dataclass(frozen=True)
class ChatStatus:
    url: str
    title: str
    has_input: bool
    is_challenge: bool


class ChatGPTPage:
    def __init__(self, page: Page) -> None:
        self.page = page

    def open(self, url: str = "https://chatgpt.com/") -> ChatStatus:
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        return self.status()

    def status(self) -> ChatStatus:
        url = self.page.url
        return ChatStatus(
            url=url,
            title=self.page.title(),
            has_input=self._find_input() is not None,
            is_challenge=("__cf_chl_" in url or "challenge" in url.lower()),
        )

    def _find_input(self):
        for selector in ('textarea', '[contenteditable="true"]'):
            locator = self.page.locator(selector).first
            if locator.count() > 0 and locator.is_visible():
                return locator
        return None

    def is_authenticated(self) -> bool:
        return self._find_input() is not None

    def send_message(self, message: str) -> None:
        if not message.strip():
            raise ValueError("message must not be empty")
        input_box = self._find_input()
        if input_box is None:
            raise RuntimeError("ChatGPT message input was not found")
        input_box.fill(message)
        input_box.press("Enter")


    def _assistant_texts(self) -> list[str]:
        locator = self.page.locator('[data-message-author-role="assistant"]')
        return [text.strip() for text in locator.all_text_contents() if text.strip()]

    def send_and_wait_for_response(
        self,
        message: str,
        *,
        timeout_seconds: float = 90,
        stable_seconds: float = 1.5,
        poll_seconds: float = 0.5,
    ) -> str:
        """Send one message and conservatively wait for a stable new response."""
        import time

        before = self._assistant_texts()
        self.send_message(message)
        deadline = time.monotonic() + timeout_seconds
        last_text = ""
        stable_since = None

        while time.monotonic() < deadline:
            texts = self._assistant_texts()
            if texts:
                candidate = texts[-1]
                previous = before[-1] if before else ""
                if len(texts) > len(before) or candidate != previous:
                    if candidate != last_text:
                        last_text = candidate
                        stable_since = time.monotonic()
                    elif stable_since is not None and time.monotonic() - stable_since >= stable_seconds:
                        return candidate
            time.sleep(poll_seconds)

        raise TimeoutError(
            "Timed out waiting for a stable new assistant response. "
            "Autonomous continuation must not proceed."
        )
