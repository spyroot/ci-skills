"""Exact-host GitLab API transport used by access checks and operations."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .runtime import CommandResult, error_class, run_command

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class GitLabAPIError(RuntimeError):
    """A classified API failure with no response body or credential value."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


GitLabApiError = GitLabAPIError


def _endpoint(value: str) -> str:
    """Reject host changes and path traversal before handing a path to glab."""
    parsed = urlsplit(value)
    if (
        not value
        or value.startswith("/")
        or parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or any(part in (".", "..") for part in parsed.path.split("/"))
    ):
        raise GitLabAPIError("invalid_relative_endpoint")
    return value


class GlabAPIClient:
    """Call GitLab using one bound session and an injectable command runner.

    JSON bodies travel in a mode-0600 temporary file rather than argv, where
    issue text and the one-time runner response must not be exposed. The
    transport never reports raw provider stderr or a response body on failure.
    """

    def __init__(
        self,
        command: Callable[..., CommandResult] = run_command,
        *,
        timeout: int = 25,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._command = command
        self.timeout = timeout

    def get_json(self, session: Any, endpoint: str) -> Any:
        return self._request(session, "GET", endpoint)

    def post_json(self, session: Any, endpoint: str, body: dict[str, Any]) -> Any:
        return self._request(session, "POST", endpoint, body)

    def put_json(self, session: Any, endpoint: str, body: dict[str, Any]) -> Any:
        return self._request(session, "PUT", endpoint, body)

    def _request(
        self,
        session: Any,
        method: str,
        endpoint: str,
        body: dict[str, Any] | None = None,
    ) -> Any:
        selected = _endpoint(endpoint)
        argv = [
            "glab",
            "api",
            selected,
            "--hostname",
            session.host,
            "--method",
            method,
            "--output",
            "json",
        ]
        temporary: Path | None = None
        try:
            if body is not None:
                descriptor, name = tempfile.mkstemp(
                    prefix="ci-skills-gitlab-", suffix=".json"
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
                argv.extend(("--input", str(temporary)))
            result = self._command(
                argv,
                timeout=self.timeout,
                env=dict(session.environment),
            )
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        if result.returncode != 0:
            raise GitLabAPIError(error_class(result))
        if len(result.stdout.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise GitLabAPIError("response_too_large")
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise GitLabAPIError("invalid_json") from exc
        if not isinstance(payload, (dict, list)):
            raise GitLabAPIError("invalid_shape")
        return payload
