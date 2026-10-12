"""Shared result limits and failures for the native read-only commands.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from collections.abc import Mapping
from enum import IntEnum
from types import MappingProxyType
from typing import Final


class ReadExit(IntEnum):
    """Exit classes retained from the installed read-only command contract.

    .. attribute :: SUCCESS

        The selected read or plan completed successfully.

    .. attribute :: USAGE

        Caller arguments do not select a valid operation.

    .. attribute :: DATA

        Provider data, manifest content, or source identity is invalid.

    .. attribute :: MISSING

        A required input file or source directory is absent.

    .. attribute :: BLOCKED

        The provider, selected credentials, or required tool is unavailable.
    """

    SUCCESS = 0
    USAGE = 64
    DATA = 65
    MISSING = 66
    BLOCKED = 69


MAX_READ_BYTES: Final = 1024 * 1024
MAX_READ_LINES: Final = 200
MAX_READ_RETRY_AFTER: Final = 5

READ_FAILURE_MESSAGES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "authentication": "AUTHORIZATION_FAILED",
        "authorization": "AUTHORIZATION_FAILED",
        "provider_404": "RESOURCE_NOT_FOUND",
        "transport": "CONNECTIVITY_FAILED",
        "timeout": "PROVIDER_UNAVAILABLE",
        "provider_408": "PROVIDER_UNAVAILABLE",
        "rate_limited": "PROVIDER_UNAVAILABLE",
        "provider_5xx": "PROVIDER_UNAVAILABLE",
        "command_failed": "PROVIDER_FAILED",
        "retry_after_exceeds_budget": (
            "provider Retry-After exceeds the "
            f"{MAX_READ_RETRY_AFTER}-second retry budget"
        ),
    }
)


def read_failure_message(reason: str) -> str:
    """Render established read-command diagnostics from classified failures.

    :param reason: Credential-free classification carried by an APIError;
        never native provider stderr or response content.
    :returns: Compatible public diagnostic for known API failures, otherwise the
        unchanged API failure classification.
    """
    return READ_FAILURE_MESSAGES.get(reason, reason)


class ReadToolError(ValueError):
    """An input or result failure with a stable, credential-free diagnostic."""

    def __init__(self, reason: str, code: ReadExit = ReadExit.DATA):
        """Retain the failure class without exposing provider response content.

        :param reason: Nonsecret diagnostic describing the failed operation.
        :param code: Existing public command exit class.
        """
        self.code = code
        super().__init__(reason)
