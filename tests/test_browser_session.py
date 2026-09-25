from pathlib import Path

import labos_agent.browser.session as session_module
from labos_agent.browser.session import BrowserSession


class FakeBrowserType:
    def __init__(self):
        self.launch_calls = 0
        self.cdp_calls = []

    def launch_persistent_context(self, *args, **kwargs):
        self.launch_calls += 1
        raise AssertionError("CDP session must not launch a new Chromium")

    def connect_over_cdp(self, url):
        self.cdp_calls.append(url)
        return FakeBrowser()


class FakeBrowser:
    contexts = [object()]


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeBrowserType()
        self.stop_calls = 0

    def stop(self):
        self.stop_calls += 1


class FakePlaywrightFactory:
    def __init__(self):
        self.instance = FakePlaywright()

    def start(self):
        return self.instance


def test_context_manager_attaches_over_cdp_without_launching(tmp_path, monkeypatch):
    factory = FakePlaywrightFactory()
    monkeypatch.setattr(session_module, "sync_playwright", lambda: factory)

    session = BrowserSession(Path(tmp_path), cdp_url="http://127.0.0.1:9222")

    with session as attached:
        assert attached.context is not None
        assert factory.instance.chromium.launch_calls == 0
        assert factory.instance.chromium.cdp_calls == ["http://127.0.0.1:9222"]

    assert factory.instance.stop_calls == 1


def test_browser_request_has_identity_and_is_cleared(tmp_path):
    session = BrowserSession(Path(tmp_path), cdp_url="http://127.0.0.1:9222")

    seen = []

    result = session.request(
        lambda request: (seen.append(request), "ok")[1],
        is_transient=lambda exc: False,
    )

    assert result == "ok"
    assert len(seen) == 1
    assert seen[0].request_id
    assert seen[0].attempt == 1
    assert session.active_request_id is None


def test_reconnect_requires_cdp_url(tmp_path):
    session = BrowserSession(Path(tmp_path))
    try:
        session.reconnect()
    except RuntimeError as exc:
        assert "CDP URL" in str(exc)
    else:
        raise AssertionError("expected reconnect without CDP URL to fail")
