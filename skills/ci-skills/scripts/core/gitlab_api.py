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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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


class GitLabAPIError(RuntimeError):
    """A classified API failure with no response body or credential value."""

    # Summary: Preserve a classified reason and attempt count without raw response text.
    # Arguments: reason is failure class, attempts counts calls; Environment inputs: none.
    # Stdout: none; Stderr: none; Exit classes: constructs an exception.
    # Side effects: object state only; Idempotency: same inputs give same fields; Cleanup: none.
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


# Summary: Reject absolute, host-changing, or traversal GitLab API paths.
# Arguments: value is caller endpoint; Environment inputs: none; Stdout: none; Stderr: none.
# Exit classes: GitLabAPIError for unsafe path; Side effects: none.
# Idempotency: same endpoint gives same result; Cleanup: none.
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


# Summary: Split included HTTP headers from a GitLab JSON response.
# Arguments: stdout is bounded glab output; Environment inputs: none; Stdout: none; Stderr: none.
# Exit classes: GitLabAPIError for truncated headers; Side effects: none.
# Idempotency: same response gives same split; Cleanup: none.
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


# Summary: Classify a failed GitLab command without exposing response content.
# Arguments: result is bounded command outcome, headers are included HTTP metadata.
# Environment inputs: none; Stdout: none; Stderr: none; Exit classes: no process exit.
# Side effects: none; Idempotency: same outcome gives same class; Cleanup: none.
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


# Summary: Bound a retry delay from Retry-After or deterministic jitter.
# Arguments: stderr carries header text, endpoint/attempt seed fallback delay.
# Environment inputs: clock for dated Retry-After; Stdout: none; Stderr: none.
# Exit classes: GitLabAPIError when delay exceeds budget; Side effects: none.
# Idempotency: numeric header or fallback is stable, dated header follows clock; Cleanup: none.
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
                    (
                        date.astimezone(timezone.utc) - datetime.now(timezone.utc)
                    ).total_seconds(),
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


# Summary: Name the current user's local GitLab create-lock directory.
# Arguments: none; Environment inputs: process UID; Stdout: none; Stderr: none.
# Exit classes: no process exit; Side effects: none, path is not created here.
# Idempotency: stable for same UID; Cleanup: none.
def _lock_root() -> Path:
    return Path("/tmp") / f"ci-skills-gitlab-locks-{os.getuid()}"


class CreateGuard:
    """A same-host create lock with a crash-persistent uncertain marker."""

    # Summary: Read one locked descriptor's pending marker into guard state.
    # Arguments: descriptor is an open lock file; Environment inputs: current file bytes.
    # Stdout: none; Stderr: none; Exit classes: GitLabAPIError for invalid marker.
    # Side effects: seeks and reads descriptor; Idempotency: follows marker bytes.
    # Cleanup: caller owns descriptor and lock release.
    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor
        os.lseek(descriptor, 0, os.SEEK_SET)
        state = os.read(descriptor, 16)
        if state not in (b"", b"pending\n"):
            raise GitLabAPIError("create_lock_invalid_state")
        self.pending = state == b"pending\n"

    # Summary: Refuse a second create while an earlier POST remains uncertain.
    # Arguments: self holds marker state; Environment inputs: none; Stdout: none; Stderr: none.
    # Exit classes: GitLabAPIError for pending create; Side effects: none.
    # Idempotency: same guard state gives same outcome; Cleanup: caller owns lock.
    def require_ready(self) -> None:
        if self.pending:
            raise GitLabAPIError("create_outcome_uncertain_reconcile_before_retry")

    # Summary: Write and sync the guard's exact marker bytes.
    # Arguments: value is pending marker or empty bytes; Environment inputs: lock descriptor.
    # Stdout: none; Stderr: none; Exit classes: OSError for failed write or sync.
    # Side effects: updates lock file; Idempotency: same value yields same marker.
    # Cleanup: caller owns descriptor and lock release.
    def _set(self, value: bytes) -> None:
        os.lseek(self._descriptor, 0, os.SEEK_SET)
        os.write(self._descriptor, value)
        os.ftruncate(self._descriptor, len(value))
        os.fsync(self._descriptor)
        self.pending = bool(value)

    # Summary: Mark one validated create as in flight before sending POST.
    # Arguments: self holds lock state; Environment inputs: lock descriptor.
    # Stdout: none; Stderr: none; Exit classes: GitLabAPIError if already pending.
    # Side effects: writes pending marker; Idempotency: repeat is refused.
    # Cleanup: caller owns descriptor and lock release.
    def mark_pending(self) -> None:
        self.require_ready()
        self._set(b"pending\n")

    # Summary: Clear an uncertain marker after a complete empty resource read.
    # Arguments: self holds lock state; Environment inputs: lock descriptor.
    # Stdout: none; Stderr: none; Exit classes: GitLabAPIError requests a safe retry.
    # Side effects: may clear marker; Idempotency: empty state is no-op.
    # Cleanup: caller owns descriptor and lock release.
    def reconcile_absent(self) -> None:
        """Clear an earlier uncertain create only after a complete empty read."""
        if self.pending:
            self.clear()
            raise GitLabAPIError("create_outcome_uncertain_absent_after_readback_retry")

    # Summary: Remove a pending marker after verified resource state.
    # Arguments: self holds lock state; Environment inputs: lock descriptor.
    # Stdout: none; Stderr: none; Exit classes: OSError if marker write fails.
    # Side effects: may clear lock file; Idempotency: empty state is no-op.
    # Cleanup: caller owns descriptor and lock release.
    def clear(self) -> None:
        if self.pending:
            self._set(b"")


