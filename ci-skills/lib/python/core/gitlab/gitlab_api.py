"""Exact-host GitLab API transport used by access checks and operations.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

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
from collections.abc import Iterator, MutableSequence, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Union
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
from lib.python.core.cli_api import (
    APISession,
    CLIAPIClient,
    NativeResponse,
    RequestDeadline,
    validate_endpoint,
)
from lib.python.core.json_types import JSONDocument, JSONObject

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


def unverified_write(target_id: int, resource: JSONObject, reason: str) -> JSONObject:
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
    """Locate this user's local GitLab resource-lock directory.

    :returns: ``/tmp`` directory path qualified by the current numeric user ID.
    """
    return Path("/tmp") / f"ci-skills-gitlab-locks-{os.getuid()}"


class CreateGuard:
    """A same-host create lock with a crash-persistent uncertain marker."""

    def __init__(self, descriptor: int):
        """Load the lock's persistent uncertain-create marker.

        :param descriptor: Open descriptor for the selected create lock.
        :raises GitLabAPIError: If the marker contains an unknown state.
        :raises OSError: Seeking or reading the locked marker descriptor fails.
        """  # noqa: DOC503 - descriptor seek and read propagate OSError
        self._descriptor = descriptor
        os.lseek(descriptor, 0, os.SEEK_SET)
        state = os.read(descriptor, 16)
        if state not in (b"", b"pending\n"):
            raise GitLabAPIError("create_lock_invalid_state")
        self.pending = state == b"pending\n"

    def require_ready(self) -> None:
        """Refuse another create while a prior outcome remains uncertain.

        :raises GitLabAPIError: Persistent pending marker requires read-back
            reconciliation before a retry.
        """
        if self.pending:
            raise GitLabAPIError("create_outcome_uncertain_reconcile_before_retry")

    def _set(self, value: bytes) -> None:
        """Persist one create marker on the locked descriptor.

        :param value: Marker bytes; empty bytes clear the marker.
        :raises OSError: Writing, truncating, or syncing the marker fails.
        """  # noqa: DOC502 - marker write and fsync propagate OSError
        os.lseek(self._descriptor, 0, os.SEEK_SET)
        os.write(self._descriptor, value)
        os.ftruncate(self._descriptor, len(value))
        os.fsync(self._descriptor)
        self.pending = bool(value)

    def mark_pending(self) -> None:
        """Persist an uncertain-create marker before dispatching a POST.

        :raises GitLabAPIError: A previous create remains unreconciled.
        :raises OSError: Persisting the marker fails.
        """  # noqa: DOC502 - readiness validation and marker persistence propagate failures
        self.require_ready()
        self._set(b"pending\n")

    def reconcile_absent(self) -> None:
        """Clear an uncertain create after an independent empty read.

        :raises GitLabAPIError: If an earlier create is absent and needs a new plan.
        :raises OSError: Clearing the independently reconciled marker fails.
        """  # noqa: DOC503 - marker cleanup propagates OSError
        if self.pending:
            self.clear()
            raise GitLabAPIError("create_outcome_uncertain_absent_after_readback_retry")

    def clear(self) -> None:
        """Remove an existing pending marker after verified reconciliation.

        :raises OSError: Clearing the persistent marker fails.
        """  # noqa: DOC502 - persistent marker cleanup propagates OSError
        if self.pending:
            self._set(b"")


@contextmanager
def _locked_descriptor(identity: Sequence[Union[str, int]]) -> Iterator[int]:
    """Hold an exclusive local lock for one provider resource identity.

    :param identity: Provider and resource identifiers, without credentials.
    :yields: Open locked file descriptor until the caller's scope exits.
    :ytype: int
    :raises GitLabAPIError: Lock directory/file metadata is unsafe or acquisition
        exceeds its deadline.
    :raises OSError: Lock file creation, locking, or cleanup fails.
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
    :raises OSError: Acquiring, reading, or releasing the resource lock fails.
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
    :raises OSError: Accessing or releasing the resource lock fails.
    """  # noqa: DOC502 - lock failures propagate from the context manager
    with _locked_descriptor([origin, "runner", runner_id]):
        yield


class GlabAPIClient(CLIAPIClient):
    """Bind the shared CLI transport to GitLab's native API command."""

    def _identity_json(self, session: APISession) -> JSONDocument:
        """Read only the authenticated user before runtime state is established.

        :param session: Exact host and credential selected by authentication.
        :returns: Native user object used by the provider's identity initializer.
        :raises GitLabAPIError: The identity read or response decoding fails.
        :raises OSError: The native command cannot start or complete its I/O.
        """  # noqa: DOC502 - The shared transport propagates request failures.
        response = super()._exchange(session, "GET", "user")
        return self._decode_response(response.text, "GET")

    def _exchange(
        self,
        session: APISession,
        method: str,
        endpoint: str,
        body: Optional[JSONObject] = None,
        *,
        raw: bool = False,
        paginate: bool = False,
        deadline: Optional[RequestDeadline] = None,
    ) -> NativeResponse:
        """Require retained access before every public native API operation.

        :param session: Selected host, credential, and shared invocation state.
        :param method: HTTP operation supplied by the caller.
        :param endpoint: Provider-relative request path.
        :param body: Optional JSON request fields.
        :param raw: Whether the caller requests response text without headers.
        :param paginate: Whether the native CLI collects all pages.
        :param deadline: Optional absolute deadline shared by related reads.
        :returns: The existing bounded native response.
        :raises GitLabAPIError: Runtime access, transport, or response handling fails.
        :raises OSError: Native process or private request-file I/O fails.
        """  # noqa: DOC502,DOC503 - The shared transport propagates native failures.
        from lib.python.access.gitlab import require_gitlab_access
        from lib.python.core.abstract.skill_state import SkillState

        state = getattr(session, "invocation", None)
        if not isinstance(state, SkillState):
            raise GitLabAPIError("gitlab_identity_preflight_missing")
        try:
            require_gitlab_access(state, session)
        except ValueError as exc:
            raise GitLabAPIError(str(exc)) from exc
        return super()._exchange(
            session,
            method,
            endpoint,
            body,
            raw=raw,
            paginate=paginate,
            deadline=deadline,
        )

    def argv(self, host: str, method: str, endpoint: str) -> MutableSequence[str]:
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


def next_keyset_endpoint(
    session: APISession, current: str, headers: str
) -> Optional[str]:
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
    base = urlsplit(validate_endpoint(current))
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
    return validate_endpoint(f"{relative}?{parsed.query}")
