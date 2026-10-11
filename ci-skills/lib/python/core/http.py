"""Bounded JSON HTTP transport for providers without a native API client.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .cli_api import (
    MAX_RESPONSE_BYTES,
    READ_ATTEMPTS,
    APIError,
    _endpoint,
    http_failure,
    read_retry_delay,
)
from .runtime import trace_event


class _NoRedirect(HTTPRedirectHandler):
    """Keep provider authentication on the exact selected authority."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
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
    ) -> None:
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
        self, method: str, endpoint: str, body: Mapping[str, Any] | None = None
    ) -> Any:
        """Use bounded retries for safe reads; writes are never blindly retried.

        :param method: Provider HTTP method.
        :param endpoint: Relative path at the selected origin.
        :param body: Optional JSON request document.
        :returns: Parsed JSON, or None for an empty successful response.
        :raises APIError: If transport, HTTP status, response size, or JSON fails.
        """
        path = _endpoint(endpoint)
        data = json.dumps(dict(body)).encode() if body is not None else None
        request = Request(
            f"{self.origin}/{path}",
            data=data,
            method=method,
            headers={
                "Authorization": self._authorization,
                "Content-Type": "application/json",
            },
        )
        for attempt in range(READ_ATTEMPTS if method == "GET" else 1):
            detail = {
                "method": method,
                "origin": self.origin,
                "path": path,
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
                            "status_code": getattr(response, "status", None),
                            "response_bytes": len(raw),
                        },
                    )
                break
            except HTTPError as exc:
                trace_event("http.failed", {**detail, "status_code": exc.code})
                reason = http_failure(exc.code) or f"provider_{exc.code}"
                headers = str(exc.headers or "")
                exc.close()
            except (URLError, TimeoutError, OSError):
                trace_event("http.failed", {**detail, "reason": "transport"})
                reason, headers = "transport", ""
            self._sleep(read_retry_delay(method, reason, headers, path, attempt))
        if len(raw) > MAX_RESPONSE_BYTES:
            raise APIError("response_too_large")
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise APIError("invalid_json") from None
