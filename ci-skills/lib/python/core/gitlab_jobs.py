"""Bounded GitLab job listing for one verified project."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from math import isfinite
from typing import Any, Final, TypedDict
from urllib.parse import urlencode, urlsplit

from .collect import _timestamp, parse_window
from .gitlab_api import GitLabAPIError, GlabAPIClient, next_keyset_endpoint
from .gitlab_job_fields import FILTERABLE_JOB_STATUSES, GitLabJobFields
from .gitlab_session import BoundGitLabSession

PAGE_SIZE: Final = 100
SCAN_PAGES: Final = 20
MAX_RESULTS: Final = 500
MAX_RECORD_ERRORS: Final = 32


class RunnerSummary(TypedDict):
    """Public runner identity attached to a GitLab job."""

    id: int
    description: str | None


class JobSummary(TypedDict):
    """Fields emitted by the bounded job-list result."""

    id: int
    name: str
    stage: str
    status: str
    stuck: bool
    failure_reason: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    duration: float | None
    runner: RunnerSummary | None
    pipeline_id: int
    ref: str
    web_url: str


@dataclass(frozen=True)
class JobListResult:
    """Validated records and completeness from one bounded API scan."""

    records: list[JobSummary]
    errors: list[dict[str, str]]
    truncated: bool
    first_page_failed: bool = False


@dataclass(frozen=True)
class JobQuery:
    """Validated filters for one bounded project job read.

    :param statuses: Requested GitLab or derived job states.
    :param pipeline_id: Exact pipeline ID, when selected.
    :param ref: Exact branch or tag, when selected.
    :param search: Case-insensitive substring across the declared fields.
    :param name_glob: Shell-style job-name pattern, when selected.
    :param limit: Maximum returned matching records.
    :param start: Inclusive UTC lower bound, when selected.
    :param end: Inclusive UTC upper bound, when selected.
    :param stuck_after: Minimum pending or created age in seconds.
    """

    statuses: tuple[str, ...]
    pipeline_id: int | None
    ref: str | None
    search: str | None
    name_glob: str | None
    limit: int
    start: datetime | None
    end: datetime | None
    stuck_after: int

    @classmethod
    def from_args(cls, args: Any) -> JobQuery:
        """Validate CLI values before any GitLab access.

        :param args: Parsed job-list arguments.
        :returns: Immutable query with UTC time bounds.
        :raises ValueError: If a limit, status, time range, or ID is invalid.
        """
        statuses = tuple(dict.fromkeys(args.status or ()))
        if any(status not in FILTERABLE_JOB_STATUSES for status in statuses):
            raise ValueError("job_status_invalid")
        if not 1 <= args.limit <= MAX_RESULTS:
            raise ValueError("job_limit_out_of_range")
        if args.pipeline_id is not None and args.pipeline_id <= 0:
            raise ValueError("pipeline_id_must_be_positive")
        if any(
            value == ""
            for value in (
                args.project,
                args.ref,
                args.search,
                args.name_glob,
                args.last,
                args.from_time,
                args.to_time,
            )
        ):
            raise ValueError("job_filter_must_be_nonempty")
        if args.stuck_after <= 0:
            raise ValueError("stuck_after_must_be_positive")
        if args.last is not None and (args.from_time or args.to_time):
            raise ValueError("last_conflicts_with_from_or_to")
        end = _timestamp(args.to_time) if args.to_time else None
        start = _timestamp(args.from_time) if args.from_time else None
        if args.last is not None:
            end = datetime.now(UTC)
            start = end - parse_window(args.last)
        if start is not None and end is not None and start > end:
            raise ValueError("from_after_to")
        return cls(
            statuses=statuses,
            pipeline_id=args.pipeline_id,
            ref=args.ref,
            search=args.search.casefold() if args.search else None,
            name_glob=args.name_glob,
            limit=args.limit,
            start=start,
            end=end,
            stuck_after=args.stuck_after,
        )

    def public(self) -> dict[str, Any]:
        """Return only filters safe for the machine report.

        :returns: The selected list filters and bounded result limit.
        """
        return {
            "status": list(self.statuses),
            "pipeline_id": self.pipeline_id,
            "ref": self.ref,
            "search": self.search,
            "name_glob": self.name_glob,
            "limit": self.limit,
            "from": self.start.isoformat() if self.start else None,
            "to": self.end.isoformat() if self.end else None,
            "stuck_after": self.stuck_after,
        }

    def matches(self, job: JobRecord, now: datetime) -> bool:
        """Apply filters not supported by GitLab's list endpoint.

        :param job: Validated job record.
        :param now: UTC observation time for the derived stuck state.
        :returns: Whether the record matches every selected filter.
        """
        stuck = job.is_stuck(now, self.stuck_after)
        if self.statuses and not (
            job.status in self.statuses or ("stuck" in self.statuses and stuck)
        ):
            return False
        stamp = job.finished_at or job.created_at
        if self.start is not None and stamp < self.start:
            return False
        if self.end is not None and stamp > self.end:
            return False
        if self.ref is not None and job.ref != self.ref:
            return False
        if self.pipeline_id is not None and job.pipeline_id != self.pipeline_id:
            return False
        if self.name_glob is not None and not fnmatchcase(job.name, self.name_glob):
            return False
        return self.search is None or any(
            self.search in value.casefold()
            for value in (job.name, job.stage, job.ref, job.failure_reason or "")
        )


@dataclass(frozen=True)
class JobRecord:
    """Validated public fields from one GitLab job response."""

    id: int
    name: str
    stage: str
    status: str
    failure_reason: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration: float | None
    runner: RunnerSummary | None
    pipeline_id: int
    ref: str
    web_url: str

    @classmethod
    def from_api(cls, item: Any, host: str) -> JobRecord:
        """Narrow an untrusted API object to the advertised job fields.

        :param item: One GitLab job API value.
        :param host: Exact bound GitLab host.
        :returns: Validated public job record.
        :raises ValueError: If required data is absent, malformed, or off host.
        """
        if not isinstance(item, dict) or not isinstance(item.get("pipeline"), dict):
            # Preserve the provider error code consumed by list reports.
            raise ValueError("job_response_invalid")  # noqa: TRY004
        pipeline = item.get("pipeline")
        runner = item.get("runner")
        url = item.get("web_url")
        if (
            type(pipeline.get("id")) is not int
            or pipeline["id"] <= 0
            or not isinstance(url, str)
            or urlsplit(url).scheme != "https"
            or urlsplit(url).netloc.lower() != host
        ):
            raise ValueError("job_response_invalid")
        fields = GitLabJobFields.from_api(item)
        for field in ("ref", "created_at"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f"job_{field}_invalid")
        duration = item.get("duration")
        if duration is not None and (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or duration < 0
            or not isfinite(duration)
        ):
            raise ValueError("job_duration_invalid")
        if runner is not None and (
            not isinstance(runner, dict)
            or type(runner.get("id")) is not int
            or runner["id"] <= 0
        ):
            raise ValueError("job_runner_invalid")
        selected_runner: RunnerSummary | None = None
        if runner is not None:
            description = runner.get("description")
            if description is not None and not isinstance(description, str):
                raise ValueError("job_runner_invalid")
            selected_runner = {"id": runner["id"], "description": description}
        try:
            created = _timestamp(item["created_at"])
            started = _timestamp(item["started_at"]) if item.get("started_at") else None
            finished = (
                _timestamp(item["finished_at"]) if item.get("finished_at") else None
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("job_timestamp_invalid") from exc
        return cls(
            id=fields.id,
            name=fields.name,
            stage=fields.stage,
            status=fields.status,
            failure_reason=fields.failure_reason,
            created_at=created,
            started_at=started,
            finished_at=finished,
            duration=float(duration) if duration is not None else None,
            runner=selected_runner,
            pipeline_id=pipeline["id"],
            ref=item["ref"],
            web_url=url,
        )

    def is_stuck(self, now: datetime, after: int) -> bool:
        """Classify a pending or created job older than the selected bound.

        :param now: UTC observation instant.
        :param after: Minimum pending age in seconds.
        :returns: Whether this job meets the derived stuck state.
        """
        return (
            self.status in {"pending", "created"}
            and (now - self.created_at).total_seconds() >= after
        )

    def public(self, *, stuck: bool) -> JobSummary:
        """Render the declared job-list record without raw provider data.

        :param stuck: Derived pending-age state at collection time.
        :returns: A machine-readable job summary.
        """
        return {
            "id": self.id,
            "name": self.name,
            "stage": self.stage,
            "status": self.status,
            "stuck": stuck,
            "failure_reason": self.failure_reason,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration": self.duration,
            "runner": self.runner,
            "pipeline_id": self.pipeline_id,
            "ref": self.ref,
            "web_url": self.web_url,
        }


def list_jobs(
    session: BoundGitLabSession,
    project_id: int,
    query: JobQuery,
    *,
    api_client: GlabAPIClient | None = None,
) -> JobListResult:
    """Read newest jobs through GitLab's stable project-job cursor.

    :param session: Credential and host already bound to this project.
    :param project_id: Project ID read back from the access check.
    :param query: Validated list filters and result limit.
    :param api_client: Optional GitLab transport supplied by the caller.
    :returns: Public records, classified errors, and truncation state.
    :raises ValueError: If the project ID is invalid.
    """
    if type(project_id) is not int or project_id <= 0:
        raise ValueError("project_id_invalid")
    client = api_client or GlabAPIClient()
    selected_scopes = set(query.statuses) - {"stuck"}
    if "stuck" in query.statuses:
        selected_scopes.update(("pending", "created"))
    parameters = [
        ("pagination", "keyset"),
        ("per_page", str(PAGE_SIZE)),
        ("order_by", "id"),
        ("sort", "desc"),
        *(("scope[]", status) for status in sorted(selected_scopes)),
    ]
    endpoint = f"projects/{project_id}/jobs?{urlencode(parameters)}"
    records: list[JobSummary] = []
    errors: list[dict[str, str]] = []
    now = datetime.now(UTC)
    seen_ids: set[int] = set()
    seen_endpoints: set[str] = set()
    previous_id: int | None = None
    for page in range(SCAN_PAGES):
        if endpoint in seen_endpoints:
            errors.append({"source": "jobs", "reason": "pagination_cursor_repeated"})
            return JobListResult(records, errors, True)
        seen_endpoints.add(endpoint)
        try:
            items, headers = client.get_json_with_headers(session, endpoint)
        except GitLabAPIError as exc:
            errors.append({"source": "jobs", "reason": exc.reason})
            return JobListResult(records, errors, True, first_page_failed=page == 0)
        if not isinstance(items, list) or len(items) > PAGE_SIZE:
            errors.append({"source": "jobs", "reason": "job_page_invalid"})
            return JobListResult(records, errors, True, first_page_failed=page == 0)
        for item in items:
            try:
                job = JobRecord.from_api(item, session.host)
            except ValueError as exc:
                errors.append({"source": "jobs", "reason": str(exc)})
                if len(errors) >= MAX_RECORD_ERRORS:
                    errors.append(
                        {"source": "jobs", "reason": "invalid_record_limit_reached"}
                    )
                    return JobListResult(records, errors, True)
                continue
            if job.id in seen_ids or (
                previous_id is not None and job.id >= previous_id
            ):
                errors.append({"source": "jobs", "reason": "pagination_order_invalid"})
                return JobListResult(records, errors, True)
            seen_ids.add(job.id)
            previous_id = job.id
            if query.matches(job, now):
                records.append(job.public(stuck=job.is_stuck(now, query.stuck_after)))
                if len(records) > query.limit:
                    return JobListResult(records[: query.limit], errors, True)
        try:
            next_endpoint = next_keyset_endpoint(session, endpoint, headers)
        except GitLabAPIError as exc:
            errors.append({"source": "jobs", "reason": exc.reason})
            return JobListResult(records, errors, True)
        if next_endpoint is None:
            return JobListResult(records, errors, False)
        endpoint = next_endpoint
    errors.append({"source": "jobs", "reason": "scan_limit_reached"})
    return JobListResult(records, errors, True)
