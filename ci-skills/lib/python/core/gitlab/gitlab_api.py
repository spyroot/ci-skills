"""Exact-host GitLab API transport used by access checks and operations."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from lib.python.core.cli_api import (
    MAX_RESPONSE_BYTES as MAX_RESPONSE_BYTES,  # noqa: PLC0414 - compatibility export
)
from lib.python.core.cli_api import (
    MAX_RETRY_AFTER_SECONDS as MAX_RETRY_AFTER_SECONDS,  # noqa: PLC0414 - compatibility export
)
from lib.python.core.cli_api import (
    READ_ATTEMPTS as READ_ATTEMPTS,  # noqa: PLC0414 - compatibility export
)
from lib.python.core.cli_api import APIError as GitLabAPIError
from lib.python.core.cli_api import CLIAPIClient, _endpoint

CREATE_LOCK_WAIT_SECONDS = 30
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
_NEXT_LINK = re.compile(r'<([^<>]+)>\s*;\s*rel\s*=\s*"?next"?', re.IGNORECASE)


GitLabApiError = GitLabAPIError


def uncertain_write(error: GitLabAPIError) -> bool:
    """Identify transport failures with an unknown write outcome.

    :param error: Classified failure from the GitLab transport.
    :returns: Whether the request may have reached GitLab.
    """
    return error.reason in _UNCERTAIN_WRITE


def unverified_write(
    target_id: int, resource: dict[str, Any], reason: str
) -> dict[str, Any]:
    """Preserve an unverified write in a partial action record.

    :param target_id: Numeric ID of the selected GitLab target.
    :param resource: Known fields from the provider write response.
    :param reason: Reason independent read-back was unavailable.
    :returns: Partial action record with an explicit uncertain outcome.
    """
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


def _lock_root() -> Path:
    return Path("/tmp") / f"ci-skills-gitlab-locks-{os.getuid()}"


class CreateGuard:
    """A same-host create lock with a crash-persistent uncertain marker."""

    def __init__(self, descriptor: int) -> None:
        """Load the lock's persistent uncertain-create marker.

        :param descriptor: Open descriptor for the selected create lock.
        :raises GitLabAPIError: If the marker contains an unknown state.
        """
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
        """Clear an uncertain create after an independent empty read.

        :raises GitLabAPIError: If an earlier create is absent and needs a new plan.
        """
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
    :yields: Guard that records an uncertain POST until reconciliation.
    :ytype: CreateGuard
    :raises GitLabAPIError: If the lock or recovery marker is unsafe.
    """  # noqa: DOC502 - lock failures propagate from the context manager
    with _locked_descriptor([origin, target_kind, target_id, kind, title]) as fd:
        yield CreateGuard(fd)


@contextmanager
def runner_update_guard(origin: str, runner_id: int) -> Iterator[None]:
    """Serialize local updates to one runner across selected projects.

    :param origin: Bound GitLab origin.
    :param runner_id: Runner ID shared by every project selection.
    :yields: Control after the resource lock is acquired.
    :ytype: None
    :raises GitLabAPIError: If the local lock cannot be acquired safely.
    """  # noqa: DOC502 - lock failures propagate from the context manager
    with _locked_descriptor([origin, "runner", runner_id]):
        yield


class GlabAPIClient(CLIAPIClient):
    """Bind the shared CLI transport to GitLab's native API command."""

    def argv(self, host: str, method: str, endpoint: str) -> list[str]:
        """Build GitLab's native JSON API invocation.

        :param host: Exact selected GitLab hostname.
        :param method: HTTP request method.
        :param endpoint: Validated relative API endpoint.
        :returns: Native glab arguments with included response headers.
        """
        return [
            "glab",
            "api",
            endpoint,
            "--hostname",
            host,
            "--method",
            method,
            "--output",
            "json",
            "--include",
        ]


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
