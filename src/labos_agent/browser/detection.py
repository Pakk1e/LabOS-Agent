"""Conservative ChatGPT response lifecycle detection."""
from __future__ import annotations
from dataclasses import dataclass
import re
from playwright.sync_api import Page

@dataclass(frozen=True)
class ResponseObservation:
    generating: bool
    input_available: bool
    stop_control_visible: bool = False
    assistant_count: int = 0


def _has_max_length_notice(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    return (
        "you've reached the maximum length for this conversation" in normalized
        and "you can keep talking by starting a new chat" in normalized
    )

def rollover_required(page: Page) -> bool:
    """Return True only for ChatGPT's explicit maximum-length rollover UI."""
    try:
        body = page.locator("body").inner_text(timeout=3000)
    except Exception:
        return False
    if not _has_max_length_notice(body):
        return False
    try:
        button = page.get_by_role("button", name=re.compile(r"^start new chat$", re.I)).first
        return button.count() > 0 and button.is_visible()
    except Exception:
        return False

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
    selectors=('[contenteditable="true"][role="textbox"]','#prompt-textarea[contenteditable="true"]','[contenteditable="true"]','textarea')
    box=None
    for selector in selectors:
        candidate=page.locator(selector).first
        try:
            if candidate.count()>0 and candidate.is_visible():
                box=candidate
                break
        except Exception:
            continue
    if box is None:
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
                editable=box.is_editable() if tag != "textarea" else True
                available=not hidden and not disabled and editable and (
                    tag == "textarea" or contenteditable == "true" or textbox
                )
    except Exception:
        available=False
    stop=any(_visible(page,s) for s in _STOP_SELECTORS)
    count=0
    for selector in (
        '[data-markdown-text-style="assistant-message"]',
        '[data-content-search-unit-key$=":assistant"]',
        '[data-chatgpt-selection-message-id]',
        '[data-message-author-role="assistant"]',
        '[data-testid^="conversation-turn-"][data-turn="assistant"]',
        '[data-turn="assistant"]',
        'article[data-turn="assistant"]',
        'section[data-turn="assistant"]',
    ):
        try:
            count=page.locator(selector).count()
        except Exception:
            count=0
        if count:
            break
    return ResponseObservation(generating=stop and not available,input_available=available,stop_control_visible=stop,assistant_count=count)
