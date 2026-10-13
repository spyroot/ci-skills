"""Bounded JSON HTTP transport for providers without a native API client.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from http.client import HTTPMessage
from typing import IO, Optional
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from lib.python.core.cli_api import (
    MAX_RESPONSE_BYTES,
    APIError,
    http_failure,
    read_retry_delay,
    response_decoder,
    validate_endpoint,
)
from lib.python.core.json_types import JSONObject, copy_json_object
from lib.python.core.runtime import trace_event


class _NoRedirect(HTTPRedirectHandler):
    """Keep provider authentication on the exact selected authority."""

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        """Leave redirects as HTTP failures instead of forwarding credentials.

        :param req: Original urllib request.
        :param fp: Provider response stream.
        :param code: HTTP redirect code.
        :param msg: Provider response message.
        :param headers: Response headers.
        :param newurl: Proposed redirect location.
        :returns: None, disabling automatic redirection.
        """
        return


class HTTPClient:
    """Bind JSON requests to one origin and a private authentication header."""

    def __init__(
        self,
        origin: str,
        *,
        authorization: str,
        timeout: int,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """Keep selected access in memory without adding it to command arguments.

        :param origin: Validated provider origin.
        :param authorization: Existing resolved credential encoded for HTTP.
        :param timeout: Per-request time bound in seconds.
        :param sleep: Injectable delay for the shared bounded read retry policy.
        """
        self.origin = origin.rstrip("/")
        self._authorization = authorization
        self.timeout = timeout
        self._sleep = sleep
        self._opener = build_opener(_NoRedirect())

    def request(
        self,
        method: str,
        endpoint: str,
        body: Optional[JSONObject] = None,
    ) -> JSONObject:
        """Request one JSON object from the bound provider.

        :param method: HTTP method; only GET requests may be retried.
        :param endpoint: Relative API path within the selected provider origin.
        :param body: JSON-compatible request fields; ``None`` sends no body.
        :returns: Decoded response object as a mapping; an empty successful
            response body produces an empty mapping.
        :raises APIError: If the endpoint or request body is invalid, the
            request cannot complete within the retry limits, or the response
            exceeds the byte limit or is not a JSON object.
        """  # noqa: DOC502 - Request validation and transport/decode helpers can fail.
        path = validate_endpoint(endpoint)
        request = Request(
            f"{self.origin}/{path}",
            data=self._encode_body(body),
            method=method,
            headers={
                "Authorization": self._authorization,
                "Content-Type": "application/json",
            },
        )
        return self._decode_response(self._read_response(request, path))

    @staticmethod
    def _encode_body(body: Optional[JSONObject]) -> Optional[bytes]:
        """Encode request fields while retaining the no-body selection.

        :param body: JSON-compatible fields; ``None`` selects no request body.
        :returns: UTF-8 JSON bytes, or ``None`` when no body was selected.
        :raises APIError: If fields contain unsupported values, nonfinite
            numbers, circular references, or excessive nesting.
        """
        if body is None:
            return None
        try:
            return json.dumps(copy_json_object(body), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise APIError("invalid_request_body") from None

    def _read_response(self, request: Request, endpoint: str) -> bytes:
        """Read response bytes using the shared bounded GET retry policy.

        :param request: Prepared urllib request with bound authorization.
        :param endpoint: Validated relative path included in diagnostic traces.
        :returns: Successful response bytes, bounded by the shared size limit.
        :raises APIError: If an HTTP or connection failure is terminal, retry
            limits are reached, or the response exceeds the byte limit.
        """
        method = request.get_method()
        attempt = 0
        while True:
            detail = {
                "method": method,
                "origin": self.origin,
                "path": endpoint,
                "attempt": attempt + 1,
            }
            trace_event("http.started", detail)
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    trace_event(
                        "http.completed",
                        {
                            **detail,
                            "status_code": response.status,
                            "response_bytes": len(raw),
                        },
                    )
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise APIError("response_too_large")
                return raw
            except HTTPError as exc:
                trace_event("http.failed", {**detail, "status_code": exc.code})
                reason = http_failure(exc.code) or f"provider_{exc.code}"
                headers = str(exc.headers or "")
                exc.close()
            except (URLError, TimeoutError, OSError):
                trace_event("http.failed", {**detail, "reason": "transport"})
                reason, headers = "transport", ""
            self._sleep(read_retry_delay(method, reason, headers, endpoint, attempt))
            attempt += 1

    @staticmethod
    def _decode_response(raw: bytes) -> JSONObject:
        """Decode an object response or normalize a successful empty body.

        :param raw: Response bytes within the transport's size limit.
        :returns: Decoded JSON object; empty or whitespace-only bytes produce
            an empty mapping.
        :raises APIError: If bytes are invalid UTF-8, invalid or excessively
            nested JSON, or contain an array, scalar, or JSON null instead of an object.
        """
        if not raw.strip():
            return {}
        try:
            value = response_decoder().decode(raw.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            raise APIError("invalid_json") from None
        if not isinstance(value, Mapping):
            raise APIError("invalid_shape")
        return value
