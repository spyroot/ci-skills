"""Exercise bounded GitLab job reads without contacting a provider.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from tests.python.conftest import import_script_module

JOBS = import_script_module("core.gitlab_jobs")
SCRIPT = import_script_module("gitlab_job")


def _query(*options: str) -> Any:
    """Parse one declared job-list query through its public CLI.

    :param options: Extra list flags to validate.
    :returns: Validated job query.
    :raises ValueError: If the selected flags conflict or are invalid.
    """
    args = SCRIPT.build_parser().parse_args(
        ["list", "--project", "unit/repo", *options]
    )
    return JOBS.JobQuery.from_args(args)


def _job(identifier: int, **changes: object) -> dict[str, object]:
    """Build one provider response with optional field overrides.

    :param identifier: Positive job ID.
    :param changes: Provider fields to override for the case.
    :returns: A job response for the test API.
    """
    job: dict[str, object] = {
        "id": identifier,
        "name": "build-app",
        "stage": "test",
        "status": "failed",
        "failure_reason": "script_failure",
        "created_at": "2026-10-07T18:00:00Z",
        "started_at": "2026-10-07T18:01:00Z",
        "finished_at": "2026-10-07T18:02:00Z",
        "duration": 60.0,
        "runner": {"id": 9, "description": "test-runner"},
        "pipeline": {"id": 77},
        "ref": "main",
        "web_url": f"https://gitlab.example.test/unit/repo/-/jobs/{identifier}",
    }
    job.update(changes)
    return job


@pytest.mark.parametrize(
    "options",
    [
        ("--limit", "0"),
        ("--pipeline-id", "0"),
        ("--ref", ""),
        ("--name-glob", ""),
        ("--last", "1h", "--from", "2026-10-07T18:00:00Z"),
        ("--from", "2026-10-07T19:00:00Z", "--to", "2026-10-07T18:00:00Z"),
    ],
)
def test_job_query_rejects_invalid_or_conflicting_filters(
    options: tuple[str, ...],
) -> None:
    """Reject bad filter inputs before a provider read.

    :param options: Invalid CLI flag combination.
    """
    with pytest.raises(ValueError):
        _query(*options)


def test_job_query_combines_declared_filters_and_derives_stuck_state() -> None:
    """Apply combined filters and derive stuck state from pending age."""
    query = _query(
        "--status",
        "failed",
        "--pipeline-id",
        "77",
        "--ref",
        "main",
        "--search",
        "script",
        "--name-glob",
        "build*",
        "--from",
        "2026-10-07T18:01:00Z",
        "--to",
        "2026-10-07T18:03:00Z",
    )
    now = datetime(2026, 10, 7, 19, tzinfo=UTC)
    job = JOBS.JobRecord.from_api(_job(5), "gitlab.example.test")
    assert query.matches(job, now)
    assert not query.matches(
        JOBS.JobRecord.from_api(_job(4, ref="other"), "gitlab.example.test"), now
    )
    pending = JOBS.JobRecord.from_api(
        _job(3, status="pending", finished_at=None), "gitlab.example.test"
    )
    assert _query("--status", "stuck", "--stuck-after", "600").matches(pending, now)
    assert not _query("--status", "stuck", "--stuck-after", "7200").matches(
        pending, now
    )


def test_job_record_rejects_off_host_and_malformed_provider_data() -> None:
    """Reject an off-host link and a nonfinite provider duration."""
    with pytest.raises(ValueError, match="job_response_invalid"):
        JOBS.JobRecord.from_api(
            _job(5, web_url="https://other.example.test/job/5"),
            "gitlab.example.test",
        )
    with pytest.raises(ValueError, match="job_duration_invalid"):
        JOBS.JobRecord.from_api(_job(5, duration=float("nan")), "gitlab.example.test")


def test_job_list_follows_keyset_cursor_and_preserves_descending_ids() -> None:
    """Read successive provider pages without changing job order."""

    class Pages:
        """Return two cursor pages and capture requested endpoints."""

        def __init__(self) -> None:
            """Start with no requested endpoints."""
            self.endpoints: list[str] = []

        def get_json_with_headers(
            self, _session: object, endpoint: str
        ) -> tuple[list[dict[str, object]], str]:
            """Return the next page for the requested cursor.

            :param _session: Unused bound session.
            :param endpoint: Requested project job endpoint.
            :returns: Provider records and response headers.
            """
            self.endpoints.append(endpoint)
            if len(self.endpoints) == 1:
                link = f'<https://gitlab.example.test/api/v4/{endpoint}&id_before=4>; rel="next"'
                return [_job(5), _job(4)], f"Link: {link}"
            return [_job(3)], ""

    pages = Pages()
    result = JOBS.list_jobs(
        SimpleNamespace(host="gitlab.example.test"),
        42,
        _query("--limit", "10"),
        api_client=pages,
    )
    assert [record["id"] for record in result.records] == [5, 4, 3]
    assert not result.errors
    assert result.truncated is False
    assert "id_before=4" in pages.endpoints[1]


def test_job_list_reports_duplicate_cursor_data_as_incomplete() -> None:
    """Mark repeated provider records as incomplete."""

    class Pages:
        """Repeat one job on the second page."""

        def __init__(self) -> None:
            """Start with no API reads."""
            self.calls = 0

        def get_json_with_headers(
            self, _session: object, endpoint: str
        ) -> tuple[list[dict[str, object]], str]:
            """Return a repeated job after the first cursor.

            :param _session: Unused bound session.
            :param endpoint: Requested project job endpoint.
            :returns: Provider records and response headers.
            """
            self.calls += 1
            if self.calls == 1:
                link = f'<https://gitlab.example.test/api/v4/{endpoint}&id_before=5>; rel="next"'
                return [_job(5)], f"Link: {link}"
            return [_job(5)], ""

    result = JOBS.list_jobs(
        SimpleNamespace(host="gitlab.example.test"), 42, _query(), api_client=Pages()
    )
    assert result.truncated
    assert result.errors == [{"source": "jobs", "reason": "pagination_order_invalid"}]


def test_job_list_classifies_malformed_record_and_limit_truncation() -> None:
    """Keep valid jobs but report malformed data and a result limit."""

    class Page:
        """Return one page containing a malformed provider record."""

        def get_json_with_headers(
            self, _session: object, _endpoint: str
        ) -> tuple[list[dict[str, object]], str]:
            """Return four jobs including one with missing fields.

            :param _session: Unused bound session.
            :param _endpoint: Unused project job endpoint.
            :returns: Provider records and empty response headers.
            """
            return [{"id": 9}, _job(5), _job(4), _job(3)], ""

    result = JOBS.list_jobs(
        SimpleNamespace(host="gitlab.example.test"),
        42,
        _query("--limit", "2"),
        api_client=Page(),
    )
    assert [record["id"] for record in result.records] == [5, 4]
    assert result.truncated
    assert result.errors == [{"source": "jobs", "reason": "job_response_invalid"}]
