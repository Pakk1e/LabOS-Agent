"""Conservative ChatGPT browser adapter."""
from __future__ import annotations
from dataclasses import dataclass
from playwright.sync_api import Page

@dataclass(frozen=True)
class ChatStatus:
    url: str
    title: str
    has_input: bool

class ChatGPTPage:
    def __init__(self, page: Page) -> None:
        self.page = page

    def open(self, url: str = "https://chatgpt.com/") -> ChatStatus:
        self.page.goto(url, wait_until="domcontentloaded")
        return self.status()

    def status(self) -> ChatStatus:
        return ChatStatus(self.page.url, self.page.title(), self._find_input() is not None)

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
