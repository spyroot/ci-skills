"""Bounded GitLab job listing for one verified project.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Mapping, MutableSequence, MutableSet, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from math import isfinite
from typing import Final, Optional, TypedDict
from urllib.parse import urlencode, urlsplit

from lib.python.core.collect import _timestamp, parse_window
from lib.python.core.gitlab.gitlab_api import (
    GitLabAPIError,
    GlabAPIClient,
    next_keyset_endpoint,
)
from lib.python.core.gitlab.gitlab_job_fields import (
    FILTERABLE_JOB_STATUSES,
    GitLabJobFields,
    job_text,
)
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
from lib.python.core.json_types import JSONObject, JSONValue, json_object

PAGE_SIZE: Final = 100
SCAN_PAGES: Final = 20
MAX_RESULTS: Final = 500
MAX_RECORD_ERRORS: Final = 32


class RunnerSummary(TypedDict):
    """Bounded public runner identity attached to a GitLab job.


    .. attribute :: id
        :type: int

        Positive assigned runner ID.

    .. attribute :: description
        :type: Optional[str]

        Runner description when the provider supplies one.
    """

    id: int
    description: Optional[str]


class JobSummary(TypedDict):
    """Public fields returned for one job in a bounded project scan.


    .. attribute :: id
        :type: int

        Positive GitLab job ID.

    .. attribute :: name
        :type: str

        Nonblank job name.

    .. attribute :: stage
        :type: str

        Nonblank pipeline stage.

    .. attribute :: status
        :type: str

        Provider job status.

    .. attribute :: stuck
        :type: bool

        Derived pending-job age classification.

    .. attribute :: failure_reason
        :type: Optional[str]

        Provider failure reason when available.

    .. attribute :: created_at
        :type: str

        Job creation timestamp.

    .. attribute :: started_at
        :type: Optional[str]

        Start timestamp when available.

    .. attribute :: finished_at
        :type: Optional[str]

        Completion timestamp when available.

    .. attribute :: duration
        :type: Optional[float]

        Finite job duration in seconds when available.

    .. attribute :: runner
        :type: Optional[RunnerSummary]

        Bounded assigned runner identity when available.

    .. attribute :: pipeline_id
        :type: int

        Positive parent pipeline ID.

    .. attribute :: ref
        :type: str

        Branch or tag associated with the job.

    .. attribute :: web_url
        :type: str

        HTTPS job URL on the bound GitLab host.
    """

    id: int
    name: str
    stage: str
    status: str
    stuck: bool
    failure_reason: Optional[str]
    created_at: str
    started_at: Optional[str]
    finished_at: Optional[str]
    duration: Optional[float]
    runner: Optional[RunnerSummary]
    pipeline_id: int
    ref: str
    web_url: str


@dataclass(frozen=True)
class JobListResult:
    """Bounded project job scan with completeness and record errors.

    :param records: Validated public jobs retained by the selected filters.
    :param errors: Bounded source and reason entries for collection failures.
    :param truncated: Whether matching results exceeded the return limit.
    :param first_page_failed: Whether the initial provider page could not be read.

    .. attribute :: records
        :type: Sequence[JobSummary]

        Validated public jobs retained by the selected filters.

    .. attribute :: errors
        :type: Sequence[Mapping[str, str]]

        Bounded source and reason entries for collection failures.

    .. attribute :: truncated
        :type: bool

        Whether matching results exceeded the return limit.

    .. attribute :: first_page_failed
        :type: bool

        Whether the initial provider page could not be read.
    """

    records: Sequence[JobSummary]
    errors: Sequence[Mapping[str, str]]
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

    .. attribute :: statuses
        :type: tuple[str, ...]

        Requested GitLab or derived job states.

    .. attribute :: pipeline_id
        :type: Optional[int]

        Exact pipeline ID, when selected.

    .. attribute :: ref
        :type: Optional[str]

        Exact branch or tag, when selected.

    .. attribute :: search
        :type: Optional[str]

        Case-insensitive substring across the declared fields.

    .. attribute :: name_glob
        :type: Optional[str]

        Shell-style job-name pattern, when selected.

    .. attribute :: limit
        :type: int

        Maximum returned matching records.

    .. attribute :: start
        :type: Optional[datetime]

        Inclusive UTC lower bound, when selected.

    .. attribute :: end
        :type: Optional[datetime]

        Inclusive UTC upper bound, when selected.

    .. attribute :: stuck_after
        :type: int

        Minimum pending or created age in seconds.
    """

    statuses: tuple[str, ...]
    pipeline_id: Optional[int]
    ref: Optional[str]
    search: Optional[str]
    name_glob: Optional[str]
    limit: int
    start: Optional[datetime]
    end: Optional[datetime]
    stuck_after: int

    @classmethod
    def from_args(cls, args: Namespace) -> JobQuery:
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

    def public(self) -> JSONObject:
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
    """Validated public job and pipeline identity from a GitLab response.

    :param id: Positive GitLab job ID.
    :param name: Nonblank provider job name.
    :param stage: Nonblank pipeline stage.
    :param status: Nonblank provider job state.
    :param failure_reason: Optional provider failure explanation.
    :param created_at: Parsed job creation time.
    :param started_at: Parsed start time when the job ran.
    :param finished_at: Parsed completion time when terminal.
    :param duration: Optional finite job duration in seconds.
    :param runner: Optional bounded runner identity attached to the job.
    :param pipeline_id: Positive parent pipeline ID.
    :param ref: Branch or tag reported for the job.
    :param web_url: HTTPS job URL on the bound GitLab host.

    .. attribute :: id
        :type: int

        Positive GitLab job ID.

    .. attribute :: name
        :type: str

        Nonblank provider job name.

    .. attribute :: stage
        :type: str

        Nonblank pipeline stage.

    .. attribute :: status
        :type: str

        Nonblank provider job state.

    .. attribute :: failure_reason
        :type: Optional[str]

        Optional provider failure explanation.

    .. attribute :: created_at
        :type: datetime

        Parsed job creation time.

    .. attribute :: started_at
        :type: Optional[datetime]

        Parsed start time when the job ran.

    .. attribute :: finished_at
        :type: Optional[datetime]

        Parsed completion time when terminal.

    .. attribute :: duration
        :type: Optional[float]

        Optional finite job duration in seconds.

    .. attribute :: runner
        :type: Optional[RunnerSummary]

        Optional bounded runner identity attached to the job.

    .. attribute :: pipeline_id
        :type: int

        Positive parent pipeline ID.

    .. attribute :: ref
        :type: str

        Branch or tag reported for the job.

    .. attribute :: web_url
        :type: str

        HTTPS job URL on the bound GitLab host.
    """

    id: int
    name: str
    stage: str
    status: str
    failure_reason: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    duration: Optional[float]
    runner: Optional[RunnerSummary]
    pipeline_id: int
    ref: str
    web_url: str

    @classmethod
    def from_api(cls, item: JSONValue, host: str) -> JobRecord:
        """Narrow a decoded API value to the advertised job fields.

        :param item: One decoded GitLab job API value.
        :param host: Exact bound GitLab host.
        :returns: Validated public job record.
        :raises ValueError: Required data is absent, malformed, or points off host.
        """
        try:
            item = json_object(item)
        except TypeError as exc:
            raise ValueError("job_response_invalid") from exc
        pipeline_id, url = _job_identity(item, host)
        fields = GitLabJobFields.from_api(item)
        ref = job_text(item, "ref")
        created_text = job_text(item, "created_at")
        try:
            created = _timestamp(created_text)
            started = _optional_timestamp(item.get("started_at"))
            finished = _optional_timestamp(item.get("finished_at"))
        except ValueError as exc:
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
            duration=_duration(item.get("duration")),
            runner=_runner(item.get("runner")),
            pipeline_id=pipeline_id,
            ref=ref,
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

    def public(self, stuck: bool) -> JobSummary:
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


