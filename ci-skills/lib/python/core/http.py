"""Bounded JSON HTTP transport for providers without a native API client.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .cli_api import MAX_RESPONSE_BYTES, APIError, _endpoint


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

    def __init__(self, origin: str, *, authorization: str, timeout: int) -> None:
        """Keep selected access in memory without adding it to command arguments.

        :param origin: Validated provider origin.
        :param authorization: Existing resolved credential encoded for HTTP.
        :param timeout: Per-request time bound in seconds.
        """
        self.origin = origin.rstrip("/")
        self._authorization = authorization
        self.timeout = timeout
        self._opener = build_opener(_NoRedirect())

    def request(
        self, method: str, endpoint: str, body: Mapping[str, Any] | None = None
    ) -> Any:
        """Make one bounded request; writes are never blindly retried.

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
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise APIError(f"provider_{exc.code}") from None
        except (URLError, TimeoutError, OSError):
            raise APIError("transport") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise APIError("response_too_large")
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise APIError("invalid_json") from None
