"""Focused HTTP transport contracts independent of provider workflows.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import MutableMapping, MutableSequence, Sequence
from types import MappingProxyType, TracebackType
from typing import TYPE_CHECKING, Optional, Self
from urllib.request import Request

import pytest
from conftest import import_script_module

HTTP = import_script_module("lib.python.protocol.http")


if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue


class _RawResponse:
    """Minimal successful urllib response returning exact bytes.

    .. attribute :: status
        :type: int

        HTTP status code exposed by urllib responses.
    """

    status: int = 200

    def __init__(self, payload: bytes):
        """Store raw response bytes for a bounded client read.

        :param payload: Provider response bytes returned by ``read``.
        """
        self.payload = payload
        self.limits: MutableSequence[int] = []

    def __enter__(self) -> Self:
        """Enter the context manager used by urllib responses.

        :returns: This fake response.
        """
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Exit without suppressing exceptions.

        :param exc_type: Exception class supplied by the context manager, if any.
        :param exc: Exception instance supplied by the context manager, if any.
        :param traceback: Traceback supplied by the context manager, if any.
        :returns: None.
        """
        return

    def read(self, limit: int) -> bytes:
        """Return the configured payload and record the requested limit.

        :param limit: Maximum byte count requested by ``HTTPClient``.
        :returns: Configured response bytes.
        """
        self.limits.append(limit)
        return self.payload


class _Opener:
    """Return scripted responses while recording prepared requests."""

    def __init__(self, responses: Sequence[_RawResponse]):
        """Retain the finite response sequence.

        :param responses: Successful responses returned in request order.
        """
        self.responses = iter(responses)
        self.requests: MutableSequence[Request] = []

    def open(self, request: Request, *, timeout: int) -> _RawResponse:
        """Record one prepared request and return the next response.

        :param request: Prepared urllib request.
        :param timeout: Per-request timeout selected by the client.
        :returns: Next scripted response.
        """
        assert timeout == 7
        self.requests.append(request)
        return next(self.responses)


def _client(response: _RawResponse) -> tuple[HTTP.HTTPClient, _Opener]:
    """Create an HTTP client whose network opener is fully mocked.

    :param response: Single response returned by the fake opener.
    :returns: Bound client and its recording opener.
    """
    opener = _Opener([response])
    client = HTTP.HTTPClient(
        "https://harbor.example.test",
        authorization="Basic hidden",
        timeout=7,
        sleep=lambda _delay: pytest.fail("successful request must not sleep"),
    )
    client._opener = opener
    return client, opener


@pytest.mark.parametrize(
    ("body", "expected_data"),
    (
        (None, None),
        (MappingProxyType({}), b"{}"),
    ),
    ids=("absent-body", "empty-object-body"),
)
def test_http_request_distinguishes_absent_body_from_empty_object(
    body: Optional[JSONObject], expected_data: Optional[bytes]
) -> None:
    """Keep a no-body request distinct from an explicit empty JSON object.

    :param body: Request body passed to ``HTTPClient``.
    :param expected_data: Prepared request data expected by urllib.

    :returns: None; assertions in the body validate the regression.
    """
    client, opener = _client(_RawResponse(b""))

    assert client.request("POST", "artifacts/unit", body) == {}
    assert len(opener.requests) == 1
    assert opener.requests[0].data == expected_data


@pytest.mark.parametrize(
    ("payload", "reason"),
    (
        (b"[{}]", "invalid_shape"),
        (b'"scalar"', "invalid_shape"),
        (b"null", "invalid_shape"),
        (b'{"unterminated":', "invalid_json"),
        (b'{"a":1,"a":2}', "invalid_json"),
        (b'{"duration":1e999}', "invalid_json"),
        (b'{"duration":NaN}', "invalid_json"),
    ),
    ids=(
        "array",
        "scalar",
        "null",
        "malformed",
        "duplicate-key",
        "overflow-number",
        "nan",
    ),
)
def test_http_response_rejects_non_object_or_invalid_json(
    payload: bytes, reason: str
) -> None:
    """Reject malformed JSON, duplicate keys, nonfinite numbers, and non-objects.

    :param payload: Raw provider response bytes returned by the fake opener.
    :param reason: Expected APIError reason.

    :returns: None; assertions in the body validate the regression.
    """
    client, opener = _client(_RawResponse(payload))

    with pytest.raises(HTTP.APIError, match=reason):
        client.request("GET", "artifacts/unit")

    assert len(opener.requests) == 1


def test_http_request_normalizes_nested_readonly_body_before_encoding() -> None:
    """Encode nested immutable mappings without mutating caller-owned input.

    :returns: None.
    """
    body = MappingProxyType(
        {
            "metadata": MappingProxyType(
                {"labels": MappingProxyType({"managed-by": "unit"})}
            ),
            "items": (MappingProxyType({"name": "artifact"}),),
        }
    )
    client, opener = _client(_RawResponse(b"{}"))

    assert client.request("PUT", "artifacts/unit", body) == {}
    assert len(opener.requests) == 1
    assert opener.requests[0].data == (
        b'{"metadata": {"labels": {"managed-by": "unit"}}, '
        b'"items": [{"name": "artifact"}]}'
    )
    assert body["metadata"]["labels"]["managed-by"] == "unit"


def test_http_request_classifies_cyclic_body_before_opening_transport() -> None:
    """Reject a cyclic JSON body as an invalid request without provider I/O.

    :returns: None.
    """
    cyclic: MutableMapping[str, JSONValue] = {}
    cyclic["self"] = cyclic
    client, opener = _client(_RawResponse(b'{"ignored":true}'))

    with pytest.raises(HTTP.APIError, match="invalid_request_body"):
        client.request("POST", "artifacts/unit", cyclic)

    assert opener.requests == []


def test_http_request_rejects_nonfinite_body_before_opening_transport() -> None:
    """Reject outbound ``NaN`` without sending a provider request.
    :returns: None; assertions in the body validate the regression.
    """
    client, opener = _client(_RawResponse(b'{"ignored":true}'))

    with pytest.raises(HTTP.APIError, match="invalid_request_body"):
        client.request(
            "POST",
            "artifacts/unit",
            MappingProxyType({"duration": float("nan")}),
        )

    assert opener.requests == []