# Summary: Serialize one exact-title GitLab create across local processes.
# Arguments: origin, target_kind, target_id, kind, title bind lock identity.
# Environment inputs: process UID and local lock files; Stdout: none; Stderr: none.
# Exit classes: GitLabAPIError or OSError; Side effects: directory, lock file, and flock.
# Idempotency: same identity shares a lock, pending marker persists intentionally.
# Cleanup: unlocks and closes descriptor, lock file and marker remain.
@contextmanager
def create_guard(
    origin: str, target_kind: str, target_id: int, kind: str, title: str
) -> Iterator[CreateGuard]:
    """Serialize one title's search/create/read-back across local processes.

    Locks are scoped by exact host and numeric target. No title is written to
    the lock file. A create started but not independently verified leaves a
    pending marker, so a later invocation may read back but cannot send a new
    POST when the first POST's outcome remains unknown.
    """
    root = _lock_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise GitLabAPIError("create_lock_directory_unsafe")
    identity = json.dumps(
        [origin, target_kind, target_id, kind, title], separators=(",", ":")
    )
    name = hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root / name, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise GitLabAPIError("create_lock_file_unsafe")
        deadline = time.monotonic() + CREATE_LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise GitLabAPIError("create_lock_timeout") from exc
                time.sleep(0.05)
        yield CreateGuard(descriptor)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


class GlabAPIClient:
    """Call GitLab using one bound session and an injectable command runner.

    JSON bodies travel in a mode-0600 temporary file rather than argv, where
    issue text and the one-time runner response must not be exposed. The
    transport never reports raw provider stderr or a response body on failure.
    """

    # Summary: Configure bounded GitLab command transport and retry timing.
    # Arguments: command runs glab, timeout bounds it, sleep injects retry delay.
    # Environment inputs: none; Stdout: none; Stderr: none; Exit classes: ValueError for timeout.
    # Side effects: client state only; Idempotency: same inputs give same state; Cleanup: none.
    def __init__(
        self,
        command: Callable[..., CommandResult] = run_command_bounded,
        *,
        timeout: int = 25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._command = command
        self._sleep = sleep
        self.timeout = timeout

    def get_json(self, session: Any, endpoint: str) -> Any:
        return self._request(session, "GET", endpoint)

    def post_json(self, session: Any, endpoint: str, body: dict[str, Any]) -> Any:
        return self._request(session, "POST", endpoint, body)

    def put_json(self, session: Any, endpoint: str, body: dict[str, Any]) -> Any:
        return self._request(session, "PUT", endpoint, body)

    def delete_json(self, session: Any, endpoint: str) -> Any:
        """Issue one DELETE and accept GitLab's empty 204 response."""
        return self._request(session, "DELETE", endpoint)

    # Summary: Run one exact-host API request with bounded GET retry and JSON validation.
    # Arguments: session binds host/auth, method/endpoint select call, body is optional JSON.
    # Environment inputs: bound session and temporary file directory.
    # Stdout: none, returns parsed payload; Stderr: none, external diagnostics classified.
    # Exit classes: GitLabAPIError or OSError; Side effects: glab request and optional temp file.
    # Idempotency: GET follows server state, writes are sent once; Cleanup: temp file removed.
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
        return payload
