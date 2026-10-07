"""Exact-host GitLab API transport used by access checks and operations."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .runtime import CommandResult, error_class, run_command_bounded

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
READ_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 30
CREATE_LOCK_WAIT_SECONDS = 30
_RETRYABLE = frozenset(
    {"timeout", "transport", "rate_limited", "provider_408", "provider_5xx"}
)
_UNCERTAIN_WRITE = frozenset(
    {
        "timeout",
        "transport",
        "provider_408",
        "provider_5xx",
        "response_too_large",
        "invalid_json",
        "invalid_shape",
    }
)
_RETRY_AFTER = re.compile(r"(?im)^retry-after\s*:\s*([^\r\n]+)")
_NEXT_LINK = re.compile(r'<([^<>]+)>\s*;\s*rel\s*=\s*"?next"?', re.IGNORECASE)


class GitLabAPIError(RuntimeError):
    """A classified API failure with no response body or credential value."""

    def __init__(self, reason: str, *, attempts: int = 1) -> None:
        self.reason = reason
        self.attempts = attempts
        super().__init__(reason)


GitLabApiError = GitLabAPIError


def uncertain_write(error: GitLabAPIError) -> bool:
    """A write may have reached GitLab even though its result was lost."""
    return error.reason in _UNCERTAIN_WRITE


def unverified_write(
    target_id: int, resource: dict[str, Any], reason: str
) -> dict[str, Any]:
    """Keep a successful write's known resource in a PARTIAL adapter record."""
    return {
        "action": "APPLIED",
        "verified": False,
        "mutated": True,
        "uncertain": True,
        **resource,
        "errors": [{"project_id": target_id, "reason": reason}],
        "cleanup": {
            "status": "NOT_PERFORMED",
            "reason": "post_write_readback_unverified",
        },
    }


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


def _split_response(stdout: str) -> tuple[str, str]:
    """Separate `glab api --include` headers from the JSON body."""
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
            raise GitLabAPIError("invalid_http_headers")
        offset, size = min(available)
        headers = remaining[:offset]
        remaining = remaining[offset + size :]
    return headers, remaining


def _failure(result: CommandResult, headers: str) -> str:
    """Classify retryable GitLab HTTP failures without reporting response text."""
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
                raise GitLabAPIError("retry_after_exceeds_budget")
            return seconds
    base = 0.2 * 2**attempt
    digest = hashlib.sha256(f"{endpoint}:{attempt}".encode()).digest()
    return base + (digest[0] / 255) * base * 0.2


def _lock_root() -> Path:
    return Path("/tmp") / f"ci-skills-gitlab-locks-{os.getuid()}"


class CreateGuard:
    """A same-host create lock with a crash-persistent uncertain marker."""

    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor
        os.lseek(descriptor, 0, os.SEEK_SET)
        state = os.read(descriptor, 16)
        if state not in (b"", b"pending\n"):
            raise GitLabAPIError("create_lock_invalid_state")
        self.pending = state == b"pending\n"

    def require_ready(self) -> None:
        if self.pending:
            raise GitLabAPIError("create_outcome_uncertain_reconcile_before_retry")

    def _set(self, value: bytes) -> None:
        os.lseek(self._descriptor, 0, os.SEEK_SET)
        os.write(self._descriptor, value)
        os.ftruncate(self._descriptor, len(value))
        os.fsync(self._descriptor)
        self.pending = bool(value)

    def mark_pending(self) -> None:
        self.require_ready()
        self._set(b"pending\n")

    def reconcile_absent(self) -> None:
        """Clear an earlier uncertain create only after a complete empty read."""
        if self.pending:
            self.clear()
            raise GitLabAPIError("create_outcome_uncertain_absent_after_readback_retry")

    def clear(self) -> None:
        if self.pending:
            self._set(b"")


@contextmanager
def _locked_descriptor(identity: Sequence[str | int]) -> Iterator[int]:
    """Lock one resource identity in a private local file.

    :param identity: Exact provider and resource identifiers; never a credential.
    :returns: Open, exclusively locked descriptor for the caller's operation.
    :raises GitLabAPIError: If the lock path is unsafe or acquisition times out.
    """
    root = _lock_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise GitLabAPIError("resource_lock_directory_unsafe")
    encoded = json.dumps(identity, separators=(",", ":"))
    name = hashlib.sha256(encoded.encode("utf-8")).hexdigest() + ".lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root / name, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise GitLabAPIError("resource_lock_file_unsafe")
        deadline = time.monotonic() + CREATE_LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise GitLabAPIError("resource_lock_timeout") from exc
                time.sleep(0.05)
        yield descriptor
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def create_guard(
    origin: str, target_kind: str, target_id: int, kind: str, title: str
) -> Iterator[CreateGuard]:
    """Serialize one creation and retain its crash-recovery marker.

    :param origin: Bound GitLab origin.
    :param target_kind: Project or group scope.
    :param target_id: Access-verified scope ID.
    :param kind: Resource class being created.
    :param title: Server-visible uniqueness key.
    :returns: Guard that records an uncertain POST until reconciliation.
    :raises GitLabAPIError: If the lock or recovery marker is unsafe.
    """
    with _locked_descriptor([origin, target_kind, target_id, kind, title]) as fd:
        yield CreateGuard(fd)


