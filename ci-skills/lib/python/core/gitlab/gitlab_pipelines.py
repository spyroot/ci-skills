"""Read and summarize one exact GitLab pipeline through a bound session.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from typing import Optional, TypeGuard

from lib.python.core.gitlab.gitlab_api import GlabAPIClient
from lib.python.core.gitlab.gitlab_job_fields import (
    TERMINAL_JOB_STATUSES,
    GitLabJobFields,
)
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
from lib.python.core.json_types import JSONObject, JSONValue, json_object

PAGE_SIZE = 100
MAX_JOB_PAGES = 5


def _positive_id(value: JSONValue) -> TypeGuard[int]:
    """Narrow a provider ID without accepting booleans.

    :param value: Decoded provider ID value.
    :returns: Whether the value is a positive built-in integer.
    """
    return type(value) is int and value > 0


def _nonempty(value: JSONValue) -> TypeGuard[str]:
    """Narrow a required provider text field.

    :param value: Decoded provider field value.
    :returns: Whether the value is a nonblank string.
    """
    return isinstance(value, str) and bool(value.strip())


def _job(item: JSONValue, pipeline_id: int) -> GitLabJobFields:
    """Validate the shared job fields and selected pipeline relationship.

    :param item: Decoded GitLab pipeline-job response value.
    :param pipeline_id: Exact pipeline selected by the caller.
    :returns: Concrete job fields for progress counting and report rendering.
    :raises ValueError: Shared fields are malformed or the pipeline differs.
    """
    try:
        item = json_object(item)
        fields = GitLabJobFields.from_api(item)
    except (TypeError, ValueError) as exc:
        raise ValueError("pipeline_job_response_invalid") from exc
    pipeline = item.get("pipeline")
    if pipeline is not None and (
        not isinstance(pipeline, Mapping)
        or not _positive_id(pipeline.get("id"))
        or pipeline["id"] != pipeline_id
    ):
        raise ValueError("pipeline_job_reference_mismatch")
    return fields


def _counts(jobs: Sequence[GitLabJobFields]) -> JSONObject:
    """Count jobs and terminal progress within one pipeline or stage.

    :param jobs: Validated common job fields.
    :returns: Total, terminal count, percentage, and status counts; percentage is
        JSON null when there are no jobs to count.
    """
    by_status = Counter(job.status for job in jobs)
    completed = sum(by_status[status] for status in TERMINAL_JOB_STATUSES)
    total = len(jobs)
    return {
        "total": total,
        "terminal": completed,
        "terminal_percent": round(completed * 100 / total, 1) if total else None,
        "by_status": dict(sorted(by_status.items())),
    }


def _pipeline(
    client: GlabAPIClient,
    session: BoundGitLabSession,
    base: str,
    project_id: int,
    pipeline_id: int,
) -> JSONObject:
    """Read and validate the exact selected pipeline identity.

    :param client: Bound GitLab transport.
    :param session: Authenticated host and credential session.
    :param base: Exact project pipeline endpoint derived by the caller.
    :param project_id: Positive access-verified project ID.
    :param pipeline_id: Positive requested pipeline ID.
    :returns: Provider JSON object after identity and required text validation.
    :raises ValueError: Identity or required status, SHA, or ref fields are invalid.
    :raises GitLabAPIError: The exact pipeline read fails.
    """  # noqa: DOC503 - the exact pipeline read propagates GitLabAPIError
    pipeline = client.get_json(session, base)
    if (
        not isinstance(pipeline, Mapping)
        or not _positive_id(pipeline.get("id"))
        or pipeline["id"] != pipeline_id
        or not _positive_id(pipeline.get("project_id"))
        or pipeline["project_id"] != project_id
        or any(not _nonempty(pipeline.get(field)) for field in ("status", "sha", "ref"))
    ):
        raise ValueError("pipeline_response_invalid")
    return pipeline


def _pipeline_jobs(
    client: GlabAPIClient,
    session: BoundGitLabSession,
    base: str,
    pipeline_id: int,
) -> tuple[Sequence[GitLabJobFields], Sequence[Mapping[str, str]]]:
    """Read bounded job pages and distinguish an exact bound from truncation.

    :param client: Bound GitLab transport.
    :param session: Authenticated host and credential session.
    :param base: Exact project pipeline endpoint.
    :param pipeline_id: Pipeline identity required for every job relation.
    :returns: Validated jobs and an error only when the extra page exceeds the cap.
    :raises ValueError: A page, job, pipeline relation, or repeated job ID is invalid.
    :raises GitLabAPIError: A pipeline job page cannot be read.
    """  # noqa: DOC503 - job page reads propagate GitLabAPIError
    jobs: MutableSequence[GitLabJobFields] = []
    errors: Sequence[Mapping[str, str]] = []
    for page in range(1, MAX_JOB_PAGES + 2):
        items = client.get_json(
            session, f"{base}/jobs?per_page={PAGE_SIZE}&page={page}"
        )
        if not isinstance(items, list) or len(items) > PAGE_SIZE:
            raise ValueError("pipeline_jobs_envelope_invalid")
        if page > MAX_JOB_PAGES:
            if items:
                errors = [{"source": "jobs", "reason": "job_limit_exceeded"}]
            break
        jobs.extend(_job(item, pipeline_id) for item in items)
        if len(items) < PAGE_SIZE:
            break
    identifiers = [job.id for job in jobs]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("pipeline_jobs_duplicate_id")
    return jobs, errors


def _stage_counts(jobs: Sequence[GitLabJobFields]) -> Sequence[JSONObject]:
    """Group validated jobs by stage before computing each stage's progress.

    :param jobs: Pipeline jobs retained by the bounded page scan.
    :returns: Stage-name-sorted JSON progress objects.
    """
    stages: MutableMapping[str, MutableSequence[GitLabJobFields]] = {}
    for job in jobs:
        stages.setdefault(job.stage, []).append(job)
    return [{"name": name, **_counts(items)} for name, items in sorted(stages.items())]


def read_pipeline(
    session: BoundGitLabSession,
    project_id: int,
    pipeline_id: int,
    api_client: Optional[GlabAPIClient] = None,
) -> tuple[JSONObject, Sequence[Mapping[str, str]]]:
    """Read exact pipeline progress and retain bounded job-collection errors.

    :param session: Exact-host GitLab session.
    :param project_id: Positive selected project ID.
    :param pipeline_id: Positive selected pipeline ID.
    :param api_client: Optional caller-owned transport; otherwise construct the native client.
    :returns: Pipeline summary and bounded collection errors, with unchanged public fields.
    :raises ValueError: Selected IDs or provider responses are invalid.
    :raises GitLabAPIError: A pipeline or job collection request fails.
    """  # noqa: DOC503 - pipeline and job collection reads propagate GitLabAPIError
    if not _positive_id(project_id) or not _positive_id(pipeline_id):
        raise ValueError("project_and_pipeline_ids_must_be_positive")
    client = api_client if api_client is not None else GlabAPIClient()
    base = f"projects/{project_id}/pipelines/{pipeline_id}"
    pipeline = _pipeline(client, session, base, project_id, pipeline_id)
    jobs, errors = _pipeline_jobs(client, session, base, pipeline_id)
    return {
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
        "stages": _stage_counts(jobs),
        "jobs": [job.public() for job in jobs],
    }, errors
