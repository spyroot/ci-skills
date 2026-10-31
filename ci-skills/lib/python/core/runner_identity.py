"""Resolve names from the consumer repository without guessing its directory name.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from lib.python.core.runtime import run_command

GIT_COMMAND: Final = "git"
GIT_REMOTE_ARGUMENTS: Final = ("config", "--get", "remote.origin.url")
GIT_TIMEOUT_SECONDS: Final = 10
GIT_SUFFIX: Final = ".git"
MAX_REFERENCE_LENGTH: Final = 4096
IDENTITY_REQUIRED: Final = "repository_identity_required: provide "
INVALID_REFERENCE: Final = "repository_reference_invalid"


class RepositorySource(StrEnum):
    """Sources of repository identity exposed in runner reports."""

    PROVIDER = "provider"
    GIT = "git"
    UNKNOWN = "unknown"


class NameSource(StrEnum):
    """Supported sources of a resolved caller-facing name."""

    EXPLICIT = "explicit"
    REPOSITORY = "repository"
    PROVIDER = "provider"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    """Retain a nonsecret repository path and its resolution source.

    :param reference: Native repository path, absent when no source resolved.
    :param source: Provider, invoking Git repository, or unknown.
    """

    reference: str | None
    source: RepositorySource

    @property
    def name(self) -> str | None:
        """Return the leaf repository name, or absence without inventing one.

        :returns: Repository path's final component when known.
        """
        return self.reference.rsplit("/", 1)[-1] if self.reference else None


@dataclass(frozen=True, slots=True)
class ResolvedName:
    """Retain one resolved resource name and its provenance.

    :param value: Exact selected name.
    :param source: Explicit input or known repository identity.
    """

    value: str
    source: NameSource


def _repository_path(reference: str) -> str | None:
    """Strip transport syntax without retaining remote credentials.

    :param reference: Provider path, HTTPS URL, or SCP-style SSH remote.
    :returns: Native path, or absence for a numeric repository selector.
    :raises ValueError: A present reference has no valid repository path.
    """
    if not reference or len(reference) > MAX_REFERENCE_LENGTH:
        raise ValueError(INVALID_REFERENCE)
    selected = reference.strip()
    if selected.isdecimal():
        return None
    if "://" in selected:
        selected = urlsplit(selected).path
    elif "@" in selected and ":" in selected:
        selected = selected.split(":", 1)[1]
    selected = selected.strip("/").removesuffix(GIT_SUFFIX)
    parts = selected.split("/")
    if len(parts) < 2 or any(not item or item in (".", "..") for item in parts):
        raise ValueError(INVALID_REFERENCE)
    return selected


def resolve_repository(
    reference: str | None, directory: Path | None = None
) -> RepositoryIdentity:
    """Resolve the selected provider path before the invoking Git repository.

    An explicit numeric selector remains unknown until provider read-back; it
    never falls through to an unrelated invoking repository. Without a selected
    provider path, only ``origin`` in the invocation directory is consulted.

    :param reference: Explicit/provider repository path or URL, when selected.
    :param directory: Invocation directory; defaults to the caller's current directory.
    :returns: Nonsecret repository identity, or explicit unknown provenance.
    :raises ValueError: A selected repository reference is malformed.
    :raises OSError: The invocation directory cannot be resolved.
    """
    if reference:
        path = _repository_path(reference)
        return RepositoryIdentity(
            path, RepositorySource.PROVIDER if path else RepositorySource.UNKNOWN
        )
    result = run_command(
        (GIT_COMMAND, "-C", str(directory or Path.cwd()), *GIT_REMOTE_ARGUMENTS),
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if result.returncode:
        return RepositoryIdentity(None, RepositorySource.UNKNOWN)
    path = _repository_path(result.stdout.strip())
    return RepositoryIdentity(
        path, RepositorySource.GIT if path else RepositorySource.UNKNOWN
    )


def resolve_name(
    explicit: str | None, repository: RepositoryIdentity, *, argument: str
) -> ResolvedName:
    """Select an explicit name or the known repository name.

    :param explicit: Caller-supplied name override.
    :param repository: Repository identity already resolved by the boundary.
    :param argument: Existing CLI argument to supply when identity is unknown.
    :returns: Exact name and explicit/repository provenance.
    :raises ValueError: Explicit input is blank, or neither input resolves a name.
    """
    if explicit is not None:
        if not explicit.strip():
            raise ValueError(IDENTITY_REQUIRED + argument)
        return ResolvedName(explicit.strip(), NameSource.EXPLICIT)
    if repository.name:
        return ResolvedName(repository.name, NameSource.REPOSITORY)
    raise ValueError(IDENTITY_REQUIRED + argument)
