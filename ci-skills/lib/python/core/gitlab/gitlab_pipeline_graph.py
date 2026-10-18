"""Collect one bounded GitLab pipeline and downstream graph.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import MutableMapping, MutableSequence, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import urlencode

from lib.python.core.cli_api import REQUEST_DEADLINE_EXCEEDED, RequestDeadline
from lib.python.core.gitlab.gitlab_actions import ActionError, _required_text
from lib.python.core.gitlab.gitlab_api import GitLabAPIError, GlabAPIClient
from lib.python.core.gitlab.gitlab_pipeline_watch_types import (
    MAX_CHOICES,
    MAX_GRAPH_DEPTH,
    MAX_PAGES,
    MAX_PIPELINES,
    PAGE_SIZE,
    PipelineKey,
    PipelineObservation,
    PipelineRoute,
    WatchJob,
)
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
from lib.python.core.json_types import JSONObject, JSONValue, json_object


def _positive_id(value: JSONValue, field: str) -> int:
    """Require a positive provider identity without accepting booleans.

    :param value: Decoded provider identifier.
    :param field: Field used in the stable failure reason.
    :returns: Positive built-in integer.
    :raises ActionError: The field is not a positive integer.
    """
    if type(value) is not int or value <= 0:
        raise ActionError(f"pipeline_{field}_invalid")
    return value


def _optional_text(value: JSONValue, field: str) -> Optional[str]:
    """Validate an optional provider string.

    :param value: Decoded provider field or JSON null.
    :param field: Field used in the stable failure reason.
    :returns: Original text or ``None``.
    :raises ActionError: A present value is nontext or blank.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ActionError(f"pipeline_{field}_invalid")
    return value


def _job(value: JSONValue, pipeline_id: int) -> WatchJob:
    """Validate one pipeline job required by watch evidence.

    :param value: Decoded GitLab job response.
    :param pipeline_id: Selected pipeline ID required by an optional relation.
    :returns: Compact job observation.
    :raises ActionError: A field or pipeline relation is malformed.
    """
    item = json_object(value)
    relation = item.get("pipeline")
    if relation is not None:
        relation = json_object(relation)
        if _positive_id(relation.get("id"), "job_pipeline_id") != pipeline_id:
            raise ActionError("pipeline_job_reference_mismatch")
    failure_reason = _optional_text(item.get("failure_reason"), "job_failure_reason")
    web_url = _optional_text(item.get("web_url"), "job_web_url")
    return WatchJob(
        id=_positive_id(item.get("id"), "job_id"),
        name=_required_text(item.get("name"), "pipeline_job_name"),
        stage=_required_text(item.get("stage"), "pipeline_job_stage"),
        status=_required_text(item.get("status"), "pipeline_job_status"),
        failure_reason=failure_reason,
        web_url=web_url,
    )


def _paged(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    endpoint: str,
    source: str,
    *,
    deadline: Optional[RequestDeadline] = None,
) -> tuple[Sequence[JSONValue], Sequence[JSONObject]]:
    """Read at most five pages and preserve explicit overflow evidence.

    :param api: Bound GitLab transport.
    :param session: Exact-host credential session.
    :param endpoint: Collection endpoint without pagination fields.
    :param source: Collection name used by an overflow error.
    :param deadline: Optional absolute deadline shared by every graph request.
    :returns: Decoded items and an overflow error when an extra page is nonempty.
    :raises ActionError: A page is not an array or exceeds the page size.
    :raises GitLabAPIError: A provider page read fails.
    """  # noqa: DOC503 - provider transport errors propagate unchanged.
    items: MutableSequence[JSONValue] = []
    errors: MutableSequence[JSONObject] = []
    separator = "&" if "?" in endpoint else "?"
    for page in range(1, MAX_PAGES + 2):
        try:
            if deadline is not None:
                deadline.remaining()
            value = api.get_json(
                session,
                f"{endpoint}{separator}per_page={PAGE_SIZE}&page={page}",
                deadline=deadline,
            )
        except GitLabAPIError as exc:
            if exc.reason != REQUEST_DEADLINE_EXCEEDED:
                raise
            errors.append({"source": source, "reason": exc.reason})
            break
        if not isinstance(value, list) or len(value) > PAGE_SIZE:
            raise ActionError(f"pipeline_{source}_envelope_invalid")
        if page > MAX_PAGES:
            if value:
                errors.append({"source": source, "reason": f"{source}_limit_exceeded"})
            break
        items.extend(value)
        if len(value) < PAGE_SIZE:
            break
    return items, errors


