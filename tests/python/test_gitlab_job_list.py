"""GitLab job list filtering and keyset pagination stay bounded."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from tests.python.conftest import import_script_module

JOBS = import_script_module("core.gitlab_jobs")

SESSION = SimpleNamespace(host="gitlab.example.test")
INITIAL_ENDPOINT = (
    "projects/42/jobs?pagination=keyset&per_page=100&order_by=id&sort=desc"
)


class JobListAPI:
    """Return planned GitLab pages and record the exact endpoints requested."""

    def __init__(self, *responses: tuple[object, str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[object, str]] = []

    def get_json_with_headers(self, session: object, endpoint: str) -> tuple[Any, str]:
        self.calls.append((session, endpoint))
        if not self.responses:
            raise AssertionError(f"unexpected GitLab read: {endpoint}")
        return self.responses.pop(0)


def _query(**overrides: object):
    values = {
        "statuses": (),
        "pipeline_id": None,
        "ref": None,
        "search": None,
        "name_glob": None,
        "limit": 10,
        "start": None,
        "end": None,
        "stuck_after": 300,
    }
    values.update(overrides)
    return JOBS.JobQuery(**values)


def _headers(next_endpoint: str | None = None, *, host: str = "gitlab.example.test"):
    if next_endpoint is None:
        return "HTTP/2 200\r\n"
    return (
        f'HTTP/2 200\r\nLink: <https://{host}/api/v4/{next_endpoint}>; rel="next"\r\n'
    )


def _job(
    job_id: int,
    *,
    name: str = "test-linux",
    stage: str = "test",
    status: str = "failed",
    failure_reason: str | None = "runner oom",
    pipeline_id: int = 8,
    ref: str = "main",
) -> dict[str, object]:
    return {
        "id": job_id,
        "name": name,
        "stage": stage,
        "status": status,
        "failure_reason": failure_reason,
        "created_at": "2026-10-07T10:00:00Z",
        "started_at": None,
        "finished_at": "2026-10-07T10:01:00Z",
        "duration": 60.0,
        "runner": None,
        "pipeline": {"id": pipeline_id},
        "ref": ref,
        "web_url": f"https://gitlab.example.test/unit/repo/-/jobs/{job_id}",
    }


def test_job_list_applies_combined_filters_and_limit_before_more_pages():
    query = _query(
        statuses=("failed",),
        pipeline_id=8,
        ref="main",
        search="oom",
        name_glob="test-*",
        limit=1,
    )
    api = JobListAPI(
        (
            [
                _job(30, status="running"),
                _job(29, ref="feature"),
                _job(28),
                _job(27, name="test-macos"),
            ],
            _headers("projects/42/jobs?pagination=keyset&per_page=100&id_before=27"),
        )
    )

    result = JOBS.list_jobs(SESSION, 42, query, api_client=api)

    assert [record["id"] for record in result.records] == [28]
    assert result.errors == []
    assert result.truncated is True
    assert len(api.calls) == 1
    assert api.calls[0] == (
        SESSION,
        f"{INITIAL_ENDPOINT}&scope%5B%5D=failed",
    )


def test_job_list_follows_keyset_next_cursor_until_collection_end():
    next_endpoint = f"{INITIAL_ENDPOINT}&id_before=99"
    api = JobListAPI(
        ([_job(100), _job(99)], _headers(next_endpoint)),
        ([_job(98)], _headers()),
    )

    result = JOBS.list_jobs(SESSION, 42, _query(limit=10), api_client=api)

    assert [record["id"] for record in result.records] == [100, 99, 98]
    assert result.errors == []
    assert result.truncated is False
    assert api.calls == [(SESSION, INITIAL_ENDPOINT), (SESSION, next_endpoint)]


def test_job_list_reports_bad_keyset_cursor_as_partial_result():
    next_endpoint = f"{INITIAL_ENDPOINT}&id_before=99"
    api = JobListAPI(([_job(100)], _headers(next_endpoint, host="evil.example.test")))

    result = JOBS.list_jobs(SESSION, 42, _query(limit=10), api_client=api)

    assert [record["id"] for record in result.records] == [100]
    assert result.errors == [{"source": "jobs", "reason": "pagination_link_invalid"}]
    assert result.truncated is True
    assert api.calls == [(SESSION, INITIAL_ENDPOINT)]
