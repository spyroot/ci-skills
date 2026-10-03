"""Offline GitLab transport retry, size, and write uncertainty contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import import_script_module

API = import_script_module("core.gitlab_api")
RUNTIME = import_script_module("core.runtime")


def _session():
    return SimpleNamespace(
        host="gitlab.example.test", environment={"GITLAB_TOKEN": "selected"}
    )


def _result(code: int, stdout: str = "", stderr: str = ""):
    return RUNTIME.CommandResult(("glab",), code, stdout, stderr)


def test_get_retries_only_transient_failures_with_unchanged_target_and_retry_after():
    outcomes = iter(
        (
            _result(1, stderr="HTTP 503 service unavailable"),
            _result(
                1,
                "HTTP/2 429 Too Many Requests\r\nRetry-After: 1\r\n\r\n{}",
            ),
            _result(
                0, 'HTTP/2 200 OK\r\ncontent-type: application/json\r\n\r\n{"id":42}'
            ),
        )
    )
    calls = []
    sleeps = []

    def command(argv, *, timeout, env):
        calls.append((tuple(argv), timeout, dict(env)))
        return next(outcomes)

    client = API.GlabAPIClient(command=command, sleep=sleeps.append)
    assert client.get_json(_session(), "projects/42") == {"id": 42}
    assert len(calls) == 3
    assert all(call == calls[0] for call in calls)
    assert "--include" in calls[0][0]
    assert "--header" not in calls[0][0]
    assert 0.2 <= sleeps[0] <= 0.24
    assert sleeps[1] == 1


@pytest.mark.parametrize("status", ("401 unauthorized", "403 forbidden"))
def test_authentication_and_authorization_fail_without_retry(status):
    calls = []

    def command(argv, *, timeout, env):
        calls.append(argv)
        return _result(1, stderr=f"HTTP {status}")

    client = API.GlabAPIClient(command=command, sleep=lambda _: pytest.fail("slept"))
    with pytest.raises(API.GitLabAPIError) as failure:
        client.get_json(_session(), "projects/42")
    assert failure.value.reason in {"authentication", "authorization"}
    assert failure.value.attempts == 1
    assert len(calls) == 1


def test_get_retry_exhaustion_is_bounded_and_classified():
    calls = []

    def command(argv, *, timeout, env):
        calls.append(argv)
        return _result(124, stderr="command timed out")

    client = API.GlabAPIClient(command=command, sleep=lambda _: None)
    with pytest.raises(API.GitLabAPIError) as failure:
        client.get_json(_session(), "projects/42")
    assert failure.value.reason == "timeout"
    assert failure.value.attempts == API.READ_ATTEMPTS
    assert len(calls) == API.READ_ATTEMPTS


@pytest.mark.parametrize("status", (400, 404, 409, 422))
def test_other_provider_4xx_is_terminal_even_if_stderr_mentions_5xx(status):
    calls = []

    def command(argv, *, timeout, env):
        calls.append(argv)
        return _result(
            1,
            f"HTTP/2 {status} Error\r\ncontent-type: application/json\r\n\r\n{{}}",
            "upstream 500 detail is not the response status",
        )

    client = API.GlabAPIClient(command=command, sleep=lambda _: pytest.fail("slept"))
    with pytest.raises(API.GitLabAPIError) as failure:
        client.get_json(_session(), "projects/42")
    assert failure.value.reason == f"provider_{status}"
    assert len(calls) == 1


def test_timeout_with_partial_headers_keeps_timeout_class_for_write_reconciliation():
    calls = []

    def command(argv, *, timeout, env):
        calls.append(argv)
        return _result(124, "HTTP/2 201 Created\r\nRetry-After:")

    client = API.GlabAPIClient(command=command, sleep=lambda _: pytest.fail("slept"))
    with pytest.raises(API.GitLabAPIError) as failure:
        client.post_json(_session(), "projects/42/issues", {"title": "one"})
    assert failure.value.reason == "timeout"
    assert API.uncertain_write(failure.value)
    assert len(calls) == 1


def test_retry_after_beyond_budget_blocks_instead_of_sleeping_or_replaying():
    calls = []

    def command(argv, *, timeout, env):
        calls.append(argv)
        return _result(1, "HTTP/2 429\r\nRetry-After: 600\r\n\r\n{}")

    client = API.GlabAPIClient(command=command, sleep=lambda _: pytest.fail("slept"))
    with pytest.raises(API.GitLabAPIError, match="retry_after_exceeds_budget"):
        client.get_json(_session(), "projects/42")
    assert len(calls) == 1


@pytest.mark.parametrize("method", ("post_json", "put_json", "delete_json"))
def test_uncertain_writes_are_never_retried(method):
    calls = []

    def command(argv, *, timeout, env):
        calls.append(argv)
        return _result(124, stderr="command timed out")

    client = API.GlabAPIClient(command=command, sleep=lambda _: pytest.fail("slept"))
    action = getattr(client, method)
    with pytest.raises(API.GitLabAPIError, match="timeout"):
        if method == "delete_json":
            action(_session(), "projects/42/runners/7")
        else:
            action(_session(), "projects/42/issues", {"title": "one"})
    assert len(calls) == 1


def test_delete_accepts_empty_success_and_removes_no_body_file():
    calls = []

    def command(argv, *, timeout, env):
        calls.append(argv)
        return _result(0, "HTTP/2 204 No Content\r\ncontent-length: 0\r\n\r\n")

    assert (
        API.GlabAPIClient(command=command).delete_json(
            _session(), "projects/42/runners/7"
        )
        == {}
    )
    assert calls[0][calls[0].index("--method") + 1] == "DELETE"
    assert "--input" not in calls[0]
    assert "--header" not in calls[0]


@pytest.mark.parametrize("method", ("post_json", "put_json"))
def test_json_body_write_declares_content_type(method):
    calls = []

    def command(argv, *, timeout, env):
        calls.append(tuple(argv))
        assert argv[argv.index("--header") + 1] == "Content-Type: application/json"
        with Path(argv[argv.index("--input") + 1]).open(encoding="utf-8") as body:
            assert json.load(body) == {"title": "one"}
        return _result(0, 'HTTP/2 200 OK\r\n\r\n{"id":42}')

    result = getattr(API.GlabAPIClient(command=command), method)(
        _session(), "projects/42/issues", {"title": "one"}
    )
    assert result == {"id": 42}
    assert len(calls) == 1


def test_json_capture_kills_a_child_before_retaining_unbounded_response():
    result = RUNTIME.run_command_bounded(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000000)"],
        max_stdout_bytes=1024,
        timeout=5,
    )
    assert result.returncode == RUNTIME.OUTPUT_LIMIT_EXIT_CODE
    assert result.stdout == ""
    assert RUNTIME.error_class(result) == "response_too_large"


def test_post_body_file_is_removed_after_timeout():
    captured = []

    def command(argv, *, timeout, env):
        captured.append(Path(argv[argv.index("--input") + 1]))
        assert captured[-1].exists()
        return _result(124)

    with pytest.raises(API.GitLabAPIError, match="timeout"):
        API.GlabAPIClient(command=command).post_json(
            _session(), "projects/42/issues", {"title": "one"}
        )
    assert len(captured) == 1
    assert not captured[0].exists()
