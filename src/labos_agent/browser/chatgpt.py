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
        for selector in ('textarea','[contenteditable="true"]'):
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

    def start_new_project_chat(self,*,project_name:str|None,project_url:str|None,selector:str|None)->None:
        if project_url:
            self.page.goto(project_url,wait_until="domcontentloaded",timeout=60000)
        if project_name and not self.project_context_present(project_name):
            raise RuntimeError(f"required ChatGPT Project context not detected: {project_name}")
        if selector:
            loc=self.page.locator(selector).first
            if loc.count()==0 or not loc.is_visible():
                raise RuntimeError("configured Project new-chat selector not found")
            loc.click()
        else:
            project_options=f'button[aria-label="Open project options for {project_name}"]' if project_name else ""
            options=self.page.locator(project_options).first if project_options else None
            if options is None or options.count()==0 or not options.is_visible():
                raise RuntimeError("Project new-chat navigation requires a selector or a visible Project options control")
            options.click()
            menu=self.page.locator('[role="menu"]').first
            if menu.count()==0 or not menu.is_visible():
                raise RuntimeError("Project options menu did not open")
            new_chat=menu.get_by_text("New chat",exact=True).first
            if new_chat.count()==0 or not new_chat.is_visible():
                raise RuntimeError("Project options menu has no visible New chat action")
            new_chat.click()
        self.page.wait_for_timeout(1000)
        self.assert_ready()
        if project_name and not self.project_context_present(project_name):
            raise RuntimeError("Project context disappeared after starting the new chat")