def _job_identity(item: JSONObject, host: str) -> tuple[int, str]:
    """Validate the parent pipeline ID and same-host HTTPS job link.

    :param item: Decoded GitLab job object.
    :param host: Exact authority selected by the bound session.
    :returns: Positive parent pipeline ID and verified job URL.
    :raises ValueError: If pipeline identity or URL fields are missing, have
        an invalid JSON shape, or do not identify a positive pipeline on the bound host.
    """
    pipeline, url = item.get("pipeline"), item.get("web_url")
    if not isinstance(pipeline, Mapping) or not isinstance(url, str):
        # JobScan retains malformed provider values as ValueError failures.
        raise ValueError("job_response_invalid")  # noqa: TRY004
    identifier = pipeline.get("id")
    if (
        type(identifier) is not int
        or identifier <= 0
        or urlsplit(url).scheme != "https"
        or urlsplit(url).netloc.lower() != host
    ):
        raise ValueError("job_response_invalid")
    return identifier, url


def _duration(value: JSONValue) -> Optional[float]:
    """Validate a nullable duration without accepting booleans or nonfinite values.

    :param value: Provider duration in seconds, or JSON null when unavailable.
    :returns: Finite nonnegative seconds as a float, or None.
    :raises ValueError: A present duration is nonnumeric, negative, or nonfinite.
    """
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
        or not isfinite(value)
    ):
        raise ValueError("job_duration_invalid")
    return float(value)