def _bridges(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    route: PipelineRoute,
    *,
    deadline: Optional[RequestDeadline] = None,
) -> tuple[Sequence[PipelineRoute], Sequence[JSONObject]]:
    """Read downstream pipelines started by this pipeline's bridge jobs.

    :param api: Bound GitLab transport.
    :param session: Exact-host credential session.
    :param route: Parent pipeline and its current graph depth.
    :param deadline: Optional absolute deadline shared by every graph request.
    :returns: Child routes and bounded bridge errors.
    :raises ActionError: A bridge or downstream identity is malformed.
    :raises GitLabAPIError: A provider page read fails.
    """  # noqa: DOC503 - provider transport errors propagate unchanged.
    key = route.key
    values, errors = _paged(
        api,
        session,
        f"projects/{key.project_id}/pipelines/{key.pipeline_id}/bridges",
        "bridges",
        deadline=deadline,
    )
    children: MutableSequence[PipelineRoute] = []
    for value in values:
        item = json_object(value)
        downstream = item.get("downstream_pipeline")
        if downstream is None:
            continue
        downstream = json_object(downstream)
        child = PipelineKey(
            _positive_id(downstream.get("project_id"), "bridge_project_id"),
            _positive_id(downstream.get("id"), "bridge_pipeline_id"),
        )
        name = _required_text(item.get("name"), "pipeline_bridge_name")
        children.append(PipelineRoute(child, f"bridge:{name}", route.depth + 1))
    return children, errors


def _observe(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    route: PipelineRoute,
    *,
    deadline: Optional[RequestDeadline] = None,
) -> PipelineObservation:
    """Read one pipeline, its jobs, and its direct bridge children.

    :param api: Bound GitLab transport.
    :param session: Exact-host credential session.
    :param route: Pipeline graph identity to inspect.
    :param deadline: Optional absolute deadline shared by every graph request.
    :returns: Validated pipeline observation.
    :raises ActionError: Provider response identity or fields are invalid.
    :raises GitLabAPIError: A required provider read fails.
    """  # noqa: DOC503 - provider transport errors propagate unchanged.
    key = route.key
    if deadline is not None:
        deadline.remaining()
    item = json_object(
        api.get_json(
            session,
            f"projects/{key.project_id}/pipelines/{key.pipeline_id}",
            deadline=deadline,
        )
    )
    if (
        _positive_id(item.get("id"), "id") != key.pipeline_id
        or _positive_id(item.get("project_id"), "project_id") != key.project_id
    ):
        raise ActionError("pipeline_response_identity_mismatch")
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs_future = pool.submit(
            _paged,
            api,
            session,
            f"projects/{key.project_id}/pipelines/{key.pipeline_id}/jobs",
            "jobs",
            deadline=deadline,
        )
        bridges_future = pool.submit(_bridges, api, session, route, deadline=deadline)
        try:
            values, job_errors = jobs_future.result()
            children, bridge_errors = bridges_future.result()
        finally:
            jobs_future.cancel()
            bridges_future.cancel()
    jobs = tuple(_job(value, key.pipeline_id) for value in values)
    if len({job.id for job in jobs}) != len(jobs):
        raise ActionError("pipeline_jobs_duplicate_id")
    return PipelineObservation(
        route=route,
        status=_required_text(item.get("status"), "pipeline_status"),
        ref=_required_text(item.get("ref"), "pipeline_ref"),
        sha=_required_text(item.get("sha"), "pipeline_sha"),
        jobs=jobs,
        children=children,
        errors=(*job_errors, *bridge_errors),
    )


def _related(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    root: PipelineObservation,
    pattern: re.Pattern[str],
    *,
    deadline: Optional[RequestDeadline] = None,
) -> tuple[Sequence[PipelineRoute], Sequence[JSONObject]]:
    """Select newer root-project pipelines whose provider name matches.

    :param api: Bound GitLab transport.
    :param session: Exact-host credential session.
    :param root: Root pipeline supplying project, ref, and lower ID bound.
    :param pattern: Caller-compiled related pipeline expression.
    :param deadline: Optional absolute deadline shared by every graph request.
    :returns: Related routes and bounded list errors.
    :raises ActionError: A listed pipeline has malformed identity or name.
    :raises GitLabAPIError: A provider page read fails.
    """  # noqa: DOC503 - provider transport errors propagate unchanged.
    key = root.route.key
    query = urlencode({"ref": root.ref})
    values, errors = _paged(
        api,
        session,
        f"projects/{key.project_id}/pipelines?{query}",
        "related",
        deadline=deadline,
    )
    selected: MutableSequence[PipelineRoute] = []
    for value in values:
        item = json_object(value)
        pipeline_id = _positive_id(item.get("id"), "related_id")
        project_id = _positive_id(
            item.get("project_id", key.project_id), "related_project_id"
        )
        name = _optional_text(item.get("name"), "related_name")
        if (
            project_id == key.project_id
            and pipeline_id > key.pipeline_id
            and name is not None
            and pattern.search(name)
        ):
            selected.append(
                PipelineRoute(PipelineKey(project_id, pipeline_id), "related", 0)
            )
    return selected, errors


