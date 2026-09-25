"""Conservative ChatGPT browser adapter."""
from __future__ import annotations
from dataclasses import dataclass
import re
import time
from urllib.parse import urlparse
from playwright.sync_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError
from .detection import observe, rollover_required
from ..trace import dom_enabled, trace

@dataclass(frozen=True)
class ChatStatus:
    url: str
    title: str
    has_input: bool
    is_challenge: bool
    is_chatgpt: bool
    generating: bool
    assistant_count: int
    rollover_required: bool

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
                          self._is_chatgpt_url(url),obs.generating,obs.assistant_count,
                          rollover_required(self.page))

    @staticmethod
    def _is_chatgpt_url(url:str)->bool:
        try: host=(urlparse(url).hostname or "").lower()
        except ValueError: return False
        return host=="chatgpt.com" or host.endswith(".chatgpt.com")

    @classmethod
    def select_page(cls,context,*,project_name:str|None=None,project_url:str|None=None):
        """Select the ChatGPT page belonging to the requested Project.

        Multiple ChatGPT tabs are common during long-running browser sessions.
        Never blindly use the first tab when a Project can be identified.
        """
        pages=[p for p in context.pages if cls._is_chatgpt_url(p.url)]
        if not pages:
            raise RuntimeError("No ChatGPT page is attached")
        if len(pages)==1:
            return pages[0]

        if project_url:
            normalized_project_url=project_url.rstrip("/")
            for page in pages:
                if page.url.rstrip("/") == normalized_project_url:
                    return page

        best_page=None
        best_score=-1
        for page in pages:
            score=0
            try:
                chat=cls(page)
                if project_name:
                    if chat.project_context_present(project_name):
                        score += 20
                    if chat.project_chat_composer(project_name) is not None:
                        score += 40
            except Exception:
                continue
            if score > best_score:
                best_score=score
                best_page=page

        if best_page is None or (project_name and best_score <= 0):
            raise RuntimeError("No ChatGPT page matching the requested Project was found")
        return best_page

    def _find_input(self):
        for selector in ('[contenteditable="true"][role="textbox"]','#prompt-textarea[contenteditable="true"]','[contenteditable="true"]','textarea'):
            loc=self.page.locator(selector).first
            if loc.count()>0 and loc.is_visible(): return loc
        return None

    def assert_ready(self)->None:
        status=self.status()
        if status.is_challenge: raise RuntimeError("ChatGPT verification challenge is active")
        if not status.is_chatgpt: raise RuntimeError("attached page is not ChatGPT")
        if status.rollover_required: raise RuntimeError("ChatGPT conversation has reached maximum length; rollover is required")
        if not status.has_input: raise RuntimeError("ChatGPT composer is unavailable; authentication may be required")

    def is_authenticated(self)->bool:
        try: self.assert_ready(); return True
        except RuntimeError: return False

    def _wait_for_editable_input(self,timeout_seconds:float=15.0):
        deadline=time.monotonic()+timeout_seconds
        while time.monotonic()<deadline:
            box=self._find_input()
            if box is not None:
                try:
                    if box.is_editable():
                        return box
                except Exception:
                    pass
            self.page.wait_for_timeout(250)
        raise RuntimeError("ChatGPT message input did not become editable")

    def send_message(self,message:str)->None:
        if not message.strip(): raise ValueError("message must not be empty")
        self.assert_ready()
        deadline=time.monotonic()+15
        last_error=None
        while time.monotonic()<deadline:
            box=self._find_input()
            if box is None:
                self.page.wait_for_timeout(250)
                continue
            try:
                box.click(force=True, timeout=1000)
                self.page.keyboard.insert_text(message)
                self.page.wait_for_timeout(100)
                box.press("Enter",timeout=1000)
                return
            except (PlaywrightTimeoutError,PlaywrightError) as exc:
                last_error=exc
                self.page.wait_for_timeout(250)
        detail=f": {last_error}" if last_error else ""
        raise RuntimeError(f"ChatGPT message input remained unstable while sending{detail}")

    def _assistant_texts(self)->list[str]:
        """Extract assistant replies using role selectors and turn-level fallbacks."""
        selectors=(
            '[data-message-author-role="assistant"] .markdown',
            '[data-message-author-role="assistant"] .prose',
            '[data-message-author-role="assistant"]',
            '[data-testid^="conversation-turn-"][data-turn="assistant"] .markdown',
            '[data-testid^="conversation-turn-"][data-turn="assistant"]',
            '[data-turn="assistant"] .markdown',
            '[data-turn="assistant"] .prose',
            '[data-turn="assistant"]',
            'article[data-turn="assistant"] .markdown',
            'article[data-turn="assistant"]',
            'section[data-turn="assistant"] .markdown',
            'section[data-turn="assistant"]',
            '[data-testid^="conversation-turn-"] [data-message-author-role="assistant"] .markdown',
            '[data-testid^="conversation-turn-"] [data-message-author-role="assistant"]',
            '[data-role="assistant"] .markdown',
            '[data-role="assistant"]',
            '[data-message-author="assistant"] .markdown',
            '[data-message-author="assistant"]',
            '.agent-turn .markdown',
            '.agent-turn',
        )
        for selector in selectors:
            try:
                texts=[t.strip() for t in self.page.locator(selector).all_text_contents() if t.strip()]
            except Exception:
                continue
            if texts:
                return texts
        return []

    def _user_texts(self)->list[str]:
        selectors=(
            '[data-message-author-role="user"] .whitespace-pre-wrap',
            '[data-message-author-role="user"]',
            '[data-role="user"] .whitespace-pre-wrap',
            '[data-role="user"]',
            '[data-message-author="user"] .whitespace-pre-wrap',
            '[data-message-author="user"]',
            '[data-testid^="conversation-turn-"][data-turn="user"]',
        )
        for selector in selectors:
            try:
                texts=[t.strip() for t in self.page.locator(selector).all_text_contents() if t.strip()]
            except Exception:
                continue
            if texts:
                return texts
        return []

    def _wait_for_submission(self,before:list[str],composer,message:str,timeout_seconds=15)->None:
        """Wait for browser-visible evidence that Enter submitted the turn.

        The user-turn DOM is not a stable contract in ChatGPT. Do not require a
        particular user-message selector before starting the assistant detector.
        """
        deadline=time.monotonic()+timeout_seconds
        target=message.strip()
        while time.monotonic()<deadline:
            if self._submission_evidence(before,composer,target):
                trace("chat.submission_evidence", assistant_count=len(self._assistant_texts()),
                      generating=observe(self.page).generating)
                return
            time.sleep(.25)

    def _submission_evidence(self,before:list[str],composer,message:str)->bool:
        try:
            if observe(self.page).generating:
                return True
        except Exception:
            pass
        try:
            current=self._assistant_texts()
            if len(current)>len(before) or (current and before and current[-1]!=before[-1]):
                return True
        except Exception:
            pass
        try:
            value=composer.input_value(timeout=300)
            if not value.strip():
                return True
        except Exception:
            try:
                text=composer.inner_text(timeout=300)
                if not text.strip():
                    return True
            except Exception:
                pass
        try:
            return message.strip() in self._user_texts()
        except Exception:
            return False

    def wait_for_response(self,*,before:list[str],timeout_seconds=300,quiet_seconds=3,poll_seconds=.5,require_input_available=True)->str:
        trace("chat.response_wait.start", before_count=len(before), timeout_seconds=timeout_seconds,
              quiet_seconds=quiet_seconds, url=self.page.url)
        deadline=time.monotonic()+timeout_seconds
        last_text=""
        last_change=0.0
        saw=False
        last_count=len(before)
        last_trace=0.0
        while time.monotonic()<deadline:
            texts=self._assistant_texts()
            now=time.monotonic()
            if texts:
                candidate=texts[-1]
                previous=before[-1] if before else ""
                if len(texts)>len(before) or candidate!=previous:
                    if not saw:
                        trace("chat.response_wait.detected", assistant_count=len(texts),
                              candidate_chars=len(candidate))
                    saw=True
                    last_count=len(texts)
                    if candidate!=last_text:
                        last_text=candidate
                        last_change=now
                    obs=observe(self.page)
                    input_ready = obs.input_available or not require_input_available
                    if now-last_change>=quiet_seconds and not obs.generating and input_ready:
                        trace("chat.response_wait.complete", assistant_count=len(texts),
                              response_chars=len(candidate), generating=obs.generating,
                              input_available=obs.input_available)
                        return candidate
            if now-last_trace>=10:
                obs=observe(self.page)
                trace("chat.response_wait.poll", assistant_count=len(texts), before_count=len(before),
                      saw_response=saw, generating=obs.generating,
                      input_available=obs.input_available)
                last_trace=now
            time.sleep(poll_seconds)
        diagnostic = self._response_detection_diagnostic(before)
        trace("chat.response_wait.timeout", assistant_count=last_count, before_count=len(before),
              saw_response=saw, diagnostic=diagnostic)
        if not saw:
            raise TimeoutError(
                f"No new assistant response appeared before timeout "
                f"(assistant_count={last_count}, before_count={len(before)}, url={self.page.url})"
            )
        raise TimeoutError("Assistant response did not reach a conservative completed state before timeout")

    def _response_detection_diagnostic(self,before:list[str])->dict[str,object]:
        selectors=(
            '[data-message-author-role="assistant"]',
            '[data-testid^="conversation-turn-"][data-turn="assistant"]',
            '[data-turn="assistant"]',
            'article[data-turn="assistant"]',
            'section[data-turn="assistant"]',
            '[data-testid^="conversation-turn-"]',
            '[data-message-author-role="user"]',
            '.agent-turn',
        )
        counts={}
        for selector in selectors:
            try:
                counts[selector]=self.page.locator(selector).count()
            except Exception:
                counts[selector]="error"
        result:dict[str,object]={
            "url":self.page.url,
            "before_count":len(before),
            "selector_counts":counts,
        }
        try:
            obs=observe(self.page)
            result["generating"]=obs.generating
            result["input_available"]=obs.input_available
            result["observed_assistant_count"]=obs.assistant_count
        except Exception as exc:
            result["observe_error"]=f"{type(exc).__name__}: {exc}"
        if dom_enabled():
            try:
                body=self.page.locator("body").inner_text(timeout=2000)
                result["body_prefix"]=body[:4000]
            except Exception as exc:
                result["body_error"]=f"{type(exc).__name__}: {exc}"
        return result

    def send_and_wait_for_response(self,message:str,**kwargs)->str:
        before=self._assistant_texts()
        self.send_message(message)
        return self.wait_for_response(before=before,**kwargs)

    def send_project_message_and_wait_for_response(self,project_name:str,message:str,**kwargs)->str:
        """Send through the visible Project-home composer without requiring generic chat readiness."""
        if not message.strip(): raise ValueError("message must not be empty")
        composer=self.project_chat_composer(project_name)
        if composer is None:
            raise RuntimeError(f"Project composer is unavailable for: {project_name}")
        before=self._assistant_texts()
        trace("chat.project_send.start", project=project_name, before_count=len(before),
              message_chars=len(message), url=self.page.url)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:
                if composer.is_visible():
                    break
            except Exception:
                pass
            self.page.wait_for_timeout(250)
        else:
            raise RuntimeError(f"Project composer is not visible for: {project_name}")
        deadline=time.monotonic()+15
        last_error=None
        while time.monotonic()<deadline:
            composer=self.project_chat_composer(project_name)
            if composer is None:
                self.page.wait_for_timeout(250)
                continue
            try:
                composer.click(force=True, timeout=1000)
                self.page.keyboard.insert_text(message)
                self.page.wait_for_timeout(100)
                composer.press("Enter",timeout=1000)
                trace("chat.project_send.enter", project=project_name)
                self._wait_for_submission(before,composer,message)
                trace("chat.project_send.submitted", project=project_name, url=self.page.url)
                return self.wait_for_response(before=before,**kwargs)
            except (PlaywrightTimeoutError,PlaywrightError) as exc:
                last_error=exc
                self.page.wait_for_timeout(250)
        detail=f": {last_error}" if last_error else ""
        raise RuntimeError(f"Project composer remained unstable while sending for: {project_name}{detail}")

    def project_context_present(self,project_name:str)->bool:
        if not project_name: return True
        try:
            body=self.page.locator("body").inner_text(timeout=3000)
        except Exception:
            return False
        target=project_name.casefold().strip()
        if not target:
            return True
        escaped=re.escape(target)
        return re.search(r"(?<![\w])" + escaped + r"(?![\w])", body.casefold()) is not None

    def project_chat_composer(self,project_name:str):
        """Return the visible Project-home composer, identified by its rendered placeholder."""
        if not project_name:
            return None
        expected=f"new chat in {project_name}".casefold()
        # Use one CSS union so the same DOM element is not counted once per
        # matching selector. This matters for the placeholder-independent
        # fallback below.
        loc=self.page.locator(
            '#prompt-textarea[contenteditable="true"], '
            '[contenteditable="true"][role="textbox"], '
            '[contenteditable="true"]'
        )
        visible_candidates=[]
        for i in range(loc.count()):
                item=loc.nth(i)
                try:
                    if not item.is_visible():
                        continue
                    visible_candidates.append(item)
                    found=item.evaluate(
                        """(el, expected) => {
                            const values = [
                                el.getAttribute('aria-label') || '',
                                el.getAttribute('placeholder') || '',
                                el.getAttribute('data-placeholder') || ''
                            ];
                            for (const child of el.querySelectorAll('[data-placeholder],[placeholder]')) {
                                values.push(child.getAttribute('data-placeholder') || '');
                                values.push(child.getAttribute('placeholder') || '');
                            }
                            return values.some(v => v.toLowerCase().includes(expected));
                        }""",
                        expected,
                    )
                    if found:
                        return item
                except Exception:
                    continue

        editable=[]
        for item in visible_candidates:
            try:
                if item.is_editable():
                    editable.append(item)
            except Exception:
                continue
        if len(editable)==1:
            return editable[0]
        return None

    def project_chat_composer_present(self,project_name:str)->bool:
        return self.project_chat_composer(project_name) is not None

    def navigate_to_project_home(self,*,project_name:str)->None:
        home=self._project_home_button(project_name)
        if home is None:
            raise RuntimeError(f"visible Project home button not found for: {project_name}")
        home.click()
        self.page.wait_for_timeout(1500)

    def print_project_home_composer_diagnostic(self,project_name:str)->None:
        print(f"URL: {self.page.url}")
        print(f"Title: {self.page.title()}")
        body=self.page.locator("body").inner_text(timeout=5000)
        print("--- body-text ---")
        print(body[:12000])
        print("--- editable-diagnostic ---")
        selectors=(
            '[contenteditable="true"]',
            '[role="textbox"]',
            'textarea',
            'input',
            'button',
        )
        for selector in selectors:
            loc=self.page.locator(selector)
            print(f"selector: {selector} count={loc.count()}")
            for i in range(min(loc.count(),40)):
                item=loc.nth(i)
                try:
                    if not item.is_visible():
                        continue
                    print(
                        f"[{i}] tag={item.evaluate('(el)=>el.tagName.toLowerCase()')} "
                        f"aria={item.get_attribute('aria-label')!r} "
                        f"placeholder={item.get_attribute('placeholder')!r} "
                        f"data-placeholder={item.get_attribute('data-placeholder')!r} "
                        f"role={item.get_attribute('role')!r} "
                        f"testid={item.get_attribute('data-testid')!r}"
                    )
                    print(item.evaluate("(el)=>el.outerHTML.slice(0,4000)"))
                except Exception:
                    continue
        print("--- project-related-buttons ---")
        buttons=self.page.locator("button")
        for i in range(min(buttons.count(),250)):
            b=buttons.nth(i)
            try:
                if not b.is_visible():
                    continue
                combined=" | ".join(
                    x for x in (
                        b.inner_text(timeout=300),
                        b.get_attribute("aria-label"),
                        b.get_attribute("title"),
                        b.get_attribute("data-testid"),
                    ) if x
                ).replace("\n"," ")
                if any(k in combined.casefold() for k in ("project","new chat","send","submit")):
                    print(f"{i}: {combined[:500]}")
            except Exception:
                continue

    def _project_home_button(self, project_name: str):
        if not project_name:
            return None
        option=self.page.locator(
            f'button[aria-label="Open project options for {project_name}"]'
        ).first
        try:
            if option.count() == 0 or not option.is_visible():
                return None
            row=option
            for _ in range(10):
                row=row.locator("xpath=..")
                home=row.locator('button[aria-label="Open project home"]')
                if home.count():
                    text=row.inner_text().strip()
                    if any(line.strip() == project_name for line in text.splitlines()):
                        return home.first
        except Exception:
            return None
        return None

    def start_new_project_chat(self,*,project_name:str|None,project_url:str|None,selector:str|None)->None:
        if project_name:
            home=self._project_home_button(project_name)
            if home is None:
                raise RuntimeError(
                    f"visible Project home button not found for: {project_name}"
                )
            home.click()
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                composer=self.project_chat_composer(project_name)
                if composer is not None:
                    try:
                        if composer.is_visible():
                            break
                    except Exception:
                        pass
                self.page.wait_for_timeout(250)
            else:
                raise RuntimeError(
                    f"Project composer did not become editable for: {project_name}"
                )
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

        if project_name and not self.project_context_present(project_name):
            raise RuntimeError(f"required ChatGPT Project context not detected: {project_name}")
        if project_name:
            if self.project_chat_composer(project_name) is None:
                raise RuntimeError(f"Project composer does not identify a new chat for: {project_name}")
        else:
            self.assert_ready()
        if not project_name:
            self.assert_ready()
