from labos_agent.browser.chatgpt import ChatGPTPage


def test_chatgpt_url_detection():
    assert ChatGPTPage._is_chatgpt_url("https://chatgpt.com/")
    assert ChatGPTPage._is_chatgpt_url("https://chatgpt.com/c/abc")
    assert not ChatGPTPage._is_chatgpt_url("https://accounts.google.com/")
    assert not ChatGPTPage._is_chatgpt_url("not-a-url")


class _FakeComposer:
    def __init__(self):
        self.editable_checks = 0
        self.filled = None
        self.pressed = None
        self.clicked = 0

    def count(self):
        return 1

    def is_visible(self):
        return True

    def is_editable(self):
        self.editable_checks += 1
        return self.editable_checks >= 3

    def click(self, force=False, timeout=None):
        assert force is True
        assert timeout == 1000
        self.clicked += 1

    def press(self, value, timeout=None):
        assert timeout == 1000
        self.pressed = value


class _FakeLocatorSet:
    def __init__(self, composer):
        self.first = composer


class _FakePage:
    def __init__(self, composer):
        self.composer = composer
        self.waits = 0

    def locator(self, selector):
        return _FakeLocatorSet(self.composer)

    def wait_for_timeout(self, milliseconds):
        self.waits += milliseconds

    @property
    def keyboard(self):
        return self

    def insert_text(self, value):
        self.composer.filled = value


def test_send_message_uses_keyboard_insertion_for_visible_composer(monkeypatch):
    composer = _FakeComposer()
    page = _FakePage(composer)
    chat = ChatGPTPage(page)
    monkeypatch.setattr(chat, "assert_ready", lambda: None)

    chat.send_message("hello")

    assert composer.filled == "hello"
    assert composer.pressed == "Enter"
    assert composer.clicked == 1
    assert page.waits == 100


def test_project_message_retries_transient_composer_instability(monkeypatch):
    composer = _FakeComposer()
    page = _FakePage(composer)
    chat = ChatGPTPage(page)
    monkeypatch.setattr(chat, "project_chat_composer", lambda project_name: composer)
    monkeypatch.setattr(chat, "_assistant_texts", lambda: [])
    monkeypatch.setattr(
        chat,
        "wait_for_response",
        lambda **kwargs: "response",
    )
    monkeypatch.setattr(
        chat,
        "_wait_for_submission",
        lambda before, composer, message, timeout_seconds=15: None,
    )

    result = chat.send_project_message_and_wait_for_response("Weather", "hello")

    assert result == "response"
    assert composer.filled == "hello"
    assert composer.pressed == "Enter"
    assert composer.clicked == 1


class _FakeTab:
    def __init__(self, url):
        self.url = url


class _FakeContext:
    def __init__(self, *pages):
        self.pages = list(pages)


class _ProjectButton:
    def __init__(self, visible=True):
        self.visible = visible
        self.clicked = 0

    def count(self):
        return 1

    def is_visible(self):
        return self.visible

    def click(self):
        self.clicked += 1


class _ProjectButtonPage:
    def __init__(self):
        self.direct = _ProjectButton()
        self.calls = []

    def locator(self, selector):
        self.calls.append(selector)
        if selector == 'button[aria-label="New chat in Vadovsky Tech — Lab OS"]':
            return type("_Locator", (), {
                "first": type("_First", (), {
                    "count": lambda _self: 1,
                    "is_visible": lambda _self: self.direct.visible,
                })()
            })()
        raise AssertionError(f"unexpected selector: {selector}")


def test_project_home_button_prefers_current_direct_new_chat_control():
    page = _ProjectButtonPage()
    chat = ChatGPTPage(page)
    button = chat._project_home_button("Vadovsky Tech — Lab OS")
    assert button is not None
    assert page.calls[0] == 'button[aria-label="New chat in Vadovsky Tech — Lab OS"]'


class _ProjectRowControl:
    def __init__(self):
        self.parent = _ProjectRow()
    
    def is_visible(self):
        return True
    
    def inner_text(self, timeout=None):
        return ""
    
    def get_attribute(self, name):
        return "New chat" if name == "aria-label" else None
    
    def locator(self, selector):
        assert selector == "xpath=.."
        return self.parent


class _ProjectRow:
    def inner_text(self, timeout=None):
        return "Testing"
    
    def locator(self, selector):
        assert selector == "xpath=.."
        return self


class _ProjectRowControls:
    def __init__(self, control):
        self.control = control
    
    def count(self):
        return 1
    
    def nth(self, index):
        assert index == 0
        return self.control


class _ProjectRowPage:
    def __init__(self):
        self.control = _ProjectRowControl()
    
    def locator(self, selector):
        if selector == 'button[aria-label="New chat in Testing"]':
            return type("_Direct", (), {
                "first": type("_First", (), {
                    "count": lambda _self: 0,
                    "is_visible": lambda _self: False,
                })()
            })()
        if selector == 'button, [role="button"]':
            return _ProjectRowControls(self.control)
        raise AssertionError(f"unexpected selector: {selector}")


def test_project_home_button_finds_generic_new_chat_next_to_project_name():
    page = _ProjectRowPage()
    chat = ChatGPTPage(page)
    button = chat._project_home_button("Testing")
    assert button is page.control


