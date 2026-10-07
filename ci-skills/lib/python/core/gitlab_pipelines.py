"""Read and summarize one exact GitLab pipeline through a bound session."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .gitlab_api import GlabAPIClient
from .gitlab_job_fields import TERMINAL_JOB_STATUSES, GitLabJobFields
from .gitlab_session import BoundGitLabSession

PAGE_SIZE = 100
MAX_JOB_PAGES = 5


def _positive_id(value: Any) -> bool:
    return type(value) is int and value > 0


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _job(item: Any, pipeline_id: int) -> dict[str, Any]:
    """Keep shared job fields and verify this pipeline's relationship.

    :param item: One untrusted GitLab pipeline-job response.
    :param pipeline_id: Exact pipeline selected by the caller.
    :returns: Bounded job fields for the pipeline report.
    :raises ValueError: If fields are malformed or the pipeline differs.
    """
    try:
        fields = GitLabJobFields.from_api(item)
    except ValueError as exc:
        raise ValueError("pipeline_job_response_invalid") from exc
    pipeline = item.get("pipeline")
    if pipeline is not None and (
        not isinstance(pipeline, dict)
        or not _positive_id(pipeline.get("id"))
        or pipeline["id"] != pipeline_id
    ):
        raise ValueError("pipeline_job_reference_mismatch")
    return {
        "id": fields.id,
        "name": fields.name,
        "stage": fields.stage,
        "status": fields.status,
        "failure_reason": fields.failure_reason,
    }


def _counts(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Count jobs and their terminal progress for one pipeline scope.

    :param jobs: Validated pipeline job summaries.
    :returns: Total, terminal count, percentage, and statuses.
    """
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
    :param session: Exact-host GitLab session.
    :param project_id: Positive selected project ID.
    :param pipeline_id: Positive selected pipeline ID.
    :param api_client: GitLab transport supplied by the caller.
    :returns: Pipeline summary and bounded collection errors.
    :raises ValueError: If IDs or provider responses are invalid.
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
