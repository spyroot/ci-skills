"""Shared bounded CLI API transport for provider callbacks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .runtime import CommandResult, error_class, run_command_bounded

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
READ_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 30
_RETRYABLE = frozenset(
    {"timeout", "transport", "rate_limited", "provider_408", "provider_5xx"}
)
_RETRY_AFTER = re.compile(r"(?im)^retry-after\s*:\s*([^\r\n]+)")


class APIError(RuntimeError):
    """A classified API failure with no response body or credential value."""

    def __init__(self, reason: str, *, attempts: int = 1) -> None:
        """Record a redacted provider failure and retry count.

        :param reason: Classified error reason without response content.
        :param attempts: Number of transport attempts made.
        """
        self.reason = reason
        self.attempts = attempts
        super().__init__(reason)


def _endpoint(value: str) -> str:
    """Reject host changes and path traversal before handing a path to the CLI."""
    parsed = urlsplit(value)
    if (
        not value
        or value.startswith("/")
        or parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or any(part in (".", "..") for part in parsed.path.split("/"))
    ):
        raise APIError("invalid_relative_endpoint")
    return value


def _split_response(stdout: str) -> tuple[str, str]:
    """Separate `CLI api --include` headers from the JSON body."""
    if not stdout.startswith("HTTP/"):
        return "", stdout
    remaining = stdout
    headers = ""
    while remaining.startswith("HTTP/"):
        crlf = remaining.find("\r\n\r\n")
        lf = remaining.find("\n\n")
        choices = [(crlf, 4), (lf, 2)]
        available = [(offset, size) for offset, size in choices if offset >= 0]
        if not available:
            raise APIError("invalid_http_headers")
        offset, size = min(available)
        headers = remaining[:offset]
        remaining = remaining[offset + size :]
    return headers, remaining


def _failure(result: CommandResult, headers: str) -> str:
    """Classify retryable provider HTTP failures without reporting response text."""
    status = re.match(r"HTTP/\S+\s+(\d{3})\b", headers)
    if status:
        code = int(status.group(1))
        if code == 401:
            return "authentication"
        if code == 403:
            return "authorization"
        if code == 408:
            return "provider_408"
        if code == 429:
            return "rate_limited"
        if 400 <= code <= 499:
            return f"provider_{code}"
        if 500 <= code <= 599:
            return "provider_5xx"
    known = error_class(result)
    if known != "command_failed":
        return known
    if re.search(r"\b429\b", result.stderr):
        return "rate_limited"
    if re.search(r"\b408\b", result.stderr):
        return "provider_408"
    if re.search(r"\b5\d\d\b", result.stderr):
        return "provider_5xx"
    return known


def _delay(stderr: str, endpoint: str, attempt: int) -> float:
    """Use Retry-After when surfaced, otherwise bounded backoff with jitter."""
    found = _RETRY_AFTER.search(stderr)
    if found:
        raw = found.group(1).strip()
        if raw.isdecimal():
            seconds = float(raw)
        else:
            try:
                date = parsedate_to_datetime(raw)
                seconds = max(
                    0.0,
                    (date.astimezone(UTC) - datetime.now(UTC)).total_seconds(),
                )
            except (TypeError, ValueError, OverflowError):
                seconds = -1.0
        if seconds >= 0:
            if seconds > MAX_RETRY_AFTER_SECONDS:
                raise APIError("retry_after_exceeds_budget")
            return seconds
    base = 0.2 * 2**attempt
    digest = hashlib.sha256(f"{endpoint}:{attempt}".encode()).digest()
    return base + (digest[0] / 255) * base * 0.2


class CLIAPIClient:
    """Call provider using one bound session and an injectable command runner.

    JSON bodies travel in a mode-0600 temporary file rather than argv, where
    issue text and the one-time runner response must not be exposed. The
    transport never reports raw provider stderr or a response body on failure.
    """

    def __init__(
        self,
        command: Callable[..., CommandResult] = run_command_bounded,
        *,
        timeout: int = 25,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """Bind the provider transport to a command runner and timeout.

        :param command: Injectable command executor for provider requests.
        :param timeout: Positive per-request timeout in seconds.
        :param sleep: Delay function used for bounded retries.
        :raises ValueError: If the timeout is not positive.
        """
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._command = command
        self._sleep = sleep
        self.timeout = timeout

    def argv(self, host: str, method: str, endpoint: str) -> list[str]:
        """Build the provider CLI argument vector.

        :param host: Exact host of the bound credential.
        :param method: HTTP operation selected by the atomic owner.
        :param endpoint: Validated provider-relative API path.
        :returns: Provider CLI arguments, excluding any request body.
        :raises NotImplementedError: If a provider has no CLI adapter.
        """
        raise NotImplementedError("provider_cli_required")

    def get_json(self, session: Any, endpoint: str) -> Any:
        """Call the bound provider through the shared JSON transport.

        :param session: Exact-host authenticated provider session.
        :param endpoint: Relative provider API endpoint.
        :returns: Parsed JSON response.
        :raises APIError: If the provider call or response validation fails.
        """
        return self._request(session, "GET", endpoint)

    def get_json_with_headers(self, session: Any, endpoint: str) -> tuple[Any, str]:
        """Read one JSON page and retain its HTTP pagination headers.

        :param session: Exact-host bound provider session.
        :param endpoint: Relative provider API endpoint.
        :returns: Parsed JSON and response headers from the same read.
        :raises APIError: If the response fails shared transport checks.
        """
        payload, headers = self._request(session, "GET", endpoint, include_headers=True)
        if not headers.startswith("HTTP/"):
            raise APIError("pagination_headers_missing")
        return payload, headers

    def post_json(self, session: Any, endpoint: str, body: dict[str, Any]) -> Any:
        """Call the bound provider through the shared JSON transport.

        :param session: Exact-host authenticated provider session.
        :param endpoint: Relative provider API endpoint.
        :param body: JSON fields passed through a private temporary file.
        :returns: Parsed JSON response.
        :raises APIError: If the provider call or response validation fails.
        """
        return self._request(session, "POST", endpoint, body)

    def put_json(self, session: Any, endpoint: str, body: dict[str, Any]) -> Any:
        """Call the bound provider through the shared JSON transport.

        :param session: Exact-host authenticated provider session.
        :param endpoint: Relative provider API endpoint.
        :param body: JSON fields passed through a private temporary file.
        :returns: Parsed JSON response.
        :raises APIError: If the provider call or response validation fails.
        """
        return self._request(session, "PUT", endpoint, body)

    def delete_json(self, session: Any, endpoint: str) -> Any:
        """Issue a DELETE and accept provider's empty 204 response.

        :param session: Bound provider session.
        :param endpoint: Relative provider API endpoint to delete.
        :returns: Parsed response or ``None`` for an empty response.
        """
        return self._request(session, "DELETE", endpoint)

    def _request(
        self,
        session: Any,
        method: str,
        endpoint: str,
        body: dict[str, Any] | None = None,
        *,
        include_headers: bool = False,
    ) -> Any:
        selected = _endpoint(endpoint)
        argv = self.argv(session.host, method, selected)
        temporary: Path | None = None
        try:
            if body is not None:
                descriptor, name = tempfile.mkstemp(
                    prefix="ci-skills-api-", suffix=".json"
                )
                temporary = Path(name)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                        json.dump(
                            body, handle, ensure_ascii=False, separators=(",", ":")
                        )
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    raise
                argv.extend(
                    (
                        "--header",
                        "Content-Type: application/json",
                        "--input",
                        str(temporary),
                    )
                )
            for attempt in range(READ_ATTEMPTS if method == "GET" else 1):
                result = self._command(
                    argv,
                    timeout=self.timeout,
                    env=dict(session.environment),
                )
                # A timed-out child may have printed only half an HTTP header.
                # Its exit class takes priority over parsing incomplete output.
                if result.returncode == 124:
                    headers, payload_text = "", ""
                else:
                    headers, payload_text = _split_response(result.stdout)
                if result.returncode == 0:
                    break
                reason = _failure(result, headers)
                if method != "GET" or reason not in _RETRYABLE:
                    raise APIError(reason, attempts=attempt + 1)
                if attempt + 1 == READ_ATTEMPTS:
                    raise APIError(reason, attempts=READ_ATTEMPTS)
                self._sleep(_delay(headers or result.stderr, selected, attempt))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        if len(result.stdout.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise APIError("response_too_large")

        if method == "DELETE" and not payload_text.strip():
            return {}

        try:
            payload = json.loads(payload_text)

        except (TypeError, ValueError) as exc:
            raise APIError("invalid_json") from exc
        if not isinstance(payload, (dict, list)):
            raise APIError("invalid_shape")
        return (payload, headers) if include_headers else payload
