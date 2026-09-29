import json
from io import BytesIO
from urllib.error import HTTPError

import labos_agent.github_observer as observer


def test_observe_github_can_verify_exact_ci_run(monkeypatch):
    calls = []

    def fake_get(url):
        calls.append(url)
        if url.endswith("/repos/owner/repo"):
            return {"default_branch": "main"}
        if url.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "abc1234"}}
        if url.endswith("/actions/runs/166"):
            return {
                "id": 166,
                "status": "completed",
                "conclusion": "success",
                "head_sha": "abc1234",
                "name": "Weather CI",
                "created_at": "2026-09-29T08:00:00Z",
                "html_url": "https://example.test/166",
            }
        raise AssertionError(url)

    monkeypatch.setattr(observer, "_get_json", fake_get)
    result = observer.observe_github("owner/repo", ci_run_id=166)

    assert result.commit_sha == "abc1234"
    assert result.ci_run_id == 166
    assert result.ci_sha == "abc1234"
    assert result.ci_conclusion == "success"
    assert not any("/actions/runs?branch=" in url for url in calls)


def test_get_json_retries_with_gh_auth_after_environment_token_404(monkeypatch):
    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append((request.full_url, request.headers.get("Authorization")))
        if len(attempts) == 1:
            raise HTTPError(request.full_url, 404, "Not Found", {}, BytesIO(b""))
        return _JsonResponse({"ok": True})

    monkeypatch.setenv("GITHUB_TOKEN", "stale-token")
    monkeypatch.setattr(observer, "_gh_auth_token", lambda: "gh-token")
    monkeypatch.setattr(observer, "urlopen", fake_urlopen)

    assert observer._get_json("https://api.github.com/repos/owner/repo") == {"ok": True}
    assert attempts == [
        ("https://api.github.com/repos/owner/repo", "Bearer stale-token"),
        ("https://api.github.com/repos/owner/repo", "Bearer gh-token"),
    ]


class _JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")
