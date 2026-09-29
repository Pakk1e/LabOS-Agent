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
