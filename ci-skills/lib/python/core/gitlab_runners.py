"""Scoped GitLab runner reads and confirmed record lifecycle actions."""

from __future__ import annotations

import os
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TypedDict

from .gitlab_actions import (
    ActionError,
    ActionPlan,
    _fields,
    _id,
    _object,
    _pages,
    _positive,
    _required_text,
)
from .gitlab_api import (
    GitLabAPIError,
    create_guard,
    runner_update_guard,
    uncertain_write,
    unverified_write,
)
from .target import GitLabOperationTarget

MAX_TAG_UPDATE_ATTEMPTS: Final[int] = 2


class RunnerRecord(TypedDict):
    """Bounded public fields from one GitLab runner read.

    .. attribute :: id
        :type: int
        Runner ID.

    .. attribute :: description
        :type: str | None
        Runner description, when set.

    .. attribute :: runner_type
        :type: str
        GitLab runner scope type.

    .. attribute :: tag_list
        :type: list[str]
        Runner job tags.

    .. attribute :: status
        :type: str | None
        Runner status reported by GitLab.

    .. attribute :: online
        :type: bool | None
        Whether GitLab reports the runner online.

    .. attribute :: paused
        :type: bool | None
        Whether the runner is paused.

    .. attribute :: is_shared
        :type: bool | None
        Whether GitLab reports the runner as shared.

    .. attribute :: access_level
        :type: str | None
        Protected or unprotected ref access.

    .. attribute :: job_execution_status
        :type: str | None
        Current job execution state.

    .. attribute :: projects
        :type: list[int] | None
        IDs of linked projects, when returned.

    .. attribute :: contacted_at
        :type: str | None
        Last contact timestamp, when returned.

    .. attribute :: version
        :type: str | None
        Runner version, when returned.

    .. attribute :: platform
        :type: str | None
        Runner platform, when returned.

    .. attribute :: architecture
        :type: str | None
        Runner architecture, when returned.
    """

    id: int
    description: str | None
    runner_type: str
    tag_list: list[str]
    status: str | None
    online: bool | None
    paused: bool | None
    is_shared: bool | None
    access_level: str | None
    job_execution_status: str | None
    projects: list[int] | None
    contacted_at: str | None
    version: str | None
    platform: str | None
    architecture: str | None


class RelatedJob(TypedDict):
    """Exact job identity that led to a runner read.

    .. attribute :: id
        :type: int
        Job ID.

    .. attribute :: status
        :type: str
        Observed job status.

    .. attribute :: failure_reason
        :type: str | None
        Provider failure reason, when available.

    .. attribute :: pipeline_id
        :type: int | None
        Parent pipeline ID, when available.

    .. attribute :: runner_id
        :type: int | None
        Assigned runner ID, or ``None`` while unassigned.
    """

    id: int
    status: str
    failure_reason: str | None
    pipeline_id: int | None
    runner_id: int | None


@dataclass(frozen=True)
class RunnerReadResult:
    """Compact runner records and their source relation.

    .. attribute :: records
        :type: list[RunnerRecord]
        Selected public runner records.

    .. attribute :: truncated
        :type: bool
        Whether more matching runners exist.

    .. attribute :: related_job
        :type: RelatedJob | None
        Job that led to the runner, when selected by job ID.
    """

    records: list[RunnerRecord]
    truncated: bool
    related_job: RelatedJob | None


