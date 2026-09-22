"""Conservative ChatGPT browser adapter."""
from __future__ import annotations
from dataclasses import dataclass
import time
from urllib.parse import urlparse
from playwright.sync_api import Page
from .detection import observe

@dataclass(frozen=True)
class ChatStatus:
    url: str
    title: str
    has_input: bool
    is_challenge: bool
    is_chatgpt: bool
    generating: bool
    assistant_count: int

class ChatGPTPage:
    def __init__(self,page:Page)->None: self.page=page

    def open(self,url="https://chatgpt.com/")->ChatStatus:
        self.page.goto(url,wait_until="domcontentloaded",timeout=60000)
        return self.status()

    def status(self)->ChatStatus:
        url=self.page.url
        obs=observe(self.page)
        return ChatStatus(url,self.page.title(),obs.input_available,
                          "__cf_chl_" in url or "challenge" in url.lower(),
                          self._is_chatgpt_url(url),obs.generating,obs.assistant_count)

    @staticmethod
    def _is_chatgpt_url(url:str)->bool:
        try: host=(urlparse(url).hostname or "").lower()
        except ValueError: return False
        return host=="chatgpt.com" or host.endswith(".chatgpt.com")

    def _find_input(self):
        for selector in ('[contenteditable="true"][role="textbox"]','#prompt-textarea[contenteditable="true"]','[contenteditable="true"]','textarea'):
            loc=self.page.locator(selector).first
            if loc.count()>0 and loc.is_visible(): return loc
        return None

    def assert_ready(self)->None:
        status=self.status()
        if status.is_challenge: raise RuntimeError("ChatGPT verification challenge is active")
        if not status.is_chatgpt: raise RuntimeError("attached page is not ChatGPT")
        if not status.has_input: raise RuntimeError("ChatGPT composer is unavailable; authentication may be required")

    def is_authenticated(self)->bool:
        try: self.assert_ready(); return True
        except RuntimeError: return False

    def send_message(self,message:str)->None:
        if not message.strip(): raise ValueError("message must not be empty")
        self.assert_ready()
        box=self._find_input()
        if box is None: raise RuntimeError("ChatGPT message input was not found")
        box.fill(message)
        box.press("Enter")

    def _assistant_texts(self)->list[str]:
        loc=self.page.locator('[data-message-author-role="assistant"]')
        return [t.strip() for t in loc.all_text_contents() if t.strip()]

    def wait_for_response(self,*,before:list[str],timeout_seconds=300,quiet_seconds=3,poll_seconds=.5)->str:
        deadline=time.monotonic()+timeout_seconds
        last_text=""
        last_change=0.0
        saw=False
        while time.monotonic()<deadline:
            texts=self._assistant_texts()
            if texts:
                candidate=texts[-1]
                previous=before[-1] if before else ""
                if len(texts)>len(before) or candidate!=previous:
                    saw=True
                    if candidate!=last_text:
                        last_text=candidate
                        last_change=time.monotonic()
                    obs=observe(self.page)
                    if time.monotonic()-last_change>=quiet_seconds and not obs.generating and obs.input_available:
                        return candidate
            time.sleep(poll_seconds)
        if not saw: raise TimeoutError("No new assistant response appeared before timeout")
        raise TimeoutError("Assistant response did not reach a conservative completed state before timeout")

    def send_and_wait_for_response(self,message:str,**kwargs)->str:
        before=self._assistant_texts()
        self.send_message(message)
        return self.wait_for_response(before=before,**kwargs)

    def project_context_present(self,project_name:str)->bool:
        if not project_name: return True
        try: body=self.page.locator("body").inner_text(timeout=3000)
        except Exception: return False
        return project_name.casefold() in body.casefold()

    def project_chat_composer_present(self,project_name:str)->bool:
        """Return whether the visible composer explicitly belongs to the Project."""
        if not project_name:
            return False
        expected=f"new chat in {project_name}".casefold()
        selectors=(
            '[contenteditable="true"][role="textbox"]',
            '#prompt-textarea[contenteditable="true"]',
        )
        for selector in selectors:
            loc=self.page.locator(selector).first
            try:
                if not loc.count() or not loc.is_visible():
                    continue
                label=(loc.get_attribute("aria-label") or "").casefold()
                placeholder=(loc.get_attribute("placeholder") or "").casefold()
                if expected in label or expected in placeholder:
                    return True
            except Exception:
                continue
        return False

    def _project_home_button(self, project_name: str):
        """Find the Project-home button belonging to the named Project."""
        buttons=self.page.locator('button[aria-label="Open project home"]')
        for i in range(buttons.count()):
            button=buttons.nth(i)
            try:
                if not button.is_visible():
                    continue
                if button.evaluate(
                    """(el, name) => {
                        let node = el;
                        for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
                            const text = (node.innerText || '').trim();
                            if (text.toLowerCase().includes(name.toLowerCase())) return true;
                        }
                        return false;
                    }""",
                    project_name,
                ):
                    return button
            except Exception:
                continue
        return None

    def start_new_project_chat(self,*,project_name:str|None,project_url:str|None,selector:str|None)->None:
        """Navigate to a fresh Project composer using the visible Project UI.

        Current ChatGPT exposes Project navigation through the sidebar's
        "Open project home" control. The Project home then exposes a composer
        labelled "New chat in <Project>". We prefer this UI flow over direct
        URL navigation so the agent follows the same user-visible path and
        does not need to know or persist a Project URL.
        """
        if project_name:
            home=self._project_home_button(project_name)
            if home is None:
                raise RuntimeError(
                    f"visible Project home button not found for: {project_name}"
                )
            home.click()
            self.page.wait_for_timeout(1000)
        elif project_url:
            self.page.goto(project_url,wait_until="domcontentloaded",timeout=60000)
        elif selector:
            loc=self.page.locator(selector).first
            if loc.count()==0 or not loc.is_visible():
                raise RuntimeError("configured Project new-chat selector not found")
            loc.click()
            self.page.wait_for_timeout(1000)
        else:
            raise RuntimeError("Project rollover requires a Project name, URL, or verified selector")

        self.assert_ready()
        if project_name and not self.project_context_present(project_name):
            raise RuntimeError(f"required ChatGPT Project context not detected: {project_name}")
        if project_name and not self.project_chat_composer_present(project_name):
            raise RuntimeError(f"Project composer does not identify a new chat for: {project_name}")
