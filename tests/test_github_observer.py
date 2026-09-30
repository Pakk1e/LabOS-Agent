import json
from io import BytesIO
from urllib.error import HTTPError

import labos_agent.github_observer as observer


def test_observe_github_uses_gh_for_latest_ci_run(monkeypatch):
    def fake_get(url):
        if url.endswith("/repos/owner/repo"):
            return {"default_branch": "main"}
        if url.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "abc1234"}}
        raise AssertionError(url)

    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["gh", "api", "repos/owner/repo/"]:
            return _Completed(json.dumps({"default_branch": "main"}))
        if command[:3] == ["gh", "api", "repos/owner/repo/branches/main"]:
            return _Completed(json.dumps({"commit": {"sha": "abc1234"}}))
        return _Completed(
            json.dumps([{
                "databaseId": 166,
                "number": 42,
                "status": "completed",
                "conclusion": "success",
                "headSha": "abc1234",
                "name": "Weather CI",
                "createdAt": "2026-09-29T08:00:00Z",
                "url": "https://example.test/166",
            }])
        )

    monkeypatch.setattr(observer, "_get_json", fake_get)
    monkeypatch.setattr(observer.subprocess, "run", fake_run)

    result = observer.observe_github("owner/repo")

    assert result.commit_sha == "abc1234"
    assert result.ci_run_id == 166
    assert result.ci_sha == "abc1234"
    assert result.ci_conclusion == "success"
    assert calls[:2] == [
        ["gh", "api", "repos/owner/repo/"],
        ["gh", "api", "repos/owner/repo/branches/main"],
    ]


def test_observe_github_resolves_workflow_run_number(monkeypatch):
    def fake_get(url):
        if url.endswith("/repos/owner/repo"):
            return {"default_branch": "main"}
        if url.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "abc1234"}}
        raise AssertionError(url)

    def fake_run(command, **kwargs):
        if command[:3] == ["gh", "api", "repos/owner/repo/"]:
            return _Completed(json.dumps({"default_branch": "main"}))
        if command[:3] == ["gh", "api", "repos/owner/repo/branches/main"]:
            return _Completed(json.dumps({"commit": {"sha": "abc1234"}}))
        return _Completed(json.dumps([
            {
                "databaseId": 166,
                "number": 42,
                "status": "completed",
                "conclusion": "success",
                "headSha": "abc1234",
                "name": "Weather CI",
                "createdAt": "2026-09-29T08:00:00Z",
                "url": "https://example.test/166",
            },
            {
                "databaseId": 165,
                "number": 41,
                "status": "completed",
                "conclusion": "success",
                "headSha": "other-sha",
                "name": "Weather CI",
                "createdAt": "2026-09-29T07:00:00Z",
                "url": "https://example.test/165",
            },
        ]))

    monkeypatch.setattr(observer, "_get_json", fake_get)
    monkeypatch.setattr(observer.subprocess, "run", fake_run)

    result = observer.observe_github("owner/repo", ci_run_id=42)

    assert result.ci_run_id == 166
    assert result.ci_sha == "abc1234"


def test_observe_github_uses_gh_api_for_repository_state(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command == ["gh", "api", "repos/owner/repo/"]:
            return _Completed(json.dumps({"default_branch": "main"}))
        if command == ["gh", "api", "repos/owner/repo/branches/main"]:
            return _Completed(json.dumps({"commit": {"sha": "deadbeef"}}))
        return _Completed("[]")

    monkeypatch.setattr(observer.subprocess, "run", fake_run)

    result = observer.observe_github("owner/repo")

    assert result.branch == "main"
    assert result.commit_sha == "deadbeef"
    assert calls[:2] == [
        ["gh", "api", "repos/owner/repo/"],
        ["gh", "api", "repos/owner/repo/branches/main"],
    ]


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


class _Completed:
    def __init__(self, stdout):
        self.stdout = stdout


class _JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")