def prepare(
    args: Namespace, target: GitLabOperationTarget, target_kind: str
) -> tuple[dict[str, Any], int | None, str | None]:
    """Validate one runner action before the shared plan is fingerprinted.

    :param args: Parsed command arguments.
    :param target: Selected nonsecret GitLab target.
    :param target_kind: Access scope, ``project`` or ``group``.
    :returns: Request body, existing runner ID, and one-time token sink.
    :raises ActionError: If the action or its arguments are invalid.
    """
    if args.action in {"get", "list", "delete"}:
        if args.token_out or args.tag or args.runner_type:
            raise ActionError("runner_read_or_delete_rejects_create_options")
        if args.action == "list":
            if (
                args.runner_id is not None
                or args.job_id is not None
                or not 1 <= args.limit <= 100
            ):
                raise ActionError("runner_list_selector_invalid")
            filters = tuple(dict.fromkeys(args.filter))
            if (
                {"protected", "unprotected"} <= set(filters)
                or {"online", "offline"} <= set(filters)
                or {"shared", "dedicated"} <= set(filters)
                or {"paused", "active"} <= set(filters)
            ):
                raise ActionError("runner_filters_conflict")
            return (
                {
                    "description": args.description,
                    "limit": args.limit,
                    "filters": list(filters),
                },
                None,
                None,
            )
        if args.filter:
            raise ActionError("runner_filter_only_valid_for_list")
        if args.limit != 10:
            raise ActionError("runner_limit_only_valid_for_list")
        if args.job_id is not None:
            if args.action != "get" or target_kind != "project":
                raise ActionError("job_id_requires_project_runner_get")
            if args.runner_id is not None or args.description:
                raise ActionError("job_id_conflicts_with_runner_selector")
            return {"job_id": _positive(args.job_id, "job_id")}, None, None
        if args.action == "delete" and args.runner_id is None:
            raise ActionError("runner_delete_requires_runner_id")
        if args.runner_id is None and not args.description:
            raise ActionError("runner_id_or_description_required")
        identifier = (
            _positive(args.runner_id, "runner_id")
            if args.runner_id is not None
            else None
        )
        description = (
            _required_text(args.description, "description")
            if args.description
            else None
        )
        return {"description": description}, identifier, None
    if args.limit != 10:
        raise ActionError("runner_limit_only_valid_for_list")
    if args.filter:
        raise ActionError("runner_filter_only_valid_for_list")
    if args.job_id is not None:
        raise ActionError("job_id_only_valid_for_runner_get")
    if args.action == "assign":
        identifier = _positive(args.runner_id or target.gitlab.runner_id, "runner_id")
        if args.token_out or args.description or args.tag or args.runner_type:
            raise ActionError("assign_accepts_runner_id_only")
        return {}, identifier, None
    if args.action == "tag":
        identifier = _positive(args.runner_id, "runner_id")
        if args.token_out or args.description or args.runner_type:
            raise ActionError("tag_accepts_runner_id_and_tags_only")
        tags = _requested_tags(args.tag)
        if not tags:
            raise ActionError("tag_requires_tag")
        return {"tag_list": tags}, identifier, None
    if args.action != "create":
        raise ActionError("unsupported_runner_action")
    body: dict[str, Any] = {
        "runner_type": f"{target_kind}_type",
        "description": _required_text(args.description, "description"),
    }
    tags = _requested_tags(args.tag)
    if tags:
        body["tag_list"] = ",".join(tags)
    if args.runner_id is not None:
        raise ActionError("runner_id_only_valid_for_assign")
    if not args.token_out:
        raise ActionError("token_out_required_for_runner_create_plan")
    token_out = (
        str(Path(args.token_out).expanduser().absolute()) if args.token_out else None
    )
    return body, None, token_out


def _requested_tags(values: list[str]) -> list[str]:
    """Validate caller tags for both record creation and later additions.

    :param values: Repeated ``--tag`` values from the command parser.
    :returns: Distinct, nonempty tag names in caller order.
    :raises ActionError: If a tag is empty, contains a comma, or is repeated.
    """
    tags = [_required_text(value, "tag") for value in values]
    if any("," in tag for tag in tags):
        raise ActionError("tag_must_not_contain_comma")
    if len(set(tags)) != len(tags):
        raise ActionError("duplicate_tag")
    return tags


def _observed_tags(value: Any) -> list[str]:
    """Accept only a concrete GitLab tag list for a runner read-back.

    :param value: Provider ``tag_list`` value.
    :returns: Nonempty tag names in provider order.
    :raises ActionError: If GitLab returns an invalid or duplicated tag.
    """
    if not isinstance(value, list) or any(
        not isinstance(tag, str) or not tag or "," in tag for tag in value
    ):
        raise ActionError("runner_tag_list_invalid_shape")
    if len(set(value)) != len(value):
        raise ActionError("runner_tag_list_duplicate")
    return value


def project_ids(api: Any, session: Any, group_id: int) -> tuple[int, ...]:
    """Resolve validated group membership for a live runner plan.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param group_id: Group whose direct and subgroup projects are selected.
    :returns: Sorted project IDs from the live group response.
    :raises ActionError: If membership is empty, duplicated, or malformed.
    """
    projects = _pages(
        api,
        session,
        f"groups/{group_id}/projects?include_subgroups=true&with_shared=false",
    )
    identifiers = [
        _id(item["id"], "project") for item in _fields(projects, "group_projects", "id")
    ]
    if len(set(identifiers)) != len(identifiers):
        raise ActionError("group_projects_duplicate_id")
    if not identifiers:
        raise ActionError("group_projects_empty")
    return tuple(sorted(identifiers))


def _direct_projects(api: Any, session: Any, runner_id: int) -> frozenset[int]:
    """Read direct runner assignments without inherited availability.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param runner_id: Project runner whose assignments are needed.
    :returns: IDs of directly assigned projects.
    :raises ActionError: If runner identity, type, or projects are invalid.
    """
    runner = _object(
        api.get_json(session, f"runners/{runner_id}"),
        "runner",
        "id",
        "runner_type",
        "projects",
    )
    if runner["id"] != runner_id:
        raise ActionError("runner_id_mismatch")
    if runner["runner_type"] != "project_type":
        raise ActionError("runner_assignment_requires_project_type")
    if not isinstance(runner["projects"], list):
        raise ActionError("runner_projects_invalid_shape")
    projects = [
        _id(item["id"], "runner_project")
        for item in _fields(runner["projects"], "runner_project", "id")
    ]
    if len(projects) != len(set(projects)):
        raise ActionError("runner_projects_duplicate_id")
    return frozenset(projects)


