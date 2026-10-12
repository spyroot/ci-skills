"""Shared GitLab job fields and status definitions for job and pipeline reads.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Optional

from lib.python.core.json_types import JSONObject, JSONValue, json_object

TERMINAL_JOB_STATUSES: Final = frozenset({"success", "failed", "canceled", "skipped"})
FILTERABLE_JOB_STATUSES: Final = frozenset(
    {"failed", "success", "running", "pending", "canceled", "stuck"}
)


def job_text(item: JSONObject, field: str) -> str:
    """Read a required job string while preserving its provider spelling.

    :param item: Decoded job object returned by the GitLab transport.
    :param field: Required string field, used in the stable invalid-field reason.
    :returns: Original nonblank provider text, including meaningful whitespace.
    :raises ValueError: The selected field is absent, nontext, or blank.
    """
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"job_{field}_invalid")
    return value


@dataclass(frozen=True)
class GitLabJobFields:
    """Validated fields shared by job-list and pipeline-job responses.

    :param id: Positive GitLab job ID.
    :param name: Nonblank provider job name.
    :param stage: Nonblank pipeline stage name.
    :param status: Nonblank provider job status.
    :param failure_reason: Optional provider failure explanation.

    .. attribute :: id
        :type: int

        Positive GitLab job ID.

    .. attribute :: name
        :type: str

        Nonblank provider job name.

    .. attribute :: stage
        :type: str

        Nonblank pipeline stage name.

    .. attribute :: status
        :type: str

        Nonblank provider job status.

    .. attribute :: failure_reason
        :type: Optional[str]

        Optional provider failure explanation.
    """

    id: int
    name: str
    stage: str
    status: str
    failure_reason: Optional[str]

    @classmethod
    def from_api(cls, item: JSONValue) -> GitLabJobFields:
        """Normalize the shared fields of one decoded GitLab job value.

        :param item: JSON value from one GitLab job API response.
        :returns: Validated fields common to both job read routes.
        :raises ValueError: The value is not an object or a shared field is malformed.
        """
        try:
            item = json_object(item)
        except TypeError as exc:
            raise ValueError("job_response_invalid") from exc
        identifier = item.get("id")
        if type(identifier) is not int or identifier <= 0:
            raise ValueError("job_response_invalid")
        name, stage, status = (
            job_text(item, field) for field in ("name", "stage", "status")
        )
        failure_reason = item.get("failure_reason")
        if failure_reason is not None and not isinstance(failure_reason, str):
            raise ValueError("job_failure_reason_invalid")
        return cls(identifier, name, stage, status, failure_reason)

    def public(self) -> JSONObject:
        """Expose the common job fields without the original provider response.

        :returns: JSON object containing identity, stage, status, and failure reason.
        """
        return {
            "id": self.id,
            "name": self.name,
            "stage": self.stage,
            "status": self.status,
            "failure_reason": self.failure_reason,
        }
