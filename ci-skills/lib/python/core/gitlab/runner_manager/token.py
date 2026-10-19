"""Bind one private GitLab runner token without exposing its value.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias, Union

from lib.python.access.sources import read_token_file
from lib.python.core.json_types import JSONObject


@dataclass(frozen=True)
class NoRunnerToken:
    """Represent an operation that consumes no runner registration token."""

    def document(self) -> JSONObject:
        """Describe the explicit absence of a token requirement.

        :returns: Nonsecret token-selection state for the plan fingerprint.
        """
        return {"required": False}


@dataclass(frozen=True)
class DeferredRunnerToken:
    """Retain a selected token path without reading it in offline mode.

    :param path: Explicit file whose private contents a live plan binds.

    .. attribute :: path
        :type: Path

        Selected token file, unread in offline mode.
    """

    path: Path

    def document(self) -> JSONObject:
        """Describe the deferred token input without credential bytes.

        :returns: Nonsecret token-selection state for the offline fingerprint.
        """
        return {"required": True, "source": f"file:{self.path}", "state": "deferred"}


@dataclass(frozen=True)
class BoundRunnerToken:
    """Retain a private token and its confirmation-safe identity.

    :param path: Explicit caller-selected token file.
    :param digest: SHA-256 identity used to bind plan and apply.
    :param value: Registration token kept out of representations and comparisons.

    .. attribute :: path
        :type: Path

        Caller-selected token file consumed by the shared reader.

    .. attribute :: digest
        :type: str

        SHA-256 identity binding plan and apply to the same token.

    .. attribute :: value
        :type: str

        Private registration token excluded from representations.
    """

    path: Path
    digest: str
    value: str = field(repr=False, compare=False)

    def document(self) -> JSONObject:
        """Describe the bound credential without exposing its value.

        :returns: Token source and one-way identity for the plan fingerprint.
        """
        return {
            "required": True,
            "source": f"file:{self.path}",
            "digest": self.digest,
            "state": "bound",
        }


RunnerTokenSelection: TypeAlias = Union[
    NoRunnerToken, DeferredRunnerToken, BoundRunnerToken
]


def token_path(value: str) -> Path:
    """Resolve one caller-selected token path without a fallback.

    :param value: Nonempty path supplied by the command caller.
    :returns: Absolute expanded path used by plan and apply.
    :raises ValueError: The selected path is empty.
    """
    if not value.strip():
        raise ValueError("runner_token_file_required")
    selected = Path(value).expanduser()
    return selected if selected.is_absolute() else (Path.cwd() / selected).resolve()


def read_runner_token(path: Path) -> BoundRunnerToken:
    """Read one shared-valid token file and bind its exact credential identity.

    :param path: Caller-selected registration-token file.
    :returns: Private token plus a confirmation-safe SHA-256 identity.
    :raises TargetError: The shared reader rejects the selected token file.
    """  # noqa: DOC502,DOC503 - Shared token validation raises TargetError.
    value = read_token_file(path)
    digest = "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
    return BoundRunnerToken(path, digest, value)