def _assigned(api: Any, session: Any, runner_id: int, project_id: int) -> bool:
    return project_id in _direct_projects(api, session, runner_id)


def _membership_snapshot(
    api: Any,
    session: Any,
    runner_id: int,
    identifiers: tuple[int, ...],
) -> dict[int, bool]:
    """Snapshot direct assignments for selected project IDs.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param runner_id: Runner whose assignments are read.
    :param identifiers: Project IDs selected for the action.
    :returns: Each project ID mapped to its current assignment state.
    """
    direct = _direct_projects(api, session, runner_id)
    return {project_id: project_id in direct for project_id in identifiers}


def _rollback_assignments(
    api: Any,
    session: Any,
    runner_id: int,
    attempted: list[int],
    baseline: dict[int, bool],
) -> dict[str, Any]:
    """Undo attempted assignments and read their final state.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param runner_id: Runner whose attempted assignments are removed.
    :param attempted: Project IDs changed by this invocation.
    :param baseline: Assignment state before the action.
    :returns: Cleanup status, final restoration flag, and per-project records.
    """
    records: list[dict[str, Any]] = []
    for project_id in reversed(attempted):
        scope = f"projects/{project_id}/runners/{runner_id}"
        try:
            if _assigned(api, session, runner_id, project_id):
                api.delete_json(session, scope)
            absent = not _assigned(api, session, runner_id, project_id)
            records.append(
                {
                    "project_id": project_id,
                    "status": "PASS" if absent else "BLOCKED",
                    "verified_absent": absent,
                }
            )
        except (ActionError, GitLabAPIError) as exc:
            records.append(
                {"project_id": project_id, "status": "BLOCKED", "reason": str(exc)}
            )
    try:
        final = _membership_snapshot(api, session, runner_id, tuple(sorted(baseline)))
        restored = final == baseline
    except (ActionError, GitLabAPIError):
        restored = False
    return {
        "status": "PASS"
        if restored and all(record["status"] == "PASS" for record in records)
        else "BLOCKED",
        "restored": restored,
        "records": records,
    }