@contextmanager
def runner_update_guard(origin: str, runner_id: int) -> Iterator[None]:
    """Serialize local updates to one runner across selected projects.

    :param origin: Bound GitLab origin.
    :param runner_id: Runner ID shared by every project selection.
    :returns: Control after the resource lock is acquired.
    :raises GitLabAPIError: If the local lock cannot be acquired safely.
    """
    with _locked_descriptor([origin, "runner", runner_id]):
        yield


class GlabAPIClient:
    """Call GitLab using one bound session and an injectable command runner.

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
        """

        :param command:
        :param timeout:
        :param sleep:
        """
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._command = command
        self._sleep = sleep
        self.timeout = timeout

    def get_json(self, session: Any, endpoint: str) -> Any:
        return self._request(session, "GET", endpoint)

    def get_json_with_headers(self, session: Any, endpoint: str) -> tuple[Any, str]:
        """Read one JSON page and retain its HTTP pagination headers.

        :param session: Exact-host bound GitLab session.
        :param endpoint: Relative GitLab API endpoint.
        :returns: Parsed JSON and response headers from the same read.
        :raises GitLabAPIError: If the response fails shared transport checks.
        """
        payload, headers = self._request(session, "GET", endpoint, include_headers=True)
        if not headers.startswith("HTTP/"):
            raise GitLabAPIError("pagination_headers_missing")
        return payload, headers

    def post_json(self, session: Any, endpoint: str, body: dict[str, Any]) -> Any:
        return self._request(session, "POST", endpoint, body)

    def put_json(self, session: Any, endpoint: str, body: dict[str, Any]) -> Any:
        return self._request(session, "PUT", endpoint, body)

    def delete_json(self, session: Any, endpoint: str) -> Any:
        """Issue one DELETE and accept GitLab's empty 204 response."""
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
            "--include",
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
                    raise GitLabAPIError(reason, attempts=attempt + 1)
                if attempt + 1 == READ_ATTEMPTS:
                    raise GitLabAPIError(reason, attempts=READ_ATTEMPTS)
                self._sleep(_delay(headers or result.stderr, selected, attempt))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        if len(result.stdout.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise GitLabAPIError("response_too_large")

        if method == "DELETE" and not payload_text.strip():
            return {}

        try:
            payload = json.loads(payload_text)

        except (TypeError, ValueError) as exc:
            raise GitLabAPIError("invalid_json") from exc
        if not isinstance(payload, (dict, list)):
            raise GitLabAPIError("invalid_shape")
        return (payload, headers) if include_headers else payload


def next_keyset_endpoint(session: Any, current: str, headers: str) -> str | None:
    """Resolve GitLab's next cursor without changing host or collection.

    :param session: Exact-host bound GitLab session.
    :param current: Relative endpoint used for the page just read.
    :param headers: Headers returned with that same page.
    :returns: Safe relative next endpoint, or None at the collection end.
    :raises GitLabAPIError: If the next link changes host, path, or filters.
    """
    links = [
        line[5:].strip()
        for line in headers.splitlines()
        if line.lower().startswith("link:")
    ]
    matches = [match.group(1) for link in links for match in _NEXT_LINK.finditer(link)]
    if not matches:
        return None
    if len(matches) != 1:
        raise GitLabAPIError("pagination_link_invalid")
    parsed = urlsplit(matches[0])
    prefix = "/api/v4/"
    base = urlsplit(_endpoint(current))
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != session.host
        or not parsed.path.startswith(prefix)
        or parsed.fragment
    ):
        raise GitLabAPIError("pagination_link_invalid")
    relative = parsed.path[len(prefix) :]
    if relative != base.path:
        raise GitLabAPIError("pagination_link_invalid")
    current_pairs = parse_qsl(base.query, keep_blank_values=True)
    next_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    cursor_keys = {"id_before", "id_after", "cursor", "page_token"}
    current_filters = Counter(
        pair for pair in current_pairs if pair[0] not in cursor_keys
    )
    next_filters = Counter(pair for pair in next_pairs if pair[0] not in cursor_keys)
    if current_filters != next_filters:
        raise GitLabAPIError("pagination_link_invalid")
    return _endpoint(f"{relative}?{parsed.query}")