def _scope(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    root: PipelineRoute,
    related_name: Optional[str],
    *,
    deadline: Optional[RequestDeadline] = None,
) -> tuple[Sequence[PipelineObservation], Sequence[JSONObject]]:
    """Discover and observe the complete bounded graph for one poll.

    :param api: Bound GitLab transport.
    :param session: Exact-host credential session.
    :param root: Required root pipeline route.
    :param related_name: Optional root-project pipeline-name expression.
    :param deadline: Optional absolute deadline shared by every graph request.
    :returns: Sorted observations and explicit collection or scope errors.
    :raises ActionError: The root read or provider evidence is invalid.
    :raises GitLabAPIError: The root provider read fails.
    """  # noqa: DOC503 - the root read must remain a blocking provider failure.
    pattern = re.compile(related_name) if related_name is not None else None
    pending = deque([root])
    routes: MutableMapping[PipelineKey, PipelineRoute] = {root.key: root}
    observations: MutableMapping[PipelineKey, PipelineObservation] = {}
    errors: MutableSequence[JSONObject] = []
    dropped: set[PipelineKey] = set()
    while pending:
        route = pending.popleft()
        try:
            observation = _observe(api, session, route, deadline=deadline)
        except (ActionError, GitLabAPIError) as exc:
            if (
                isinstance(exc, GitLabAPIError)
                and exc.reason == REQUEST_DEADLINE_EXCEEDED
            ):
                errors.append({"source": "scope", "reason": exc.reason})
                break
            if route.key == root.key:
                raise
            errors.append(
                {
                    "source": (
                        f"pipeline:{route.key.project_id}:{route.key.pipeline_id}"
                    ),
                    "reason": str(exc),
                }
            )
            continue
        observations[route.key] = observation
        errors.extend(observation.errors)
        discovered: Sequence[PipelineRoute] = observation.children
        if route.key == root.key and pattern is not None:
            related, related_errors = _related(
                api, session, observation, pattern, deadline=deadline
            )
            discovered = (*discovered, *related)
            errors.extend(related_errors)
        for child in discovered:
            if child.depth > MAX_GRAPH_DEPTH or child.key in routes:
                continue
            if len(routes) >= MAX_PIPELINES:
                dropped.add(child.key)
                continue
            routes[child.key] = child
            pending.append(child)
    if dropped:
        errors.append(
            {"source": "scope", "reason": "scope_limit_exceeded", "count": len(dropped)}
        )
    ordered = sorted(
        observations.values(),
        key=lambda item: (
            item.route.depth,
            item.route.key.project_id,
            item.route.key.pipeline_id,
        ),
    )
    if len(observations) != len(routes):
        errors.append(
            {
                "source": "scope",
                "reason": "pipeline_scope_incomplete",
                "count": len(routes) - len(observations),
            }
        )
    return ordered, errors


def _choice_job(job: WatchJob) -> Optional[JSONObject]:
    """Build a failed-job retrieve choice when its URL is available.

    :param job: Settled job observation.
    :returns: CI09 retrieve choice, or ``None`` when no safe URL was returned.
    """
    if job.status != "failed" or job.web_url is None:
        return None
    reason = job.failure_reason or "unknown_failure"
    return {
        "id": f"job:{job.id}",
        "kind": "retrieve",
        "summary": f"failed: {reason}, {job.name}",
        "next": {
            "action": "retrieve",
            "entrypoint": "bin/gitlab_job.py",
            "args": ["--job-url", job.web_url, "--json"],
        },
    }


def _choice_pipeline(observation: PipelineObservation) -> JSONObject:
    """Build one narrowing pointer for an unsuccessful pipeline.

    :param observation: Pipeline whose status differs from success.
    :returns: CI09 retrieve choice for the exact project and pipeline IDs.
    """
    key = observation.route.key
    return {
        "id": f"pipeline:{key.pipeline_id}",
        "kind": "retrieve",
        "summary": f"every job of pipeline {key.pipeline_id} by stage",
        "next": {
            "action": "retrieve",
            "entrypoint": "bin/gitlab_pipeline.py",
            "args": [
                "get",
                "--project",
                str(key.project_id),
                "--pipeline-id",
                str(key.pipeline_id),
                "--json",
            ],
        },
    }


def _choices(
    observations: Sequence[PipelineObservation],
) -> tuple[Sequence[JSONObject], Sequence[JSONObject]]:
    """Select failed jobs first, then unsuccessful pipelines, within the cap.

    :param observations: Final pipeline observations.
    :returns: Retained choices and a truncation error when more than twelve exist.
    """
    available: MutableSequence[JSONObject] = []
    for observation in observations:
        for job in observation.jobs:
            choice = _choice_job(job)
            if choice is not None:
                available.append(choice)
    available.extend(
        _choice_pipeline(item) for item in observations if item.status != "success"
    )
    errors: Sequence[JSONObject] = (
        (
            {
                "source": "choices",
                "reason": "choices_limit_exceeded",
                "count": len(available),
            },
        )
        if len(available) > MAX_CHOICES
        else ()
    )
    return tuple(available[:MAX_CHOICES]), errors