def _runner(value: JSONValue) -> Optional[RunnerSummary]:
    """Validate the small public runner identity attached to a job.

    :param value: Provider runner object, or JSON null before assignment.
    :returns: Positive runner ID and nullable description, or None when unassigned.
    :raises ValueError: A present runner has malformed identity or description.
    """
    if value is None:
        return None
    try:
        value = json_object(value)
    except TypeError as exc:
        raise ValueError("job_runner_invalid") from exc
    identifier, description = value.get("id"), value.get("description")
    if type(identifier) is not int or identifier <= 0:
        raise ValueError("job_runner_invalid")
    if description is not None and not isinstance(description, str):
        raise ValueError("job_runner_invalid")
    return {"id": identifier, "description": description}


def _optional_timestamp(value: JSONValue) -> Optional[datetime]:
    """Normalize a job timestamp that the provider may not have assigned yet.

    :param value: ISO timestamp, empty text, or JSON null before the event.
    :returns: UTC timestamp, or None when no event time is present.
    :raises ValueError: A present value is not text or is not timezone-aware ISO time.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        # JobRecord and JobScan classify invalid provider timestamps together.
        raise ValueError("job_timestamp_invalid")  # noqa: TRY004
    return _timestamp(value)


class JobScan:
    """Retain bounded scan evidence while following one project's keyset cursor.

    :ivar session: Bound host and credentials supplied by the caller.
    :ivar query: Validated filters and result limit supplied by the caller.
    :ivar client: Caller-selected transport for every cursor page.
    :ivar records: Validated matching records plus at most one truncation sentinel.
    :ivar errors: Bounded provider, record-validation, or pagination failure records.
    :ivar seen_ids: Validated job IDs already consumed by this scan.
    :ivar seen_endpoints: Cursor endpoints already requested by this scan.
    :ivar previous_id: Last valid job ID, absent until the first valid record.
    :ivar now: One UTC observation time shared by every derived stuck-state check.
    """

    def __init__(
        self, session: BoundGitLabSession, query: JobQuery, client: GlabAPIClient
    ):
        """Bind the provider and query and initialize independent scan state.

        :param session: Exact-host credential and selected-project session.
        :param query: Validated filters and maximum returned record count.
        :param client: Provider transport used for every page in this scan.
        """
        self.session, self.query, self.client = session, query, client
        self.records: MutableSequence[JobSummary] = []
        self.errors: MutableSequence[Mapping[str, str]] = []
        self.seen_ids: MutableSet[int] = set()
        self.seen_endpoints: MutableSet[str] = set()
        self.previous_id: Optional[int] = None
        self.now = datetime.now(UTC)

    def _result(
        self, truncated: bool, first_page_failed: bool = False
    ) -> JobListResult:
        """Snapshot retained evidence without exceeding the query's result limit.

        :param truncated: Whether completeness could not be established.
        :param first_page_failed: Whether the initial provider page was unreadable.
        :returns: Bounded public observations and accumulated failure reasons.
        """
        return JobListResult(
            self.records[: self.query.limit], self.errors, truncated, first_page_failed
        )

    def _consume(self, items: Sequence[JSONValue]) -> bool:
        """Validate one page's ordering and append matching public records.

        :param items: Bounded decoded job values from one cursor page.
        :returns: Whether scanning may continue within record and error bounds.
        """
        for item in items:
            try:
                job = JobRecord.from_api(item, self.session.host)
            except ValueError as exc:
                self.errors.append({"source": "jobs", "reason": str(exc)})
                if len(self.errors) >= MAX_RECORD_ERRORS:
                    self.errors.append(
                        {"source": "jobs", "reason": "invalid_record_limit_reached"}
                    )
                    return False
                continue
            if job.id in self.seen_ids or (
                self.previous_id is not None and job.id >= self.previous_id
            ):
                self.errors.append(
                    {"source": "jobs", "reason": "pagination_order_invalid"}
                )
                return False
            self.seen_ids.add(job.id)
            self.previous_id = job.id
            if self.query.matches(job, self.now):
                stuck = job.is_stuck(self.now, self.query.stuck_after)
                self.records.append(job.public(stuck))
                if len(self.records) > self.query.limit:
                    return False
        return True

    def run(self, endpoint: str) -> JobListResult:
        """Follow bounded cursor pages while preserving partial provider evidence.

        :param endpoint: Initial project-jobs endpoint with fixed query filters.
        :returns: Complete or truncated observations with classified collection errors.
        """
        for page in range(SCAN_PAGES):
            if endpoint in self.seen_endpoints:
                self.errors.append(
                    {"source": "jobs", "reason": "pagination_cursor_repeated"}
                )
                return self._result(True)
            self.seen_endpoints.add(endpoint)
            try:
                items, headers = self.client.get_json_with_headers(
                    self.session, endpoint
                )
            except GitLabAPIError as exc:
                self.errors.append({"source": "jobs", "reason": exc.reason})
                return self._result(True, first_page_failed=page == 0)
            if not isinstance(items, list) or len(items) > PAGE_SIZE:
                self.errors.append({"source": "jobs", "reason": "job_page_invalid"})
                return self._result(True, first_page_failed=page == 0)
            if not self._consume(items):
                return self._result(True)
            try:
                next_endpoint = next_keyset_endpoint(self.session, endpoint, headers)
            except GitLabAPIError as exc:
                self.errors.append({"source": "jobs", "reason": exc.reason})
                return self._result(True)
            if next_endpoint is None:
                return self._result(False)
            endpoint = next_endpoint
        self.errors.append({"source": "jobs", "reason": "scan_limit_reached"})
        return self._result(True)


def list_jobs(
    session: BoundGitLabSession,
    project_id: int,
    query: JobQuery,
    api_client: Optional[GlabAPIClient] = None,
) -> JobListResult:
    """Read newest jobs through GitLab's stable project-job cursor.

    :param session: Credential and host already bound to this project.
    :param project_id: Project ID read back from the access check.
    :param query: Validated list filters and result limit.
    :param api_client: Optional caller-owned transport; otherwise use the native client.
    :returns: Public records, classified errors, and truncation state.
    :raises ValueError: The project ID is not a positive built-in integer.
    """
    if type(project_id) is not int or project_id <= 0:
        raise ValueError("project_id_invalid")
    client = api_client if api_client is not None else GlabAPIClient()
    scopes = set(query.statuses) - {"stuck"}
    if "stuck" in query.statuses:
        scopes.update(("pending", "created"))
    parameters = [
        ("pagination", "keyset"),
        ("per_page", str(PAGE_SIZE)),
        ("order_by", "id"),
        ("sort", "desc"),
        *(("scope[]", status) for status in sorted(scopes)),
    ]
    endpoint = f"projects/{project_id}/jobs?{urlencode(parameters)}"
    return JobScan(session, query, client).run(endpoint)
