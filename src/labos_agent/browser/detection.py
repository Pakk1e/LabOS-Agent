"""Conservative ChatGPT response lifecycle detection."""
from __future__ import annotations
from dataclasses import dataclass
from playwright.sync_api import Page

@dataclass(frozen=True)
class ResponseObservation:
    generating: bool
    input_available: bool
    stop_control_visible: bool = False
    assistant_count: int = 0

_STOP_SELECTORS=(
    'button[aria-label*="Stop" i]',
    'button[title*="Stop" i]',
    'button[data-testid*="stop" i]',
)

def _visible(page: Page, selector: str) -> bool:
    try:
        loc=page.locator(selector)
        return loc.count()>0 and loc.first.is_visible()
    except Exception:
        return False

def observe(page: Page) -> ResponseObservation:
    box=page.locator('textarea, [contenteditable="true"]').first
    available=box.count()>0 and box.is_visible()
    stop=any(_visible(page,s) for s in _STOP_SELECTORS)
    count=page.locator('[data-message-author-role="assistant"]').count()
    return ResponseObservation(generating=stop,input_available=available,stop_control_visible=stop,assistant_count=count)
