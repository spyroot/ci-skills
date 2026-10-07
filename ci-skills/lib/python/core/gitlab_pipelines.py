"""Read and summarize one exact GitLab pipeline through a bound session."""

from __future__ import annotations
from collections import Counter
from typing import Any
from .gitlab_api import GlabAPIClient
from .gitlab_session import BoundGitLabSession

PAGE_SIZE = 100
MAX_JOB_PAGES = 5
TERMINAL_JOB_STATUSES = frozenset({"success", "failed", "canceled", "skipped"})


def _positive_id(value: Any) -> bool:
    return type(value) is int and value > 0


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _job(item: Any, pipeline_id: int) -> dict[str, Any]:
    """Keep bounded fields only, after rejecting a malformed job response."""
    if not isinstance(item, dict) or not all(
        (
            _positive_id(item.get("id")),
            _nonempty(item.get("name")),
            _nonempty(item.get("stage")),
            _nonempty(item.get("status")),
        )
    ):
        raise ValueError("pipeline_job_response_invalid")

    failure_reason = item.get("failure_reason")
    if failure_reason is not None and not isinstance(failure_reason, str):
        raise ValueError("pipeline_job_response_invalid")
    pipeline = item.get("pipeline")
    if pipeline is not None and (
        not isinstance(pipeline, dict)
        or not _positive_id(pipeline.get("id"))
        or pipeline["id"] != pipeline_id
    ):
        raise ValueError("pipeline_job_reference_mismatch")
    return {
        "id": item["id"],
        "name": item["name"],
        "stage": item["stage"],
        "status": item["status"],
        "failure_reason": failure_reason,
    }


def _counts(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    by_status = Counter(job["status"] for job in jobs)
    completed = sum(by_status[status] for status in TERMINAL_JOB_STATUSES)
    total = len(jobs)
    return {
        "total": total,
        "terminal": completed,
        "terminal_percent": round(completed * 100 / total, 1) if total else None,
        "by_status": dict(sorted(by_status.items())),
    }


def read_pipeline(
    session: BoundGitLabSession,
    project_id: int,
    pipeline_id: int,
    *,
    api_client: Any = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Return validated progress; mark the bounded result partial if truncated.

    The additional page after the cap distinguishes exactly 500 jobs from an
    incomplete result. No pipeline variables or traces are fetched.
    :param session:
    :param project_id:
    :param pipeline_id:
    :param api_client:
    :return:
    """
    if not _positive_id(project_id) or not _positive_id(pipeline_id):
        raise ValueError("project_and_pipeline_ids_must_be_positive")

    client = api_client or GlabAPIClient()
    base = f"projects/{project_id}/pipelines/{pipeline_id}"
    pipeline = client.get_json(session, base)

    if (
        not isinstance(pipeline, dict)
        or not _positive_id(pipeline.get("id"))
        or pipeline.get("id") != pipeline_id
        or not _positive_id(pipeline.get("project_id"))
        or pipeline.get("project_id") != project_id
        or not _nonempty(pipeline.get("status"))
        or not _nonempty(pipeline.get("sha"))
        or not _nonempty(pipeline.get("ref"))
    ):
        raise ValueError("pipeline_response_invalid")

    jobs: list[dict[str, Any]] = []
    for page in range(1, MAX_JOB_PAGES + 2):

        items = client.get_json(
            session, f"{base}/jobs?per_page={PAGE_SIZE}&page={page}"
        )

        if not isinstance(items, list) or len(items) > PAGE_SIZE:
            raise ValueError("pipeline_jobs_envelope_invalid")

        if page > MAX_JOB_PAGES:
            if items:
                errors = [{"source": "jobs", "reason": "job_limit_exceeded"}]
            else:
                errors = []
            break

        jobs.extend(_job(item, pipeline_id) for item in items)
        if len(items) < PAGE_SIZE:
            errors = []
            break

    ids = [item["id"] for item in jobs]

    if len(ids) != len(set(ids)):
        raise ValueError("pipeline_jobs_duplicate_id")
    stages: dict[str, list[dict[str, Any]]] = {}

    for job in jobs:
        stages.setdefault(job["stage"], []).append(job)


    record = {
        "id": pipeline_id,
        "project_id": project_id,
        "status": pipeline["status"],
        "ref": pipeline.get("ref"),
        "sha": pipeline.get("sha"),
        "created_at": pipeline.get("created_at"),
        "updated_at": pipeline.get("updated_at"),
        "started_at": pipeline.get("started_at"),
        "finished_at": pipeline.get("finished_at"),
        "progress": _counts(jobs),
        "stages": [
            {"name": name, **_counts(stage_jobs)}
            for name, stage_jobs in sorted(stages.items())
        ],
        "jobs": jobs,
    }
    return record, errors
