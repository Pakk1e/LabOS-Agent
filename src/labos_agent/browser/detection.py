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
    available=False
    try:
        if box.count()>0:
            # Keep lightweight/fake locators compatible with unit tests while
            # using semantic checks with real Playwright locators.
            if not hasattr(box, "get_attribute"):
                available=box.is_visible()
            else:
                tag=box.evaluate("(el) => el.tagName.toLowerCase()")
                contenteditable=box.get_attribute("contenteditable")
                textbox=box.get_attribute("role") == "textbox"
                hidden=box.is_hidden()
                disabled=box.is_disabled() if tag == "textarea" else False
                available=not hidden and not disabled and (
                    tag == "textarea" or contenteditable == "true" or textbox
                )
    except Exception:
        available=False
    stop=any(_visible(page,s) for s in _STOP_SELECTORS)
    count=page.locator('[data-message-author-role="assistant"]').count()
    return ResponseObservation(generating=stop,input_available=available,stop_control_visible=stop,assistant_count=count)
