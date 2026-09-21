"""Conservative response detection primitives."""
from __future__ import annotations
from dataclasses import dataclass
from playwright.sync_api import Page

@dataclass(frozen=True)
class ResponseObservation:
    generating: bool
    input_available: bool

def observe(page: Page) -> ResponseObservation:
    input_box = page.locator('textarea, [contenteditable="true"]').first
    return ResponseObservation(
        generating=False,
        input_available=input_box.count() > 0 and input_box.is_visible())
