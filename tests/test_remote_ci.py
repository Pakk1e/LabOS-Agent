import labos_agent.remote_ci as remote_ci


def test_remote_ci_requires_exact_push_run(monkeypatch):
    responses = iter([
        {"workflow_runs": []},
        {"workflow_runs": [{
            "head_sha": "abc",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
            "html_url": "https://github.com/example/run/1",
        }]},
    ])
    monkeypatch.setattr(remote_ci, "_request", lambda url: next(responses))
    monkeypatch.setattr(remote_ci.time, "sleep", lambda seconds: None)
    result = remote_ci.verify_github_actions("owner/repo", "abc", timeout_seconds=1, poll_seconds=0)
    assert result.success
    assert result.found_runs
    assert result.completed
    assert "exact-SHA" in result.summary


def test_remote_ci_rejects_pull_request_run_for_exact_sha(monkeypatch):
    monkeypatch.setattr(remote_ci, "_request", lambda url: {
        "workflow_runs": [{
            "head_sha": "abc",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
        }]
    })
    monkeypatch.setattr(remote_ci.time, "sleep", lambda seconds: None)
    result = remote_ci.verify_github_actions("owner/repo", "abc", timeout_seconds=0, poll_seconds=0)
    assert not result.success
    assert not result.found_runs


def test_remote_ci_rejects_exact_sha_failure(monkeypatch):
    monkeypatch.setattr(remote_ci, "_request", lambda url: {
        "workflow_runs": [{
            "head_sha": "abc",
            "event": "push",
            "status": "completed",
            "conclusion": "failure",
            "name": "CI",
        }]
    })
    result = remote_ci.verify_github_actions("owner/repo", "abc", timeout_seconds=1, poll_seconds=0)
    assert not result.success
    assert result.found_runs
    assert result.completed
    assert "failed" in result.summary


def test_remote_ci_does_not_accept_empty_run_set_as_success(monkeypatch):
    monkeypatch.setattr(remote_ci, "_request", lambda url: {"workflow_runs": []})
    monkeypatch.setattr(remote_ci.time, "sleep", lambda seconds: None)
    result = remote_ci.verify_github_actions("owner/repo", "abc", timeout_seconds=0, poll_seconds=0)
    assert not result.success
    assert not result.found_runs
