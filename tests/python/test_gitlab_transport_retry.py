"""Offline GitLab transport retry, size, and write uncertainty contracts.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import TYPE_CHECKING, Optional, TypeAlias

import pytest
from conftest import import_script_module

API = import_script_module("lib.python.core.gitlab.gitlab_api")
RUNTIME = import_script_module("lib.python.core.runtime")

CallRecord: TypeAlias = tuple[tuple[str, ...], int, Mapping[str, Optional[str]]]


if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue


def _session() -> SimpleNamespace:
    """Provide a selected GitLab host and child token environment.

    :returns: Session-shaped fixture for native glab transport calls.
    """
    return SimpleNamespace(
        host="gitlab.example.test", environment={"GITLAB_TOKEN": "selected"}
    )


def _result(code: int, stdout: str = "", stderr: str = "") -> RUNTIME.CommandResult:
    """Construct a synthetic glab command result.

    :param code: Simulated process exit code.
    :param stdout: Simulated response text or HTTP header/body envelope.
    :param stderr: Simulated command diagnostic.
    :returns: Runtime result carrying the supplied status and output.
    """
    return RUNTIME.CommandResult(("glab",), code, stdout, stderr)


def test_malformed_absolute_endpoint_is_classified_before_native_command() -> None:
    """Reject endpoints whose URL parser fails before running ``glab``.
    :returns: None; assertions in the body validate the regression.
    """
    calls: MutableSequence[Sequence[str]] = []

    def command(
        argv: Sequence[str], *, timeout: int, env: Mapping[str, Optional[str]]
    ) -> RUNTIME.CommandResult:
        """Expose accidental native execution after endpoint validation failure.

        :param argv: Native command vector supplied on failure.
        :param timeout: Bounded request timeout supplied on failure.
        :param env: Child environment supplied on failure.
        :returns: Never returns; execution would mean validation regressed.
        :raises AssertionError: Always raised when native execution is reached.
        """
        calls.append(tuple(argv))
        raise AssertionError("malformed endpoint reached native command")

    with pytest.raises(API.GitLabAPIError, match="invalid_relative_endpoint"):
        API.GlabAPIClient(command=command).get_json(_session(), "https://[")

    assert calls == []


def test_get_retries_only_transient_failures_with_unchanged_target_and_retry_after() -> (
    None
):
    """Retry transient GET responses with stable argv and bounded delays.
    :returns: None; assertions in the body validate the regression.
    """
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
    calls: MutableSequence[CallRecord] = []
    sleeps: MutableSequence[float] = []

    def command(
        argv: Sequence[str], *, timeout: int, env: Optional[Mapping[str, Optional[str]]]
    ) -> RUNTIME.CommandResult:
        """Return 503, 429, then success while recording exact retry inputs.

        :param argv: glab command vector used for this attempt.
        :param timeout: Per-attempt timeout recorded for equality checks.
        :param env: Selected child environment recorded for equality checks.
        :returns: Next scripted transport result.
        """
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
def test_authentication_and_authorization_fail_without_retry(status: str) -> None:
    """Treat 401 and 403 as terminal one-attempt access failures.

    :param status: HTTP authentication or authorization response text.

    :returns: None; assertions in the body validate the regression.
    """
    calls: MutableSequence[Sequence[str]] = []

    def command(
        argv: Sequence[str], *, timeout: int, env: Optional[Mapping[str, Optional[str]]]
    ) -> RUNTIME.CommandResult:
        """Return the selected terminal access failure and record the call.

        :param argv: glab command vector for the request.
        :param timeout: Bounded request timeout accepted by the double.
        :param env: Selected child environment accepted by the double.
        :returns: Failed result with the parametrized HTTP status.
        """
        calls.append(argv)
        return _result(1, stderr=f"HTTP {status}")

    client = API.GlabAPIClient(command=command, sleep=lambda _: pytest.fail("slept"))
    with pytest.raises(API.GitLabAPIError) as failure:
        client.get_json(_session(), "projects/42")
    assert failure.value.reason in {"authentication", "authorization"}
    assert failure.value.attempts == 1
    assert len(calls) == 1


def test_get_retry_exhaustion_is_bounded_and_classified() -> None:
    """Stop timed-out GET retries at the declared attempt count.
    :returns: None; assertions in the body validate the regression.
    """
    calls: MutableSequence[Sequence[str]] = []

    def command(
        argv: Sequence[str], *, timeout: int, env: Optional[Mapping[str, Optional[str]]]
    ) -> RUNTIME.CommandResult:
        """Record each GET attempt and return a timeout result.

        :param argv: glab command vector for this attempt.
        :param timeout: Bounded request timeout accepted by the double.
        :param env: Selected child environment accepted by the double.
        :returns: Exit-124 timeout result.
        """
        calls.append(argv)
        return _result(124, stderr="command timed out")

    client = API.GlabAPIClient(command=command, sleep=lambda _: None)
    with pytest.raises(API.GitLabAPIError) as failure:
        client.get_json(_session(), "projects/42")
    assert failure.value.reason == "timeout"
    assert failure.value.attempts == API.READ_ATTEMPTS
    assert len(calls) == API.READ_ATTEMPTS


@pytest.mark.parametrize("status", (400, 404, 409, 422))
def test_other_provider_4xx_is_terminal_even_if_stderr_mentions_5xx(
    status: int,
) -> None:
    """Classify the HTTP response status instead of incidental stderr text.

    :param status: Terminal 400, 404, 409, or 422 response code.

    :returns: None; assertions in the body validate the regression.
    """
    calls: MutableSequence[Sequence[str]] = []

    def command(
        argv: Sequence[str], *, timeout: int, env: Optional[Mapping[str, Optional[str]]]
    ) -> RUNTIME.CommandResult:
        """Return the selected 4xx envelope with misleading 500 stderr.

        :param argv: glab command vector for the request.
        :param timeout: Bounded request timeout accepted by the double.
        :param env: Selected child environment accepted by the double.
        :returns: Failed result with the parametrized provider status.
        """
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


def test_timeout_with_partial_headers_keeps_timeout_class_for_write_reconciliation() -> (
    None
):
    """Keep timeout uncertainty when a POST contains incomplete success headers.
    :returns: None; assertions in the body validate the regression.
    """
    calls: MutableSequence[Sequence[str]] = []

    def command(
        argv: Sequence[str], *, timeout: int, env: Optional[Mapping[str, Optional[str]]]
    ) -> RUNTIME.CommandResult:
        """Return exit 124 with a truncated HTTP 201 header.

        :param argv: glab POST command vector.
        :param timeout: Bounded request timeout accepted by the double.
        :param env: Selected child environment accepted by the double.
        :returns: Timed-out result requiring write reconciliation.
        """
        calls.append(argv)
        return _result(124, "HTTP/2 201 Created\r\nRetry-After:")

    client = API.GlabAPIClient(command=command, sleep=lambda _: pytest.fail("slept"))
    with pytest.raises(API.GitLabAPIError) as failure:
        client.post_json(_session(), "projects/42/issues", {"title": "one"})
    assert failure.value.reason == "timeout"
    assert API.uncertain_write(failure.value)
    assert len(calls) == 1


def test_retry_after_beyond_budget_blocks_instead_of_sleeping_or_replaying() -> None:
    """Reject a 600-second Retry-After without sleeping or replaying GET.
    :returns: None; assertions in the body validate the regression.
    """
    calls: MutableSequence[Sequence[str]] = []

    def command(
        argv: Sequence[str], *, timeout: int, env: Optional[Mapping[str, Optional[str]]]
    ) -> RUNTIME.CommandResult:
        """Return a rate-limit response above the transport delay budget.

        :param argv: glab command vector for the request.
        :param timeout: Bounded request timeout accepted by the double.
        :param env: Selected child environment accepted by the double.
        :returns: HTTP 429 result with 600-second Retry-After.
        """
        calls.append(argv)
        return _result(1, "HTTP/2 429\r\nRetry-After: 600\r\n\r\n{}")

    client = API.GlabAPIClient(command=command, sleep=lambda _: pytest.fail("slept"))
    with pytest.raises(API.GitLabAPIError, match="retry_after_exceeds_budget"):
        client.get_json(_session(), "projects/42")
    assert len(calls) == 1


@pytest.mark.parametrize("method", ("post_json", "put_json", "delete_json"))
def test_uncertain_writes_are_never_retried(method: str) -> None:
    """Require POST, PUT, and DELETE timeout cases to avoid write replay.

    :param method: JSON write method invoked on the native client.

    :returns: None; assertions in the body validate the regression.
    """
    calls: MutableSequence[Sequence[str]] = []

    def command(
        argv: Sequence[str], *, timeout: int, env: Optional[Mapping[str, Optional[str]]]
    ) -> RUNTIME.CommandResult:
        """Record one write attempt and return a timeout.

        :param argv: glab write command vector.
        :param timeout: Bounded request timeout accepted by the double.
        :param env: Selected child environment accepted by the double.
        :returns: Exit-124 result for an uncertain mutation.
        """
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


def test_delete_accepts_empty_success_and_removes_no_body_file() -> None:
    """Accept HTTP 204 DELETE without constructing an input body file.
    :returns: None; assertions in the body validate the regression.
    """
    calls: MutableSequence[Sequence[str]] = []

    def command(
        argv: Sequence[str], *, timeout: int, env: Optional[Mapping[str, Optional[str]]]
    ) -> RUNTIME.CommandResult:
        """Return a no-content DELETE response and record argv.

        :param argv: glab DELETE command vector.
        :param timeout: Bounded request timeout accepted by the double.
        :param env: Selected child environment accepted by the double.
        :returns: Successful HTTP 204 result with an empty body.
        """
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
def test_json_body_write_declares_content_type(method: str) -> None:
    """Require JSON header, exact body, and temporary-file cleanup.

    :param method: POST or PUT JSON transport method.

    :returns: None; assertions in the body validate the regression.
    """
    calls: MutableSequence[Sequence[str]] = []

    def command(
        argv: Sequence[str], *, timeout: int, env: Optional[Mapping[str, Optional[str]]]
    ) -> RUNTIME.CommandResult:
        """Inspect the temporary JSON input and return a successful write.

        :param argv: glab command containing header and input-file options.
        :param timeout: Bounded request timeout accepted by the double.
        :param env: Selected child environment accepted by the double.
        :returns: HTTP 200 response containing the created record ID.
        """
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


@pytest.mark.parametrize("method", ("post_json", "put_json"))
def test_json_body_write_copies_nested_immutable_mapping(method: str) -> None:
    """Serialize nested read-only mappings through a private UTF-8 body file.

    :param method: POST or PUT JSON transport method under test.

    :returns: None; assertions in the body validate the regression.
    """
    calls: MutableSequence[tuple[str, ...]] = []
    body: JSONObject = MappingProxyType(
        {
            "title": "café",
            "metadata": MappingProxyType({"labels": ("one", "two")}),
        }
    )
    expected = {"title": "café", "metadata": {"labels": ["one", "two"]}}

    def command(
        argv: Sequence[str], *, timeout: int, env: Optional[Mapping[str, Optional[str]]]
    ) -> RUNTIME.CommandResult:
        """Read back the native private body file before returning success.

        :param argv: glab command containing the temporary input path.
        :param timeout: Bounded request timeout accepted by the fake runner.
        :param env: Selected child environment accepted by the fake runner.
        :returns: Successful provider response.
        """
        calls.append(tuple(argv))
        payload = Path(argv[argv.index("--input") + 1]).read_bytes()
        assert payload == json.dumps(
            expected,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        assert json.loads(payload.decode("utf-8")) == expected
        return _result(0, 'HTTP/2 200 OK\r\n\r\n{"id":42}')

    result = getattr(API.GlabAPIClient(command=command), method)(
        _session(), "projects/42/issues", body
    )

    assert result == {"id": 42}
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body",
    (
        {"duration": math.inf},
        {"title": "\ud800"},
    ),
    ids=("nonfinite", "surrogate"),
)
def test_json_body_write_rejects_invalid_scalars_before_native_command(
    body: JSONObject,
) -> None:
    """Reject non-JSON numeric and UTF-8 text values before glab starts.

    :param body: Invalid body that cannot be encoded as provider JSON.

    :returns: None; assertions in the body validate the regression.
    """

    def command(
        _argv: Sequence[str],
        *,
        timeout: int,
        env: Optional[Mapping[str, Optional[str]]],
    ) -> None:
        """Expose accidental provider execution for invalid request bodies.

        :param _argv: Native command vector supplied on failure.
        :param timeout: Bounded request timeout supplied on failure.
        :param env: Child environment supplied on failure.

        :returns: None; the helper records behavior through its side effects.
        :raises AssertionError: Always raised when native execution is reached.
        """
        raise AssertionError("invalid JSON body reached native command")

    with pytest.raises(API.GitLabAPIError, match="invalid_request_body"):
        API.GlabAPIClient(command=command).post_json(
            _session(), "projects/42/issues", body
        )


def test_json_body_write_rejects_cycles_before_native_command() -> None:
    """Reject recursive request bodies before a private input file is created.
    :returns: None; assertions in the body validate the regression.
    """
    body: MutableMapping[str, JSONValue] = {}
    body["self"] = body

    def command(
        _argv: Sequence[str],
        *,
        timeout: int,
        env: Optional[Mapping[str, Optional[str]]],
    ) -> None:
        """Expose accidental provider execution for cyclic request bodies.

        :param _argv: Native command vector supplied on failure.
        :param timeout: Bounded request timeout supplied on failure.
        :param env: Child environment supplied on failure.

        :returns: None; the helper records behavior through its side effects.
        :raises AssertionError: Always raised when native execution is reached.
        """
        raise AssertionError("cyclic JSON body reached native command")

    with pytest.raises(API.GitLabAPIError, match="invalid_request_body"):
        API.GlabAPIClient(command=command).post_json(
            _session(), "projects/42/issues", body
        )


def test_json_capture_kills_a_child_before_retaining_unbounded_response() -> None:
    """Stop a child that exceeds stdout cap and retain no oversized body.
    :returns: None; assertions in the body validate the regression.
    """
    result = RUNTIME.run_command_bounded(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000000)"],
        max_stdout_bytes=1024,
        timeout=5,
    )
    assert result.returncode == RUNTIME.OUTPUT_LIMIT_EXIT_CODE
    assert result.stdout == ""
    assert RUNTIME.error_class(result) == "response_too_large"


def test_post_body_file_is_removed_after_timeout() -> None:
    """Remove the JSON input file even when a POST times out.
    :returns: None; assertions in the body validate the regression.
    """
    captured: MutableSequence[Path] = []

    def command(
        argv: Sequence[str], *, timeout: int, env: Optional[Mapping[str, Optional[str]]]
    ) -> RUNTIME.CommandResult:
        """Capture the existing POST body path then return a timeout.

        :param argv: glab POST command containing ``--input``.
        :param timeout: Bounded request timeout accepted by the double.
        :param env: Selected child environment accepted by the double.
        :returns: Exit-124 result for cleanup verification.
        """
        captured.append(Path(argv[argv.index("--input") + 1]))
        assert captured[-1].exists()
        return _result(124)

    with pytest.raises(API.GitLabAPIError, match="timeout"):
        API.GlabAPIClient(command=command).post_json(
            _session(), "projects/42/issues", {"title": "one"}
        )
    assert len(captured) == 1
    assert not captured[0].exists()
