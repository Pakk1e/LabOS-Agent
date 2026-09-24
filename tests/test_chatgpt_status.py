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

    def count(self):
        return 1

    def is_visible(self):
        return True

    def is_editable(self):
        self.editable_checks += 1
        return self.editable_checks >= 3

    def fill(self, value, timeout=None):
        assert timeout == 1000
        self.filled = value

    def press(self, value, timeout=None):
        assert timeout == 1000
        self.pressed = value


class _FakePage:
    def __init__(self, composer):
        self.composer = composer
        self.waits = 0

    def locator(self, selector):
        return self.composer

    def wait_for_timeout(self, milliseconds):
        self.waits += milliseconds


def test_send_message_waits_for_visible_composer_to_become_editable(monkeypatch):
    composer = _FakeComposer()
    page = _FakePage(composer)
    chat = ChatGPTPage(page)
    monkeypatch.setattr(chat, "assert_ready", lambda: None)

    chat.send_message("hello")

    assert composer.filled == "hello"
    assert composer.pressed == "Enter"
    assert page.waits == 500