def test_select_page_prefers_exact_project_url():
    wrong = _FakeTab("https://chatgpt.com/c/wrong")
    project = _FakeTab("https://chatgpt.com/g/g-p-weather/project")
    context = _FakeContext(wrong, project)

    selected = ChatGPTPage.select_page(
        context,
        project_name="Vadovsky Tech — Weather",
        project_url="https://chatgpt.com/g/g-p-weather/project",
    )

    assert selected is project


def test_select_page_prefers_project_context_when_urls_are_not_exact(monkeypatch):
    wrong = _FakeTab("https://chatgpt.com/c/wrong")
    project = _FakeTab("https://chatgpt.com/c/project")
    context = _FakeContext(wrong, project)

    monkeypatch.setattr(
        ChatGPTPage,
        "project_context_present",
        lambda self, name: self.page is project,
    )
    monkeypatch.setattr(
        ChatGPTPage,
        "project_chat_composer",
        lambda self, name: None,
    )

    assert ChatGPTPage.select_page(
        context,
        project_name="Vadovsky Tech — Weather",
        project_url="https://chatgpt.com/g/g-p-weather/project",
    ) is project


class _BodyLocator:
    def __init__(self, text):
        self.text = text

    def inner_text(self, timeout=None):
        return self.text


class _ContextPage:
    def __init__(self, text):
        self.body = _BodyLocator(text)

    def locator(self, selector):
        assert selector == "body"
        return self.body


def test_project_context_requires_exact_visible_name():
    chat = ChatGPTPage(_ContextPage("Vadovsky Tech — Weather dashboard\n"))
    assert chat.project_context_present("Vadovsky Tech — Weather")


def test_project_context_rejects_name_as_unrelated_substring():
    chat = ChatGPTPage(_ContextPage("Open Vadovsky Tech — Weathering tools\n"))
    assert not chat.project_context_present("Vadovsky Tech — Weather")


class _TextLocator:
    def __init__(self, texts):
        self.texts = texts

    def all_text_contents(self):
        return self.texts


class _AssistantFallbackPage:
    def __init__(self):
        self.calls = []

    def locator(self, selector):
        self.calls.append(selector)
        if selector == '[data-role="assistant"] .markdown':
            return _TextLocator(["fallback reply"])
        if selector.endswith(" .markdown"):
            return _TextLocator([])
        if selector == '[data-message-author-role="assistant"]':
            return _TextLocator([])
        raise AssertionError(f"unexpected selector: {selector}")


def test_assistant_text_extraction_falls_back_to_rendered_role_selector():
    chat = ChatGPTPage(_AssistantFallbackPage())
    assert chat._assistant_texts() == ["fallback reply"]


def test_continuation_prompt_identifies_github_as_implementation_target(monkeypatch):
    from pathlib import Path
    from labos_agent.project import inspect_project, build_continuation_prompt
    monkeypatch.setattr("labos_agent.project.git_status", lambda root: "")
    snapshot = inspect_project(Path("."), "Pakk1e/VilaPro-Weather", ())
    prompt = build_continuation_prompt(snapshot, "Continue", execution_enabled=True)
    assert "GitHub repository is the implementation target" in prompt
    assert "server checkout is only a controlled working clone" in prompt
    assert "Do not make server-only implementation changes" in prompt


class _TurnFallbackPage:
    def __init__(self):
        self.calls = []

    def locator(self, selector):
        self.calls.append(selector)
        if selector == '[data-testid^="conversation-turn-"][data-turn="assistant"]':
            return _TextLocator(["assistant turn reply"])
        return _TextLocator([])


def test_assistant_text_extraction_falls_back_to_turn_role_selector():
    chat = ChatGPTPage(_TurnFallbackPage())
    assert chat._assistant_texts() == ["assistant turn reply"]


class _RealDomAssistantPage:
    def locator(self, selector):
        if selector == '[data-markdown-text-style="assistant-message"]':
            return _TextLocator(["real assistant markdown"])
        return _TextLocator([])


def test_assistant_text_extraction_uses_real_chatgpt_markdown_marker_first():
    chat = ChatGPTPage(_RealDomAssistantPage())
    assert chat._assistant_texts() == ["real assistant markdown"]


def test_observe_treats_visible_stop_control_as_generating_even_when_input_is_available(monkeypatch):
    from labos_agent.browser.detection import observe

    class _Loc:
        def __init__(self, count=0, visible=True):
            self._count = count
            self._visible = visible

        @property
        def first(self):
            return self

        def count(self):
            return self._count

        def is_visible(self):
            return self._visible

        def evaluate(self, script):
            return "textarea"

        def get_attribute(self, name):
            return "true" if name == "contenteditable" else ("textbox" if name == "role" else None)

        def is_hidden(self):
            return False

        def is_disabled(self):
            return False

        def is_editable(self):
            return True

    class _Page:
        def locator(self, selector):
            if selector.startswith("button"):
                return _Loc(1, True)
            if selector == '[contenteditable="true"][role="textbox"]':
                return _Loc(1, True)
            if "assistant-message" in selector:
                return _Loc(1, True)
            return _Loc(0, False)

    result = observe(_Page())
    assert result.input_available is True
    assert result.stop_control_visible is True
    assert result.generating is True
