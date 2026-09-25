import pytest

from labos_agent.browser.transport import BrowserTransport


def test_browser_transport_retries_transient_connection_failure():
    transport = BrowserTransport(retry_limit=2)
    calls = []

    def operation():
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("websocket disconnected")
        return "ok"

    assert transport.request(
        "req-1",
        operation,
        is_transient=lambda exc: "websocket" in str(exc),
    ) == "ok"
    assert len(calls) == 2


def test_browser_transport_does_not_retry_non_transient_failure():
    transport = BrowserTransport(retry_limit=2)
    calls = []

    def operation():
        calls.append(1)
        raise ValueError("invalid request")

    with pytest.raises(ValueError):
        transport.request("req-2", operation, is_transient=lambda exc: False)
    assert len(calls) == 1
