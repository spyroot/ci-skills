"""Shared GitLab job fields and status definitions for job and pipeline reads.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

TERMINAL_JOB_STATUSES: Final = frozenset({"success", "failed", "canceled", "skipped"})
FILTERABLE_JOB_STATUSES: Final = frozenset(
    {"failed", "success", "running", "pending", "canceled", "stuck"}
)


@dataclass(frozen=True)
class GitLabJobFields:
    """Validated fields common to project job lists and pipeline jobs."""

    id: int
    name: str
    stage: str
    status: str
    failure_reason: str | None

    @classmethod
    def from_api(cls, item: Any) -> GitLabJobFields:
        """Normalize the shared fields of one untrusted GitLab job.

        :param item: One GitLab job API response object.
        :returns: Validated fields common to both job read routes.
        :raises ValueError: If an ID or shared field is missing or malformed.
        """
        if not isinstance(item, dict) or type(item.get("id")) is not int:
            raise ValueError("job_response_invalid")
        if item["id"] <= 0:
            raise ValueError("job_response_invalid")
        for field in ("name", "stage", "status"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f"job_{field}_invalid")
        failure_reason = item.get("failure_reason")
        if failure_reason is not None and not isinstance(failure_reason, str):
            raise ValueError("job_failure_reason_invalid")
        return cls(
            id=item["id"],
            name=item["name"],
            stage=item["stage"],
            status=item["status"],
            failure_reason=failure_reason,
        )