def _assign(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
    runner_id = _positive(plan.resource_id, "runner_id")
    _direct_projects(api, session, runner_id)
    if plan.target_kind == "project":
        identifiers = (target_id,)
    else:
        identifiers = project_ids(api, session, target_id)
        if plan.project_ids is None:
            raise ActionError("group_assignment_requires_live_plan")
        if identifiers != plan.project_ids:
            raise ActionError("group_projects_changed_rerun_live_plan")
    baseline = _membership_snapshot(api, session, runner_id, identifiers)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    attempted: list[int] = []
    for project_id in identifiers:
        if baseline[project_id]:
            results.append(
                {"project_id": project_id, "action": "NO_OP", "verified": True}
            )
            continue
        attempted.append(project_id)
        try:
            api.post_json(
                session, f"projects/{project_id}/runners", {"runner_id": runner_id}
            )
            if not _assigned(api, session, runner_id, project_id):
                raise ActionError("runner_assignment_readback_mismatch")
            results.append(
                {"project_id": project_id, "action": "APPLIED", "verified": True}
            )
        except (ActionError, GitLabAPIError) as exc:
            errors.append({"project_id": project_id, "reason": str(exc)})
            break
    if not errors:
        try:
            final = _membership_snapshot(api, session, runner_id, identifiers)
            if not all(final.values()):
                errors.append(
                    {
                        "project_id": target_id,
                        "reason": "runner_final_readback_mismatch",
                    }
                )
        except (ActionError, GitLabAPIError) as exc:
            errors.append({"project_id": target_id, "reason": str(exc)})
    cleanup = (
        _rollback_assignments(api, session, runner_id, attempted, baseline)
        if errors
        else {
            "status": "NOT_APPLICABLE",
            "reason": "persistent_assignment_requested" if attempted else "no_mutation",
        }
    )
    if errors:
        for result in results:
            if result["action"] == "APPLIED":
                result["action"] = "ROLLED_BACK" if cleanup["restored"] else "UNCERTAIN"
                result["verified"] = cleanup["restored"]
    return {
        "action": ("PARTIAL" if errors else "APPLIED" if attempted else "NO_OP"),
        "id": runner_id,
        "verified": not errors,
        "mutated": (
            None
            if errors and not cleanup["restored"]
            else False
            if errors
            else bool(attempted)
        ),
        "projects": results,
        "errors": errors,
        "cleanup": cleanup,
    }


def _runner_matches(
    api: Any, session: Any, scope: str, description: str
) -> list[dict[str, Any]]:
    return [
        item
        for item in _fields(
            _pages(api, session, scope), "runner_list", "id", "description"
        )
        if item["description"] == description
    ]


def _unique_runner_id(
    api: Any, session: Any, scope: str, description: str
) -> int | None:
    """Resolve an exact runner description without guessing among duplicates.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param scope: Selected project or group runner-list endpoint.
    :param description: Exact server-visible runner description.
    :returns: Unique runner ID, or ``None`` if no record matches.
    :raises ActionError: If more than one runner has the description.
    """
    matches = _runner_matches(api, session, scope, description)
    if len(matches) > 1:
        raise ActionError("runner_description_ambiguous")
    return _id(matches[0]["id"], "runner") if matches else None


def _record(observed: dict[str, Any]) -> RunnerRecord:
    """Limit a provider runner response to declared, nonsecret fields.

    :param observed: Full runner response from the bound API client.
    :returns: Validated public runner fields for JSON and YAML output.
    :raises ActionError: If a required identity, tag, or project field is invalid.
    """
    identifier = _id(observed.get("id"), "runner")
    runner_type = _required_text(observed.get("runner_type"), "runner_type")
    projects = observed.get("projects")
    if projects is not None and not isinstance(projects, list):
        raise ActionError("runner_projects_invalid_shape")
    project_ids = (
        [
            _id(_object(item, "runner_project", "id")["id"], "runner_project")
            for item in projects
        ]
        if projects is not None
        else None
    )
    if project_ids is not None and len(project_ids) != len(set(project_ids)):
        raise ActionError("runner_projects_duplicate_id")
    description = observed.get("description")
    if description is not None and not isinstance(description, str):
        raise ActionError("runner_description_invalid")
    for field in ("online", "paused", "is_shared"):
        if observed.get(field) is not None and not isinstance(observed[field], bool):
            raise ActionError(f"runner_{field}_invalid")
    for field in (
        "status",
        "access_level",
        "job_execution_status",
        "contacted_at",
        "version",
        "platform",
        "architecture",
    ):
        if observed.get(field) is not None and not isinstance(observed[field], str):
            raise ActionError(f"runner_{field}_invalid")
    return {
        "id": identifier,
        "description": description,
        "runner_type": runner_type,
        "tag_list": _observed_tags(observed.get("tag_list")),
        "status": observed.get("status"),
        "online": observed.get("online"),
        "paused": observed.get("paused"),
        "is_shared": observed.get("is_shared"),
        "access_level": observed.get("access_level"),
        "job_execution_status": observed.get("job_execution_status"),
        "projects": project_ids,
        "contacted_at": observed.get("contacted_at"),
        "version": observed.get("version"),
        "platform": observed.get("platform"),
        "architecture": observed.get("architecture"),
    }


def _matches_filters(record: RunnerRecord, filters: list[str]) -> bool:
    """Match one runner against agent-facing state predicates.

    :param record: Validated public runner fields.
    :param filters: Requested list predicates.
    :returns: Whether every requested predicate matches the live record.
    """
    states = {
        "protected": record["access_level"] == "ref_protected",
        "unprotected": record["access_level"] == "not_protected",
        "online": record["online"] is True,
        "offline": record["online"] is False,
        "shared": record["is_shared"] is True,
        "dedicated": record["is_shared"] is False,
        "paused": record["paused"] is True,
        "active": record["paused"] is False,
    }
    return all(states[selector] for selector in filters)


def read(api: Any, session: Any, plan: ActionPlan, target_id: int) -> RunnerReadResult:
    """Read scoped runners through the same bound session as runner actions.

    :param api: Bound GitLab API client.
    :param session: Authenticated session for the selected target.
    :param plan: Validated get or list request.
    :param target_id: Access-verified project or group ID.
    :returns: Public runner records, truncation, and selected job relation.
    :raises ActionError: If the selected runner is absent, ambiguous, or malformed.
    :raises GitLabAPIError: If a provider read fails.
    """  # noqa: DOC502,DOC503 - provider failures propagate from API calls
    scope = f"{plan.target_kind}s/{target_id}/runners"
    description = plan.body.get("description")
    if plan.operation == "get":
        related_job = None
        if "job_id" in plan.body:
            related_job = _related_job(api, session, target_id, plan.body["job_id"])
            identifier = related_job["runner_id"]
            if identifier is None:
                return RunnerReadResult([], False, related_job)
        else:
            identifier = plan.resource_id or _unique_runner_id(
                api, session, scope, description
            )
        if identifier is None:
            raise ActionError("runner_not_found_in_selected_scope")
        observed = _runner_readback(api, session, scope, identifier, description)
        return RunnerReadResult([_record(observed)], False, related_job)
    if plan.operation != "list":
        raise ActionError("unsupported_runner_read")
    listed = _fields(_pages(api, session, scope), "runner_list", "id", "description")
    selected = [
        item
        for item in listed
        if description is None or item["description"] == description
    ]
    limit = plan.body["limit"]
    records: list[RunnerRecord] = []
    for item in selected:
        identifier = _id(item["id"], "runner")
        observed = _object(
            api.get_json(session, f"runners/{identifier}"),
            "runner_readback",
            "id",
            "description",
        )
        if observed["id"] != identifier:
            raise ActionError("runner_readback_mismatch")
        record = _record(observed)
        if _matches_filters(record, plan.body["filters"]):
            records.append(record)
        if len(records) > limit:
            return RunnerReadResult(records[:limit], True, None)
    return RunnerReadResult(records, False, None)


def _related_job(api: Any, session: Any, project_id: int, job_id: int) -> RelatedJob:
    """Resolve one selected job to its runner without exposing raw job JSON.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param project_id: Access-verified selected project ID.
    :param job_id: Positive job ID observed in a pipeline result.
    :returns: Job state and exact runner relation, including an unassigned job.
    :raises ActionError: If the provider job identity or relation is malformed.
    :raises GitLabAPIError: If the selected job read fails.
    """  # noqa: DOC502,DOC503 - API errors propagate from the shared client
    job = _object(
        api.get_json(session, f"projects/{project_id}/jobs/{job_id}"),
        "runner_job",
        "id",
        "status",
        "runner",
    )
    if _id(job["id"], "job") != job_id:
        raise ActionError("runner_job_id_mismatch")
    status = _required_text(job["status"], "job_status")
    failure = job.get("failure_reason")
    if failure is not None and not isinstance(failure, str):
        raise ActionError("runner_job_failure_reason_invalid")
    pipeline = job.get("pipeline")
    if pipeline is not None and not isinstance(pipeline, dict):
        raise ActionError("runner_job_pipeline_invalid")
    pipeline_id = _id(pipeline["id"], "pipeline") if pipeline else None
    runner = job["runner"]
    if runner is not None and not isinstance(runner, dict):
        raise ActionError("runner_job_runner_invalid")
    runner_id = _id(runner["id"], "runner") if runner else None
    return {
        "id": job_id,
        "status": status,
        "failure_reason": failure,
        "pipeline_id": pipeline_id,
        "runner_id": runner_id,
    }


def _runner_readback(
    api: Any,
    session: Any,
    scope: str,
    runner_id: int,
    description: str | None = None,
    expected_tags: list[str] | None = None,
) -> dict[str, Any]:
    """Read one runner and its selected-scope membership from GitLab.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param scope: Exact project or group runner-list endpoint.
    :param runner_id: Runner ID to verify.
    :param description: Optional expected creation key.
    :param expected_tags: Optional exact tag set for creation.
    :returns: Provider runner record with a validated ``tag_list``.
    :raises ActionError: If identity, scope, or tags do not match.
    """
    observed = _object(
        api.get_json(session, f"runners/{runner_id}"),
        "runner_readback",
        "id",
        "description",
    )
    if observed["id"] != runner_id or (
        description is not None and observed["description"] != description
    ):
        raise ActionError("runner_readback_mismatch")
    scoped = [
        item
        for item in _fields(_pages(api, session, scope), "runner_list", "id")
        if item["id"] == runner_id
    ]
    if len(scoped) != 1:
        raise ActionError("runner_scope_readback_mismatch")
    observed_tags = _observed_tags(observed.get("tag_list"))
    if expected_tags is not None and set(observed_tags) != set(expected_tags):
        raise ActionError("runner_tag_readback_mismatch")
    return observed


def _tag(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
    """Add requested tags to one scoped runner and verify a fresh GET.

    :param api: Bound GitLab API client.
    :param session: Authenticated session for the selected target.
    :param plan: Confirmed tag-addition plan.
    :param target_id: Access-verified project or group ID.
    :returns: Before/after tags and verified action status.
    :raises ActionError: If the runner is outside the selected target or malformed.
    :raises GitLabAPIError: If a pre-write provider read or terminal write fails.
    """
    runner_id = _positive(plan.resource_id, "runner_id")
    scope = f"{plan.target_kind}s/{target_id}/runners"
    requested = _observed_tags(plan.body["tag_list"])
    with runner_update_guard(plan.origin, runner_id):
        current = _runner_readback(api, session, scope, runner_id)
        if current.get("runner_type") != f"{plan.target_kind}_type":
            raise ActionError("runner_scope_type_mismatch")
        before = _observed_tags(current.get("tag_list"))
        observed_tags = before
        reconciled = False
        for attempt in range(MAX_TAG_UPDATE_ATTEMPTS):
            desired = observed_tags + [
                tag for tag in before + requested if tag not in observed_tags
            ]
            if desired == observed_tags:
                return {
                    "action": "NO_OP",
                    "id": runner_id,
                    "verified": True,
                    "mutated": False,
                    "before_tags": before,
                    "after_tags": before,
                    "added_tags": [],
                }
            put_confirmed = False
            try:
                api.put_json(session, f"runners/{runner_id}", {"tag_list": desired})
                put_confirmed = True
            except GitLabAPIError as exc:
                if not uncertain_write(exc):
                    raise
                reconciled = True
            try:
                observed = _runner_readback(api, session, scope, runner_id)
                observed_tags = _observed_tags(observed.get("tag_list"))
            except (ActionError, GitLabAPIError):
                if put_confirmed:
                    return unverified_write(
                        target_id,
                        {
                            "id": runner_id,
                            "before_tags": before,
                            "expected_tags": desired,
                        },
                        "runner_tag_readback_unavailable",
                    )
                return {
                    "action": "UNVERIFIED",
                    "id": runner_id,
                    "verified": False,
                    "mutated": None,
                    "uncertain": True,
                    "before_tags": before,
                    "expected_tags": desired,
                    "errors": [
                        {
                            "project_id": target_id,
                            "reason": "runner_tag_readback_unavailable",
                        }
                    ],
                    "cleanup": {
                        "status": "NOT_PERFORMED",
                        "reason": "post_write_readback_unverified",
                    },
                }
            if set(before + requested).issubset(observed_tags):
                result = {
                    "action": "APPLIED",
                    "id": runner_id,
                    "verified": True,
                    "mutated": True,
                    "before_tags": before,
                    "after_tags": observed_tags,
                    "added_tags": [tag for tag in requested if tag not in before],
                }
                if reconciled or attempt:
                    result["reconciled"] = True
                return result
        return {
            "action": "UNVERIFIED",
            "id": runner_id,
            "verified": False,
            "mutated": None,
            "uncertain": True,
            "before_tags": before,
            "after_tags": observed_tags,
            "expected_tags": desired,
            "errors": [
                {"project_id": target_id, "reason": "runner_tag_readback_mismatch"}
            ],
            "cleanup": {
                "status": "NOT_PERFORMED",
                "reason": "post_write_readback_mismatch",
            },
        }


def _rollback_created_runner(
    api: Any, session: Any, scope: str, runner_id: int, description: str
) -> dict[str, Any]:
    """Delete the newly created runner after confirming its identity.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param scope: Project or group runner-list endpoint.
    :param runner_id: ID returned by runner creation.
    :param description: Description selected for the new runner.
    :returns: Cleanup status and independent absence read-back.
    """
    try:
        observed = _object(
            api.get_json(session, f"runners/{runner_id}"),
            "runner_cleanup_readback",
            "id",
            "description",
        )
        if observed["id"] != runner_id or observed["description"] != description:
            return {"status": "BLOCKED", "reason": "runner_cleanup_identity_mismatch"}
        absence = _delete_and_read_absence(api, session, scope, runner_id)
        if not absence["verified"]:
            return {"status": "BLOCKED", "reason": "runner_cleanup_readback_mismatch"}
        return {"status": "PASS", "deleted_runner_id": runner_id}
    except (ActionError, GitLabAPIError) as exc:
        return {"status": "BLOCKED", "reason": str(exc)}


def _delete_and_read_absence(
    api: Any, session: Any, scope: str, runner_id: int
) -> dict[str, Any]:
    """Delete one record and verify both global and scoped absence.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param scope: Selected project or group runner-list endpoint.
    :param runner_id: Runner ID already verified against that scope.
    :returns: Classified write outcome and independent absence observations.
    :raises GitLabAPIError: If a terminal delete or read fails.
    :raises ActionError: If scoped runner listing is malformed.
    """  # noqa: DOC502,DOC503 - API errors propagate from the shared client
    write_error: str | None = None
    try:
        api.delete_json(session, f"runners/{runner_id}")
    except GitLabAPIError as exc:
        if not uncertain_write(exc):
            raise
        write_error = exc.reason
    try:
        api.get_json(session, f"runners/{runner_id}")
        global_absent = False
    except GitLabAPIError as exc:
        if exc.reason != "provider_404":
            raise
        global_absent = True
    scoped_ids = {
        _id(item["id"], "runner")
        for item in _fields(_pages(api, session, scope), "runner_list", "id")
    }
    scoped_absent = runner_id not in scoped_ids
    return {
        "verified": global_absent and scoped_absent,
        "global_get_status": 404 if global_absent else 200,
        "scoped_absent": scoped_absent,
        "write_error": write_error,
    }


def _delete_observation(
    api: Any, session: Any, plan: ActionPlan, target_id: int
) -> tuple[dict[str, Any], RunnerRecord | None]:
    """Read the exact runner identity and assignments a delete would affect.

    :param api: Bound GitLab API client.
    :param session: Authenticated session for the selected target.
    :param plan: Runner ID and optional expected description.
    :param target_id: Access-verified project or group ID.
    :returns: Confirmable snapshot and public runner state, or verified absence.
    :raises ActionError: If identity, scope, or assignments cannot be verified.
    :raises GitLabAPIError: If a provider read fails for a reason other than 404.
    """
    identifier = _positive(plan.resource_id, "runner_id")
    scope = f"{plan.target_kind}s/{target_id}/runners"
    try:
        observed = _runner_readback(
            api, session, scope, identifier, plan.body.get("description")
        )
    except GitLabAPIError as exc:
        if exc.reason != "provider_404":
            raise
        scoped_ids = {
            _id(item["id"], "runner")
            for item in _fields(_pages(api, session, scope), "runner_list", "id")
        }
        if identifier in scoped_ids:
            raise ActionError("runner_global_scope_disagree") from exc
        return {"id": identifier, "present": False}, None
    record = _record(observed)
    if record["runner_type"] != f"{plan.target_kind}_type":
        raise ActionError("runner_scope_type_mismatch")
    projects = record["projects"]
    if projects is None:
        raise ActionError("runner_projects_unavailable_for_delete_plan")
    return (
        {
            "id": identifier,
            "present": True,
            "runner_type": record["runner_type"],
            "description": record["description"],
            "project_ids": sorted(projects),
        },
        record,
    )


def delete_snapshot(
    api: Any, session: Any, plan: ActionPlan, target_id: int
) -> dict[str, Any]:
    """Bind a live delete plan to the exact runner and project assignments.

    :param api: Bound GitLab API client.
    :param session: Authenticated session for the selected target.
    :param plan: Runner deletion request with a numeric ID.
    :param target_id: Access-verified project or group ID.
    :returns: Snapshot to include in the confirmation digest.
    :raises ActionError: If the selected runner cannot be safely identified.
    :raises GitLabAPIError: If a provider read fails.
    """  # noqa: DOC502,DOC503 - delegated observation propagates these errors
    return _delete_observation(api, session, plan, target_id)[0]


def _delete(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
    """Delete only the runner identified in the confirmed selected scope.

    :param api: Bound GitLab API client.
    :param session: Authenticated session for the selected target.
    :param plan: Confirmed runner deletion plan.
    :param target_id: Access-verified project or group ID.
    :returns: Before state, write outcome, and independent absence read-back.
    :raises ActionError: If the selected runner identity or scope differs.
    :raises GitLabAPIError: If a terminal provider request fails.
    """  # noqa: DOC502,DOC503 - provider failures propagate from the shared client
    if plan.runner_snapshot is None:
        raise ActionError("runner_delete_requires_live_plan")
    scope = f"{plan.target_kind}s/{target_id}/runners"
    identifier = _positive(plan.resource_id, "runner_id")
    with runner_update_guard(plan.origin, identifier):
        current, before = _delete_observation(api, session, plan, target_id)
        if current != plan.runner_snapshot:
            raise ActionError("runner_changed_rerun_live_plan")
        if before is None:
            return {
                "action": "NO_OP",
                "id": identifier,
                "verified": True,
                "mutated": False,
                "before": None,
                "after": {"scoped_absent": True, "global_get_status": 404},
            }
        after = _delete_and_read_absence(api, session, scope, identifier)
        if after["verified"]:
            return {
                "action": "APPLIED",
                "id": identifier,
                "verified": True,
                "mutated": True,
                "before": before,
                "after": after,
            }
        return {
            "action": "UNVERIFIED",
            "id": identifier,
            "verified": False,
            "mutated": None if after["write_error"] else True,
            "before": before,
            "after": after,
            "errors": [
                {"project_id": target_id, "reason": "runner_delete_readback_mismatch"}
            ],
            "cleanup": {
                "status": "NOT_PERFORMED",
                "reason": "runner_delete_outcome_unverified",
            },
        }


def _create(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
    """Create one scoped runner record and save its one-time token.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param plan: Confirmed creation plan.
    :param target_id: Access-verified project or group ID.
    :returns: Verified runner ID, observed tags, and token-sink status.
    :raises ActionError: If the token sink or recovery state is invalid.
    :raises GitLabAPIError: If a terminal provider operation fails.
    """
    if not plan.token_out:
        raise ActionError("token_out_required_for_runner_create_plan")
    scope = f"{plan.target_kind}s/{target_id}/runners"
    destination = Path(plan.token_out)
    description = plan.body["description"]
    try:
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as exc:
        raise ActionError("token_out_unavailable") from exc
    token_saved = False
    runner_id: int | None = None
    try:
        with create_guard(
            plan.origin, plan.target_kind, target_id, "runner", description
        ) as guard:
            matching = _runner_matches(api, session, scope, description)
            if matching:
                raise ActionError(
                    "runner_description_exists_verify_and_remove_record_before_retry"
                )
            guard.reconcile_absent()
            body = dict(plan.body)
            body[f"{plan.target_kind}_id"] = target_id
            guard.mark_pending()
            try:
                created = _object(
                    api.post_json(session, "user/runners", body),
                    "runner_create",
                    "id",
                )
            except GitLabAPIError as exc:
                if not uncertain_write(exc):
                    guard.clear()
                    raise
                return {
                    "action": "PARTIAL",
                    "id": None,
                    "verified": False,
                    "mutated": None,
                    "sink_persisted": False,
                    "errors": [
                        {
                            "project_id": target_id,
                            "reason": "runner_create_outcome_uncertain_reconcile_before_retry",
                        }
                    ],
                    "cleanup": {"status": "BLOCKED", "reason": exc.reason},
                }
            except ActionError:
                return {
                    "action": "PARTIAL",
                    "id": None,
                    "verified": False,
                    "mutated": None,
                    "sink_persisted": False,
                    "errors": [
                        {
                            "project_id": target_id,
                            "reason": "runner_create_malformed_response_reconcile_before_retry",
                        }
                    ],
                    "cleanup": {
                        "status": "BLOCKED",
                        "reason": "runner_create_identity_unresolved",
                    },
                }
            try:
                runner_id = _id(created["id"], "runner")
            except ActionError:
                return {
                    "action": "PARTIAL",
                    "id": None,
                    "verified": False,
                    "mutated": None,
                    "sink_persisted": False,
                    "errors": [
                        {
                            "project_id": target_id,
                            "reason": "runner_create_id_invalid_reconcile_before_retry",
                        }
                    ],
                    "cleanup": {"status": "BLOCKED", "reason": "runner_id_unresolved"},
                }
            token = created.get("token")
            if not isinstance(token, str) or not token or "\n" in token:
                reason = "runner_create_missing_one_time_token"
            else:
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                        descriptor = -1
                        handle.write(token + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    token_saved = (
                        destination.read_text(encoding="utf-8") == token + "\n"
                    )
                    if not token_saved:
                        reason = "runner_token_file_readback_mismatch"
                except (OSError, UnicodeError):
                    reason = "runner_token_file_write_or_readback_failed"
            if not token_saved:
                cleanup = _rollback_created_runner(
                    api, session, scope, runner_id, description
                )
                if cleanup["status"] == "PASS":
                    guard.clear()
                return {
                    "action": "PARTIAL",
                    "id": runner_id,
                    "verified": False,
                    "errors": [{"project_id": target_id, "reason": reason}],
                    "cleanup": cleanup,
                    "sink_persisted": False,
                    "mutated": None if cleanup["status"] != "PASS" else False,
                }
            try:
                expected_tags = (
                    plan.body["tag_list"].split(",")
                    if "tag_list" in plan.body
                    else None
                )
                observed = _runner_readback(
                    api, session, scope, runner_id, description, expected_tags
                )
            except (ActionError, GitLabAPIError) as exc:
                cleanup = _rollback_created_runner(
                    api, session, scope, runner_id, description
                )
                if cleanup["status"] == "PASS":
                    token_saved = False
                    guard.clear()
                return {
                    "action": "PARTIAL",
                    "id": runner_id,
                    "verified": False,
                    "errors": [{"project_id": target_id, "reason": str(exc)}],
                    "cleanup": cleanup,
                    "sink_persisted": token_saved,
                    "mutated": None if cleanup["status"] != "PASS" else False,
                }
            guard.clear()
            return {
                "action": "APPLIED",
                "id": runner_id,
                "verified": True,
                "after_tags": observed["tag_list"],
                "sink_persisted": True,
                "mutated": True,
                "cleanup": {
                    "status": "NOT_APPLICABLE",
                    "reason": "persistent_runner_requested",
                },
            }
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not token_saved:
            destination.unlink(missing_ok=True)


def apply(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
    """Dispatch one confirmed runner action to the existing shared adapter.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param plan: Confirmed runner action plan.
    :param target_id: Access-verified project or group ID.
    :returns: Action record with independent read-back evidence.
    :raises ActionError: If a selected resource or response is invalid.
    :raises GitLabAPIError: If a terminal provider operation fails.
    """  # noqa: DOC502,DOC503 - exceptions propagate from dispatched actions
    if plan.operation == "assign":
        return _assign(api, session, plan, target_id)
    if plan.operation == "tag":
        return _tag(api, session, plan, target_id)
    if plan.operation == "delete":
        return _delete(api, session, plan, target_id)
    if plan.operation != "create":
        raise ActionError("unsupported_runner_action")
    return _create(api, session, plan, target_id)
