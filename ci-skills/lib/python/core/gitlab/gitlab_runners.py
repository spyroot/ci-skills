"""Scoped GitLab runner reads and confirmed record lifecycle actions.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import os
from argparse import Namespace
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Optional, TypedDict

from lib.python.core.gitlab.gitlab_action import GitLabOperation
from lib.python.core.gitlab.gitlab_actions import (
    ActionError,
    ActionPlan,
    PreparedOperation,
    _fields,
    _id,
    _object,
    _pages,
    _positive,
    _required_text,
)
from lib.python.core.gitlab.gitlab_api import (
    CreateGuard,
    GitLabAPIError,
    GlabAPIClient,
    create_guard,
    runner_update_guard,
    uncertain_write,
    unverified_write,
)
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json_object,
    json_strings,
)
from lib.python.core.runner_identity import (
    RepositoryIdentity,
    RepositorySource,
    resolve_name,
    resolve_repository,
)
from lib.python.core.runner_options import (
    PROTECTION_READBACK_MISMATCH,
    parse_protected,
    runner_tags,
    validate_runner_tags,
)
from lib.python.core.target import GitLabOperationTarget

MAX_TAG_UPDATE_ATTEMPTS: Final[int] = 2


class RunnerRecord(TypedDict):
    """Bounded public fields from one verified GitLab runner read.

    .. attribute :: id
        :type: int

        Positive runner ID.

    .. attribute :: description
        :type: Optional[str]

        Provider runner description when set.

    .. attribute :: runner_type
        :type: str

        Provider runner scope type.

    .. attribute :: tag_list
        :type: Sequence[str]

        Job tags assigned to the runner.

    .. attribute :: status
        :type: Optional[str]

        Provider runner status when supplied.

    .. attribute :: online
        :type: Optional[bool]

        Whether the provider reports the runner online.

    .. attribute :: paused
        :type: Optional[bool]

        Whether runner scheduling is paused.

    .. attribute :: is_shared
        :type: Optional[bool]

        Whether the provider marks the runner shared.

    .. attribute :: access_level
        :type: Optional[str]

        Protected or unprotected ref access when supplied.

    .. attribute :: job_execution_status
        :type: Optional[str]

        Current job state when supplied.

    .. attribute :: projects
        :type: Optional[Sequence[int]]

        Linked project IDs when the provider returns them.

    .. attribute :: contacted_at
        :type: Optional[str]

        Last contact timestamp when supplied.

    .. attribute :: version
        :type: Optional[str]

        Runner version when supplied.

    .. attribute :: platform
        :type: Optional[str]

        Runner platform when supplied.

    .. attribute :: architecture
        :type: Optional[str]

        Runner architecture when supplied.
    """

    id: int
    description: Optional[str]
    runner_type: str
    tag_list: Sequence[str]
    status: Optional[str]
    online: Optional[bool]
    paused: Optional[bool]
    is_shared: Optional[bool]
    access_level: Optional[str]
    job_execution_status: Optional[str]
    projects: Optional[Sequence[int]]
    contacted_at: Optional[str]
    version: Optional[str]
    platform: Optional[str]
    architecture: Optional[str]


class RelatedJob(TypedDict):
    """Exact job identity that selected a runner for read-back.

    .. attribute :: id
        :type: int

        Positive GitLab job ID.

    .. attribute :: status
        :type: str

        Observed job status.

    .. attribute :: failure_reason
        :type: Optional[str]

        Provider failure reason when supplied.

    .. attribute :: pipeline_id
        :type: Optional[int]

        Parent pipeline ID when supplied.

    .. attribute :: runner_id
        :type: Optional[int]

        Assigned runner ID, or None while unassigned.
    """

    id: int
    status: str
    failure_reason: Optional[str]
    pipeline_id: Optional[int]
    runner_id: Optional[int]


@dataclass(frozen=True)
class RunnerReadResult:
    """Bounded runner records and their optional source job relation.

    :param records: Selected public runner records from the provider read.
    :param truncated: Whether more matching runners exist beyond the bound.
    :param related_job: Job that led to this runner, when selected by job ID.

    .. attribute :: records
        :type: Sequence[RunnerRecord]

        Selected public runner records from the provider read.

    .. attribute :: truncated
        :type: bool

        Whether more matching runners exist beyond the bound.

    .. attribute :: related_job
        :type: Optional[RelatedJob]

        Job that led to this runner, when selected by job ID.
    """

    records: Sequence[RunnerRecord]
    truncated: bool
    related_job: Optional[RelatedJob]

    def document(self) -> JSONObject:
        """Expose typed runner observations as the provider's JSON callback result.

        :returns: Runner objects, the truncation flag, and the optional source job.
        :raises ActionError: If a supplied runner or job record contains non-JSON
            values instead of the declared public fields.
        :raises RecursionError: If a supplied record contains a container cycle
            or exceeds the JSON validator's nesting depth.
        """  # noqa: DOC502 - _object validates the typed-record serialization boundary.
        return {
            "records": [_object(record, "runner_record") for record in self.records],
            "truncated": self.truncated,
            "related_job": _object(self.related_job, "runner_related_job")
            if self.related_job is not None
            else None,
        }


def prepare(
    args: Namespace, target: GitLabOperationTarget, target_kind: str
) -> PreparedOperation:
    """Validate one runner action before the shared plan is fingerprinted.

    :param args: Parsed command arguments.
    :param target: Selected nonsecret GitLab target.
    :param target_kind: Access scope, ``project`` or ``group``.
    :returns: Named request fields, optional runner ID, and optional one-time token sink.
    :raises ActionError: If the action or its arguments are invalid.
    """  # noqa: DOC502 - input errors propagate from the selected preparation helper
    if args.action != "create" and getattr(args, "protected", None) is not None:
        raise ActionError("protected_only_valid_for_runner_create")
    if args.action in {"get", "list", "delete"}:
        return _prepare_runner_read(args, target_kind)
    try:
        return _prepare_runner_mutation(args, target, target_kind)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc


def _prepare_runner_read(args: Namespace, target_kind: str) -> PreparedOperation:
    """Validate runner lookup, listing, or deletion selectors.

    :param args: Parsed runner read or deletion arguments.
    :param target_kind: Selected project or group scope.
    :returns: Named read selectors and optional existing runner ID.
    :raises ActionError: Selector combinations, filters, or requested limits are invalid.
    """
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
        return PreparedOperation(
            {
                "description": args.description,
                "limit": args.limit,
                "filters": list(filters),
            }
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
        return PreparedOperation({"job_id": _positive(args.job_id, "job_id")})
    if args.action == "delete" and args.runner_id is None:
        raise ActionError("runner_delete_requires_runner_id")
    if args.runner_id is None and not args.description:
        raise ActionError("runner_id_or_description_required")
    identifier = (
        _positive(args.runner_id, "runner_id") if args.runner_id is not None else None
    )
    description = (
        _required_text(args.description, "description") if args.description else None
    )
    return PreparedOperation({"description": description}, resource_id=identifier)


def _prepare_runner_mutation(
    args: Namespace, target: GitLabOperationTarget, target_kind: str
) -> PreparedOperation:
    """Validate runner assignment, additive tags, or creation inputs.

    :param args: Parsed assignment, tagging, or creation arguments.
    :param target: Runtime configuration supplying an optional default runner ID.
    :param target_kind: Selected project or group scope.
    :returns: Named provider fields, optional existing runner ID, and optional token sink.
    :raises ActionError: The mutation verb or its required input combination is invalid.
    """
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
        return PreparedOperation({}, resource_id=identifier)
    if args.action == "tag":
        identifier = _positive(args.runner_id, "runner_id")
        if args.token_out or args.description or args.runner_type:
            raise ActionError("tag_accepts_runner_id_and_tags_only")
        tags = _requested_tags(args.tag)
        if not tags:
            raise ActionError("tag_requires_tag")
        return PreparedOperation({"tag_list": tags}, resource_id=identifier)
    if args.action != "create":
        raise ActionError("unsupported_runner_action")
    repository = (
        resolve_repository(
            (getattr(args, "project", None) or target.gitlab.project)
            if target_kind == "project"
            else None
        )
        if target_kind == "project"
        else RepositoryIdentity(None, RepositorySource.UNKNOWN)
    )
    body: MutableMapping[str, JSONValue] = {
        "runner_type": f"{target_kind}_type",
        "description": (
            _required_text(args.description, "description")
            if args.description is not None
            else resolve_name(
                None,
                repository,
                argument="--description",
                pending=target_kind == "project",
            ).value
            or None
        ),
    }
    protection = parse_protected(getattr(args, "protected", None))
    if protection is not None:
        body["access_level"] = protection
    tags = runner_tags(args.tag, repository)
    if tags:
        body["tag_list"] = ",".join(tags)
    if args.runner_id is not None:
        raise ActionError("runner_id_only_valid_for_assign")
    if not args.token_out:
        raise ActionError("token_out_required_for_runner_create_plan")
    token_out = str(Path(args.token_out).expanduser().absolute())
    return PreparedOperation(body, token_out=token_out)


RUNNER_DESCRIPTION_FIELD: Final = "description"
RUNNER_TAGS_FIELD: Final = "tag_list"
RUNNER_KIND: Final = "gitlab_runner"
RUNNER_CREATE: Final = "create"
PROJECT_KIND: Final = "project"


def pending_runner_identity(plan: ActionPlan) -> bool:
    """Identify numeric project-create defaults awaiting native repository resolution.

    :param plan: Nonmutating prepared provider operation.
    :returns: Whether a numeric project selector leaves description or tags pending.
    """
    return (
        plan.kind == RUNNER_KIND
        and plan.operation == RUNNER_CREATE
        and plan.target_kind == PROJECT_KIND
        and plan.target_reference.isdecimal()
        and (
            plan.body.get(RUNNER_DESCRIPTION_FIELD) is None
            or RUNNER_TAGS_FIELD not in plan.body
        )
    )


def resolve_runner_identity(plan: ActionPlan, access: JSONObject) -> ActionPlan:
    """Resolve exactly pending create defaults from the existing access receipt.

    :param plan: Original operation retaining all explicit provider inputs.
    :param access: Native project identity verified by existing authentication.
    :returns: Plan with only omitted repository defaults resolved.
    :raises ActionError: Verified native identity or derived inputs are inconsistent.
    """
    if not pending_runner_identity(plan):
        return plan
    try:
        repository = RepositoryIdentity.from_access(access, plan.target_reference)
        body = dict(plan.body)
        if body.get(RUNNER_DESCRIPTION_FIELD) is None:
            body[RUNNER_DESCRIPTION_FIELD] = resolve_name(
                None, repository, argument="--description"
            ).value
        if RUNNER_TAGS_FIELD not in body:
            body[RUNNER_TAGS_FIELD] = ",".join(runner_tags((), repository))
        return replace(plan, body=body)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc


def _requested_tags(values: Sequence[str]) -> Sequence[str]:
    """Validate caller tags for both record creation and later additions.

    :param values: Repeated ``--tag`` values from the command parser.
    :returns: Distinct, nonempty tag names in caller order.
    :raises ActionError: If a tag is empty, contains a comma, or is repeated.
    """
    try:
        return list(validate_runner_tags(values))
    except ValueError as exc:
        raise ActionError(str(exc)) from exc


def _observed_tags(value: JSONValue) -> Sequence[str]:
    """Accept only a concrete GitLab tag list for a runner read-back.

    :param value: Provider ``tag_list`` value.
    :returns: Nonempty tag names in provider order.
    :raises ActionError: If GitLab returns an invalid or duplicated tag.
    """
    try:
        tags = list(json_strings(value))
    except TypeError as exc:
        raise ActionError("runner_tag_list_invalid_shape") from exc
    if any(not tag or "," in tag for tag in tags):
        raise ActionError("runner_tag_list_invalid_shape")
    if len(set(tags)) != len(tags):
        raise ActionError("runner_tag_list_duplicate")
    return tags


def project_ids(
    api: GlabAPIClient, session: BoundGitLabSession, group_id: int
) -> tuple[int, ...]:
    """Resolve validated group membership for a live runner plan.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param group_id: Group whose direct and subgroup projects are selected.
    :returns: Sorted project IDs from the live group response.
    :raises ActionError: If membership is empty, duplicated, or malformed.
    :raises GitLabAPIError: A group-project membership page cannot be read.
    """  # noqa: DOC503 - collection reads propagate GitLabAPIError
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


def _direct_projects(
    api: GlabAPIClient, session: BoundGitLabSession, runner_id: int
) -> frozenset[int]:
    """Read direct runner assignments without inherited availability.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param runner_id: Project runner whose assignments are needed.
    :returns: IDs of directly assigned projects.
    :raises ActionError: If runner identity, type, or projects are invalid.
    :raises GitLabAPIError: The exact runner assignment read fails.
    """  # noqa: DOC503 - the exact runner read propagates GitLabAPIError
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
    raw_projects = runner["projects"]
    if not isinstance(raw_projects, list):
        raise ActionError("runner_projects_invalid_shape")
    projects = [
        _id(_object(item, "runner_project", "id")["id"], "runner_project")
        for item in raw_projects
    ]
    if len(projects) != len(set(projects)):
        raise ActionError("runner_projects_duplicate_id")
    return frozenset(projects)


def _assigned(
    api: GlabAPIClient, session: BoundGitLabSession, runner_id: int, project_id: int
) -> bool:
    """Check whether a runner is directly assigned to a project.

    :param api: Authenticated GitLab API client.
    :param session: Bound GitLab session.
    :param runner_id: Runner whose direct assignments are read.
    :param project_id: Exact project ID to test.
    :returns: Whether the project appears among direct runner assignments.
    :raises ActionError: Runner or assignment response is malformed.
    :raises GitLabAPIError: Direct assignment read fails.
    """  # noqa: DOC502 - identity errors propagate from the direct-assignment reader
    return project_id in _direct_projects(api, session, runner_id)


def _membership_snapshot(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    runner_id: int,
    identifiers: tuple[int, ...],
) -> Mapping[int, bool]:
    """Snapshot direct assignments for selected project IDs.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param runner_id: Runner whose assignments are read.
    :param identifiers: Project IDs selected for the action.
    :returns: Each project ID mapped to its current assignment state.
    :raises ActionError: Runner identity, type, or direct assignments are malformed.
    :raises GitLabAPIError: The runner assignment read fails.
    """  # noqa: DOC502 - assignment validation and provider reads propagate failures
    direct = _direct_projects(api, session, runner_id)
    return {project_id: project_id in direct for project_id in identifiers}


def _rollback_assignments(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    runner_id: int,
    attempted: Sequence[int],
    baseline: Mapping[int, bool],
) -> JSONObject:
    """Undo attempted assignments and read their final state.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param runner_id: Runner whose attempted assignments are removed.
    :param attempted: Project IDs changed by this invocation.
    :param baseline: Assignment state before the action.
    :returns: Cleanup status, final restoration flag, and per-project records.
    """
    records: MutableSequence[JSONObject] = []
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


class AttachToProject(GitLabOperation):
    """Attach selected projects and recover the observed baseline on failure."""

    def _selected_projects(self, runner_id: int) -> tuple[int, ...]:
        """Verify the runner and resolve the exact confirmed assignment scope.

        :param runner_id: Positive existing runner ID from the concrete plan.
        :returns: Exact project IDs covered by the current confirmation.
        :raises ActionError: Runner type, live-plan membership, or selected group changed.
        :raises GitLabAPIError: Runner or group membership cannot be read.
        """  # noqa: DOC503 - provider identity and group reads propagate failures
        _direct_projects(self.gitlab.api, self.gitlab.session, runner_id)
        if self.plan.target_kind == "project":
            return (self.gitlab.target_id,)
        identifiers = project_ids(
            self.gitlab.api, self.gitlab.session, self.gitlab.target_id
        )
        if self.plan.project_ids is None:
            raise ActionError("group_assignment_requires_live_plan")
        if identifiers != self.plan.project_ids:
            raise ActionError("group_projects_changed_rerun_live_plan")
        return identifiers

    def _verify_final(self, runner_id: int, identifiers: tuple[int, ...]) -> None:
        """Verify every selected project after the assignment sequence.

        :param runner_id: Existing runner whose assignments were changed.
        :param identifiers: Exact project IDs covered by the confirmed plan.
        :raises ActionError: Any selected membership is absent or malformed.
        :raises GitLabAPIError: The final assignment read fails.
        """  # noqa: DOC503 - the membership reader propagates provider failures
        final = _membership_snapshot(
            self.gitlab.api, self.gitlab.session, runner_id, identifiers
        )
        if not all(final.values()):
            raise ActionError("runner_final_readback_mismatch")

    def run(self) -> JSONObject:
        """Attach missing projects and restore the baseline after any failed verification.

        :returns: Provider record with independent read-back evidence.
        :raises ActionError: If runner identity or project scope is invalid.
        :raises GitLabAPIError: Initial runner or membership reads fail.
        """  # noqa: DOC503 - initial identity and membership reads propagate failures
        api, session = self.gitlab.api, self.gitlab.session
        plan, target_id = self.plan, self.gitlab.target_id
        runner_id = _positive(plan.resource_id, "runner_id")
        identifiers = self._selected_projects(runner_id)
        baseline = _membership_snapshot(api, session, runner_id, identifiers)

        results: MutableSequence[MutableMapping[str, JSONValue]] = []
        errors: MutableSequence[JSONObject] = []
        attempted: MutableSequence[int] = []

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
                self._verify_final(runner_id, identifiers)
            except (ActionError, GitLabAPIError) as exc:
                errors.append({"project_id": target_id, "reason": str(exc)})
        cleanup = (
            _rollback_assignments(api, session, runner_id, attempted, baseline)
            if errors
            else {
                "status": "NOT_APPLICABLE",
                "reason": "persistent_assignment_requested"
                if attempted
                else "no_mutation",
            }
        )
        if errors:
            for result in results:
                if result["action"] == "APPLIED":
                    result["action"] = (
                        "ROLLED_BACK" if cleanup["restored"] else "UNCERTAIN"
                    )
                    result["verified"] = cleanup["restored"] is True
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
    api: GlabAPIClient, session: BoundGitLabSession, scope: str, description: str
) -> Sequence[JSONObject]:
    """Find runners with an exact description in one selected scope.

    :param api: Authenticated GitLab API client.
    :param session: Bound GitLab session.
    :param scope: Project or group runner collection endpoint.
    :param description: Exact runner description to match.
    :returns: Matching validated runner objects.
    :raises ActionError: List pagination or required runner fields are invalid.
    :raises GitLabAPIError: A runner list request fails.
    """  # noqa: DOC502 - page reads and field validation propagate failures
    return [
        item
        for item in _fields(
            _pages(api, session, scope), "runner_list", "id", "description"
        )
        if item["description"] == description
    ]


def _unique_runner_id(
    api: GlabAPIClient, session: BoundGitLabSession, scope: str, description: str
) -> Optional[int]:
    """Resolve an exact runner description without guessing among duplicates.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param scope: Selected project or group runner-list endpoint.
    :param description: Exact server-visible runner description.
    :returns: Unique runner ID, or ``None`` if no record matches.
    :raises ActionError: If more than one runner has the description.
    :raises GitLabAPIError: A runner collection page cannot be read.
    """  # noqa: DOC503 - collection reads propagate provider failures
    matches = _runner_matches(api, session, scope, description)
    if len(matches) > 1:
        raise ActionError("runner_description_ambiguous")
    return _id(matches[0]["id"], "runner") if matches else None


def _optional_text(value: JSONValue, field: str) -> Optional[str]:
    """Narrow a nullable provider text field without normalizing its content.

    :param value: JSON field value returned by GitLab.
    :param field: Runner field name used in invalid-response diagnostics.
    :returns: Original text, or None when the provider field is absent or null.
    :raises ActionError: A present value is not text.
    """
    if value is not None and not isinstance(value, str):
        raise ActionError(f"runner_{field}_invalid")
    return value


def _optional_boolean(value: JSONValue, field: str) -> Optional[bool]:
    """Narrow a nullable provider boolean without treating integers as booleans.

    :param value: JSON field value returned by GitLab.
    :param field: Runner field name used in invalid-response diagnostics.
    :returns: Provider boolean, or None when no value was supplied.
    :raises ActionError: A present value is not a boolean.
    """
    if value is not None and not isinstance(value, bool):
        raise ActionError(f"runner_{field}_invalid")
    return value


def _record(observed: JSONObject) -> RunnerRecord:
    """Limit a provider runner response to declared, nonsecret fields.

    :param observed: Validated JSON object from the bound API client.
    :returns: Typed public runner fields for JSON and YAML output.
    :raises ActionError: Identity, tags, projects, or a nullable field is malformed.
    """
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
    return {
        "id": _id(observed.get("id"), "runner"),
        "description": _optional_text(observed.get("description"), "description"),
        "runner_type": _required_text(observed.get("runner_type"), "runner_type"),
        "tag_list": _observed_tags(observed.get("tag_list")),
        "status": _optional_text(observed.get("status"), "status"),
        "online": _optional_boolean(observed.get("online"), "online"),
        "paused": _optional_boolean(observed.get("paused"), "paused"),
        "is_shared": _optional_boolean(observed.get("is_shared"), "is_shared"),
        "access_level": _optional_text(observed.get("access_level"), "access_level"),
        "job_execution_status": _optional_text(
            observed.get("job_execution_status"), "job_execution_status"
        ),
        "projects": project_ids,
        "contacted_at": _optional_text(observed.get("contacted_at"), "contacted_at"),
        "version": _optional_text(observed.get("version"), "version"),
        "platform": _optional_text(observed.get("platform"), "platform"),
        "architecture": _optional_text(observed.get("architecture"), "architecture"),
    }


def _matches_filters(record: RunnerRecord, filters: Sequence[str]) -> bool:
    """Match one runner against agent-facing state predicates.

    :param record: Validated public runner fields.
    :param filters: Requested list predicates.
    :returns: Whether every requested predicate matches the live record.
    :raises KeyError: A direct caller supplies an unknown predicate name.
    """  # noqa: DOC502 - predicate lookup propagates KeyError
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


class ReadRunner(GitLabOperation):
    """Read scoped GitLab runner records and retain the typed observation."""

    def _get(self, scope: str, description: Optional[str]) -> RunnerReadResult:
        """Resolve one selected runner or the runner associated with a job.

        :param scope: Access-verified project or group runner collection endpoint.
        :param description: Optional exact description used to disambiguate selection.
        :returns: One observed runner, or no runner when a selected job is unassigned.
        :raises ActionError: The runner selector, job, or independent read-back is invalid.
        :raises GitLabAPIError: The selected provider read fails.
        """  # noqa: DOC503 - job and runner reads propagate provider failures
        api, session = self.gitlab.api, self.gitlab.session
        related_job = None
        if "job_id" in self.plan.body:
            related_job = _related_job(
                api,
                session,
                self.gitlab.target_id,
                _id(self.plan.body["job_id"], "job"),
            )
            identifier = related_job["runner_id"]
            if identifier is None:
                return RunnerReadResult([], False, related_job)
        else:
            selected = self.plan.resource_id
            identifier = (
                _id(selected, "runner")
                if selected is not None
                else _unique_runner_id(
                    api, session, scope, _required_text(description, "description")
                )
            )
        if identifier is None:
            raise ActionError("runner_not_found_in_selected_scope")
        observed = _runner_readback(api, session, scope, identifier, description)
        return RunnerReadResult([_record(observed)], False, related_job)

    def _list(self, scope: str, description: Optional[str]) -> RunnerReadResult:
        """Read matching scoped runners up to the caller's declared result bound.

        :param scope: Access-verified project or group runner collection endpoint.
        :param description: Optional exact description filter.
        :returns: Bounded public records and whether another matching record exists.
        :raises ActionError: Filters, limit, listed identity, or read-back is invalid.
        :raises GitLabAPIError: A collection or runner read fails.
        """  # noqa: DOC503 - collection and runner reads propagate provider failures
        api, session = self.gitlab.api, self.gitlab.session
        listed = _fields(
            _pages(api, session, scope), "runner_list", "id", "description"
        )
        limit = _id(self.plan.body.get("limit"), "runner_limit")
        raw_filters = self.plan.body.get("filters", [])
        if not isinstance(raw_filters, list):
            raise ActionError("runner_filters_invalid_shape")
        filters = [_required_text(value, "runner_filter") for value in raw_filters]
        records: MutableSequence[RunnerRecord] = []
        for item in listed:
            if description is not None and item["description"] != description:
                continue
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
            if _matches_filters(record, filters):
                records.append(record)
            if len(records) > limit:
                return RunnerReadResult(list(records[:limit]), True, None)
        return RunnerReadResult(list(records), False, None)

    def run(self) -> JSONObject:
        """Retain a typed observation and return its public mapping to the executor.

        :returns: Runner records, truncation flag, and optional source-job relation.
        :raises ActionError: The read operation, selector, or provider data is invalid.
        :raises GitLabAPIError: The selected provider read fails.
        """  # noqa: DOC503 - the selected read operation propagates provider failures
        plan = self.plan
        scope = f"{plan.target_kind}s/{self.gitlab.target_id}/runners"
        description = plan.body.get("description")
        if description is not None and not isinstance(description, str):
            raise ActionError("runner_description_invalid")
        if plan.operation == "get":
            observation = self._get(scope, description)
        elif plan.operation == "list":
            observation = self._list(scope, description)
        else:
            raise ActionError("unsupported_runner_read")
        self.state.runner_read = observation
        return observation.document()


def read(
    api: GlabAPIClient, session: BoundGitLabSession, plan: ActionPlan, target_id: int
) -> RunnerReadResult:
    """Preserve existing callers through the shared callback path.

    :param api: Existing API client.
    :param session: Bound GitLab session.
    :param plan: Validated read action.
    :param target_id: Verified scope identifier.
    :returns: Scoped runner records and related job evidence.
    :raises ActionError: The read operation, selector, or provider data is invalid.
    :raises GitLabAPIError: A selected provider read fails.
    :raises BaseException: The callback or its cleanup fails after executor cleanup.
    """  # noqa: DOC502 - operation and lifecycle errors propagate from execute
    callback = ReadRunner(action=plan)
    callback.execute(api, session, target_id)
    return callback.state.runner_read


def _related_job(
    api: GlabAPIClient, session: BoundGitLabSession, project_id: int, job_id: int
) -> RelatedJob:
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
    if pipeline is not None and not isinstance(pipeline, Mapping):
        raise ActionError("runner_job_pipeline_invalid")
    pipeline_id = _id(pipeline.get("id"), "pipeline") if pipeline else None
    runner = job["runner"]
    if runner is not None and not isinstance(runner, Mapping):
        raise ActionError("runner_job_runner_invalid")
    runner_id = _id(runner.get("id"), "runner") if runner else None
    return {
        "id": job_id,
        "status": status,
        "failure_reason": failure,
        "pipeline_id": pipeline_id,
        "runner_id": runner_id,
    }


def _runner_readback(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    scope: str,
    runner_id: int,
    description: Optional[str] = None,
    expected_tags: Optional[Sequence[str]] = None,
    expected_protection: Optional[str] = None,
) -> JSONObject:
    """Read one runner and its selected-scope membership from GitLab.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param scope: Exact project or group runner-list endpoint.
    :param runner_id: Runner ID to verify.
    :param description: Optional expected creation key.
    :param expected_tags: Optional exact tag set for creation.
    :param expected_protection: Explicit native protection setting to verify.
    :returns: Provider runner record with a validated ``tag_list``.
    :raises ActionError: If identity, scope, or tags do not match.
    :raises GitLabAPIError: Runner identity or selected-scope membership cannot be read.
    """  # noqa: DOC503 - identity and membership reads propagate provider failures
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
    if (
        expected_protection is not None
        and observed.get("access_level") != expected_protection
    ):
        raise ActionError(PROTECTION_READBACK_MISMATCH)
    return observed


def read_runner_record(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    target_kind: str,
    target_id: int,
    runner_id: int,
    *,
    description: Optional[str] = None,
    expected_tags: Optional[Sequence[str]] = None,
    expected_protection: Optional[str] = None,
) -> RunnerRecord:
    """Read one runner through its selected project or group membership.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param target_kind: Selected ``project`` or ``group`` scope.
    :param target_id: Access-verified numeric project or group ID.
    :param runner_id: Positive runner ID selected by the caller.
    :param description: Optional exact runner description.
    :param expected_tags: Optional exact runner tag set.
    :param expected_protection: Explicit native ref protection to verify, or absence.
    :returns: Typed public runner fields after global and scoped read-back.
    :raises ActionError: The scope, identity, type, description, or tags differ.
    :raises GitLabAPIError: GitLab runner or membership reads fail.
    """  # noqa: DOC503 - The shared native reader propagates GitLabAPIError.
    if target_kind not in {"project", "group"}:
        raise ActionError("runner_target_kind_invalid")
    selected_id = _positive(target_id, f"{target_kind}_id")
    selected_runner = _positive(runner_id, "runner_id")
    scope = f"{target_kind}s/{selected_id}/runners"
    record = _record(
        _runner_readback(
            api,
            session,
            scope,
            selected_runner,
            description,
            expected_tags,
            expected_protection,
        )
    )
    if record["runner_type"] != f"{target_kind}_type":
        raise ActionError("runner_scope_type_mismatch")
    return record


class SetTag(GitLabOperation):
    """Add requested tags while retaining concurrent provider additions."""

    def _write(self, runner_id: int, desired: Sequence[str]) -> bool:
        """Attempt one tag update without retrying an uncertain write blindly.

        :param runner_id: Exact verified runner whose tags will change.
        :param desired: Complete ordered tag list for this one attempt.
        :returns: Whether GitLab confirmed the PUT; False requires independent reconciliation.
        :raises GitLabAPIError: The provider reports a terminal write failure.
        """
        try:
            self.gitlab.api.put_json(
                self.gitlab.session, f"runners/{runner_id}", {"tag_list": desired}
            )
        except GitLabAPIError as exc:
            if not uncertain_write(exc):
                raise
            return False
        return True

    def _unverified(
        self,
        runner_id: int,
        before: Sequence[str],
        expected: Sequence[str],
        reason: str,
        cleanup_reason: str,
        after: Optional[Sequence[str]] = None,
    ) -> JSONObject:
        """Retain an unknown write outcome when independent tag verification fails.

        :param runner_id: Exact runner whose write outcome remains uncertain.
        :param before: Tags observed before the first write.
        :param expected: Complete tags submitted by the latest write attempt.
        :param reason: Stable read-back error code for the report.
        :param cleanup_reason: Reason automatic rollback was not attempted.
        :param after: Optional tags from the last successful independent read.
        :returns: Unverified operation evidence, including after_tags only when observed.
        """
        result: MutableMapping[str, JSONValue] = {
            "action": "UNVERIFIED",
            "id": runner_id,
            "verified": False,
            "mutated": None,
            "uncertain": True,
            "before_tags": before,
            "expected_tags": expected,
            "errors": [{"project_id": self.gitlab.target_id, "reason": reason}],
            "cleanup": {"status": "NOT_PERFORMED", "reason": cleanup_reason},
        }
        if after is not None:
            result["after_tags"] = after
        return result

    def _update_tags(
        self,
        runner_id: int,
        scope: str,
        before: Sequence[str],
        requested: Sequence[str],
    ) -> JSONObject:
        """Perform bounded additive updates and verify every attempted write.

        :param runner_id: Verified runner protected by the caller's resource lock.
        :param scope: Selected project or group runner collection endpoint.
        :param before: Tags from the initial independent read.
        :param requested: Distinct requested additions from the concrete plan.
        :returns: Verified no-op, verified addition, or unverified mutation evidence.
        :raises GitLabAPIError: A terminal tag update fails.
        """  # noqa: DOC502 - terminal writes propagate from _write
        observed_tags = before
        reconciled = False
        for attempt in range(MAX_TAG_UPDATE_ATTEMPTS):
            additions = [
                tag for tag in [*before, *requested] if tag not in observed_tags
            ]
            desired = [*observed_tags, *additions]
            if desired == list(observed_tags):
                return {
                    "action": "NO_OP",
                    "id": runner_id,
                    "verified": True,
                    "mutated": False,
                    "before_tags": before,
                    "after_tags": before,
                    "added_tags": [],
                }
            confirmed = self._write(runner_id, desired)
            reconciled = reconciled or not confirmed
            try:
                observed = _runner_readback(
                    self.gitlab.api, self.gitlab.session, scope, runner_id
                )
                observed_tags = _observed_tags(observed.get("tag_list"))
            except (ActionError, GitLabAPIError):
                if confirmed:
                    return unverified_write(
                        self.gitlab.target_id,
                        {
                            "id": runner_id,
                            "before_tags": before,
                            "expected_tags": desired,
                        },
                        "runner_tag_readback_unavailable",
                    )
                return self._unverified(
                    runner_id,
                    before,
                    desired,
                    "runner_tag_readback_unavailable",
                    "post_write_readback_unverified",
                )
            if {*before, *requested}.issubset(observed_tags):
                result: MutableMapping[str, JSONValue] = {
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
        return self._unverified(
            runner_id,
            before,
            desired,
            "runner_tag_readback_mismatch",
            "post_write_readback_mismatch",
            observed_tags,
        )

    def run(self) -> JSONObject:
        """Lock the exact runner, validate its scope, and apply additive tags.

        :returns: Provider result with independent tag read-back evidence.
        :raises ActionError: Identity, scope, or requested tags are invalid.
        :raises GitLabAPIError: The lock, initial read, or a terminal update fails.
        :raises OSError: Resource lock metadata or cleanup cannot be accessed safely.
        """  # noqa: DOC503 - lock acquisition and provider operations propagate failures
        runner_id = _positive(self.plan.resource_id, "runner_id")
        scope = f"{self.plan.target_kind}s/{self.gitlab.target_id}/runners"
        requested = _observed_tags(self.plan.body["tag_list"])
        with runner_update_guard(self.plan.origin, runner_id):
            current = _runner_readback(
                self.gitlab.api, self.gitlab.session, scope, runner_id
            )
            if current.get("runner_type") != f"{self.plan.target_kind}_type":
                raise ActionError("runner_scope_type_mismatch")
            before = _observed_tags(current.get("tag_list"))
            return self._update_tags(runner_id, scope, before, requested)


def _rollback_created_runner(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    scope: str,
    runner_id: int,
    description: str,
) -> JSONObject:
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
    api: GlabAPIClient, session: BoundGitLabSession, scope: str, runner_id: int
) -> JSONObject:
    """Delete one record and verify both global and scoped absence.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param scope: Selected project or group runner-list endpoint.
    :param runner_id: Runner ID already verified against that scope.
    :returns: Classified write outcome and independent absence observations.
    :raises GitLabAPIError: If a terminal delete or read fails.
    :raises ActionError: If scoped runner listing is malformed.
    """  # noqa: DOC502,DOC503 - API errors propagate from the shared client
    write_error: Optional[str] = None
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
    api: GlabAPIClient, session: BoundGitLabSession, plan: ActionPlan, target_id: int
) -> tuple[JSONObject, Optional[RunnerRecord]]:
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
            api,
            session,
            scope,
            identifier,
            _optional_text(plan.body.get("description"), "description"),
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
    api: GlabAPIClient, session: BoundGitLabSession, plan: ActionPlan, target_id: int
) -> JSONObject:
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


class DeleteRunner(GitLabOperation):
    """Delete the confirmed runner and verify global and selected-scope absence."""

    def run(self) -> JSONObject:
        """Recheck the confirmed identity before deleting the selected runner.

        :returns: Provider record with independent read-back evidence.
        :raises ActionError: If the confirmed runner identity changed.
        :raises GitLabAPIError: An identity read or terminal delete fails.
        :raises OSError: Opening, inspecting, or releasing the update lock fails.
        """  # noqa: DOC503 - resource locks and terminal provider operations propagate failures
        api, session = self.gitlab.api, self.gitlab.session
        plan, target_id = self.plan, self.gitlab.target_id
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
                    "before": _object(before, "runner_before"),
                    "after": after,
                }
            return {
                "action": "UNVERIFIED",
                "id": identifier,
                "verified": False,
                "mutated": None if after["write_error"] else True,
                "before": _object(before, "runner_before"),
                "after": after,
                "errors": [
                    {
                        "project_id": target_id,
                        "reason": "runner_delete_readback_mismatch",
                    }
                ],
                "cleanup": {
                    "status": "NOT_PERFORMED",
                    "reason": "runner_delete_outcome_unverified",
                },
            }


class RunnerTokenSink:
    """Own the exclusive file used to deliver one runner registration token.

    The path comes from the caller's confirmed token_out plan field. Token text
    is passed to persist and never retained in this object's attributes.

    :ivar destination: Caller-selected path reserved exclusively by the constructor.
    :ivar saved: Whether independent file read-back confirmed a token to retain.
    :ivar descriptor: Owned open file descriptor, or -1 after ownership is released.
    """

    def __init__(self, destination: Path):
        """Reserve a private file without replacing an existing token.

        :param destination: Caller-selected path for the one-time token.
        :raises ActionError: The path already exists or cannot be opened privately.
        """
        self.destination = destination
        self.saved = False
        try:
            self.descriptor = os.open(
                destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except OSError as exc:
            raise ActionError("token_out_unavailable") from exc

    def persist(self, token: JSONValue) -> None:
        """Write a valid token and independently verify its file contents.

        :param token: One-time token value from the newly created runner response.
        :raises ActionError: The response lacks a usable token, file I/O fails,
            or independent file read-back differs from the written value.
        """
        if not isinstance(token, str) or not token or "\n" in token:
            raise ActionError("runner_create_missing_one_time_token")
        try:
            with os.fdopen(self.descriptor, "w", encoding="utf-8") as handle:
                self.descriptor = -1
                handle.write(token + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self.saved = self.destination.read_text(encoding="utf-8") == token + "\n"
        except (OSError, UnicodeError) as exc:
            raise ActionError("runner_token_file_write_or_readback_failed") from exc
        if not self.saved:
            raise ActionError("runner_token_file_readback_mismatch")

    def close(self) -> None:
        """Close the descriptor and remove a token that must not be retained.

        :raises OSError: Closing the file or removing an unretained sink fails.
        """  # noqa: DOC502 - descriptor close and path unlink propagate OSError
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1
        if not self.saved:
            self.destination.unlink(missing_ok=True)


class CreateRunner(GitLabOperation):
    """Create a runner, persist its token, and recover only the owned record."""

    @property
    def scope(self) -> str:
        """Read the selected project or group runner collection endpoint.

        :returns: Endpoint derived from the plan kind and access-verified target ID.
        """
        return f"{self.plan.target_kind}s/{self.gitlab.target_id}/runners"

    @property
    def description(self) -> str:
        """Read the creation identity already fingerprinted in the action.

        :returns: Required nonblank runner description from the plan.
        :raises ActionError: The concrete plan lacks a valid description.
        """  # noqa: DOC502 - required text validation propagates ActionError
        return _required_text(self.plan.body.get("description"), "description")

    def _unknown(self, reason: str, cleanup_reason: str) -> JSONObject:
        """Report a create whose returned identity could not be established.

        :param reason: Stable operation failure code for the report.
        :param cleanup_reason: Why deleting an unverified identity is unsafe.
        :returns: Partial result retaining unknown mutation and absent token state.
        """
        return {
            "action": "PARTIAL",
            "id": None,
            "verified": False,
            "mutated": None,
            "sink_persisted": False,
            "errors": [{"project_id": self.gitlab.target_id, "reason": reason}],
            "cleanup": {"status": "BLOCKED", "reason": cleanup_reason},
        }

    def _recover(
        self, sink: RunnerTokenSink, guard: CreateGuard, runner_id: int, reason: str
    ) -> JSONObject:
        """Roll back only the verified newly created runner after delivery failure.

        :param sink: Owned token file whose retention follows rollback evidence.
        :param guard: Persistent marker held for this exact creation identity.
        :param runner_id: Positive ID read from this invocation's create response.
        :param reason: Token or provider read-back failure that requires recovery.
        :returns: Partial result with independent cleanup and token-retention evidence.
        :raises OSError: Clearing the reconciled persistent create marker fails.
        """  # noqa: DOC502 - persistent marker cleanup propagates OSError
        cleanup = _rollback_created_runner(
            self.gitlab.api,
            self.gitlab.session,
            self.scope,
            runner_id,
            self.description,
        )
        restored = cleanup["status"] == "PASS"
        if restored:
            sink.saved = False
            guard.clear()
        return {
            "action": "PARTIAL",
            "id": runner_id,
            "verified": False,
            "errors": [{"project_id": self.gitlab.target_id, "reason": reason}],
            "cleanup": cleanup,
            "sink_persisted": sink.saved,
            "mutated": False if restored else None,
        }

    def _finish_created(
        self,
        sink: RunnerTokenSink,
        guard: CreateGuard,
        runner_id: int,
        created: JSONObject,
    ) -> JSONObject:
        """Deliver the token, then independently verify the created runner.

        :param sink: Exclusive token file owned by this invocation.
        :param guard: Create marker cleared only after verified success or rollback.
        :param runner_id: Positive ID returned for this new runner.
        :param created: Validated create response containing the one-time token.
        :returns: Verified creation or a partial result with attempted owned recovery.
        :raises OSError: Clearing the persistent create marker fails.
        """  # noqa: DOC502 - persistent marker cleanup propagates OSError
        try:
            sink.persist(created.get("token"))
        except ActionError as exc:
            return self._recover(sink, guard, runner_id, str(exc))
        try:
            expected_tags = (
                _required_text(self.plan.body["tag_list"], "tag_list").split(",")
                if "tag_list" in self.plan.body
                else None
            )
            observed = _runner_readback(
                self.gitlab.api,
                self.gitlab.session,
                self.scope,
                runner_id,
                self.description,
                expected_tags,
                _required_text(self.plan.body["access_level"], "access_level")
                if "access_level" in self.plan.body
                else None,
            )
        except (ActionError, GitLabAPIError) as exc:
            return self._recover(sink, guard, runner_id, str(exc))
        guard.clear()
        return {
            "action": "APPLIED",
            "id": runner_id,
            "verified": True,
            "after_tags": observed["tag_list"],
            "access_level": observed.get("access_level"),
            "sink_persisted": True,
            "mutated": True,
            "cleanup": {
                "status": "NOT_APPLICABLE",
                "reason": "persistent_runner_requested",
            },
        }

    def _create_locked(self, sink: RunnerTokenSink, guard: CreateGuard) -> JSONObject:
        """Dispatch one create while holding its exact-identity recovery marker.

        :param sink: Exclusive token destination established before any provider write.
        :param guard: Locked create marker for the selected description and target.
        :returns: Verified creation or partial evidence for an uncertain response.
        :raises ActionError: The description already exists in the selected scope.
        :raises GitLabAPIError: A prior create is unreconciled or a terminal request fails.
        :raises OSError: Persisting or clearing the create marker fails.
        """  # noqa: DOC503 - persistent marker I/O propagates OSError
        api, session = self.gitlab.api, self.gitlab.session
        if _runner_matches(api, session, self.scope, self.description):
            raise ActionError(
                "runner_description_exists_verify_and_remove_record_before_retry"
            )
        guard.reconcile_absent()
        body = copy_json_object(self.plan.body)
        body[f"{self.plan.target_kind}_id"] = self.gitlab.target_id
        guard.mark_pending()
        try:
            created = _object(
                api.post_json(session, "user/runners", body), "runner_create", "id"
            )
        except GitLabAPIError as exc:
            if not uncertain_write(exc):
                guard.clear()
                raise
            return self._unknown(
                "runner_create_outcome_uncertain_reconcile_before_retry", exc.reason
            )
        except ActionError:
            return self._unknown(
                "runner_create_malformed_response_reconcile_before_retry",
                "runner_create_identity_unresolved",
            )
        try:
            runner_id = _id(created["id"], "runner")
        except ActionError:
            return self._unknown(
                "runner_create_id_invalid_reconcile_before_retry",
                "runner_id_unresolved",
            )
        return self._finish_created(sink, guard, runner_id, created)

    def run(self) -> JSONObject:
        """Own the private token sink throughout creation, read-back, and recovery.

        :returns: Provider result with token persistence and independent cleanup evidence.
        :raises ActionError: Creation inputs or the exclusive token destination are invalid.
        :raises GitLabAPIError: A terminal request or persistent create guard fails.
        :raises OSError: Lock metadata, marker persistence, or final sink cleanup fails.
        """  # noqa: DOC503 - provider operations and owned file cleanup propagate failures
        if not self.plan.token_out:
            raise ActionError("token_out_required_for_runner_create_plan")
        sink = RunnerTokenSink(Path(self.plan.token_out))
        try:
            with create_guard(
                self.plan.origin,
                self.plan.target_kind,
                self.gitlab.target_id,
                "runner",
                self.description,
            ) as guard:
                return self._create_locked(sink, guard)
        finally:
            sink.close()


def apply(
    api: GlabAPIClient, session: BoundGitLabSession, plan: ActionPlan, target_id: int
) -> JSONObject:
    """Dispatch one confirmed runner action to the existing shared adapter.

    :param api: Bound GitLab API client.
    :param session: Authenticated GitLab session.
    :param plan: Confirmed runner action plan.
    :param target_id: Access-verified project or group ID.
    :returns: Action record with independent read-back evidence.
    :raises ActionError: If a selected resource or response is invalid.
    :raises GitLabAPIError: If a terminal provider operation fails.
    :raises OSError: A mutation lock, marker, or token sink cannot be accessed.
    :raises BaseException: An action or lifecycle cleanup fails after finalization.
    """  # noqa: DOC502,DOC503 - exceptions propagate from dispatched actions
    if plan.operation == "assign":
        return AttachToProject(action=plan).execute(api, session, target_id)
    if plan.operation == "tag":
        return SetTag(action=plan).execute(api, session, target_id)
    if plan.operation == "delete":
        return DeleteRunner(action=plan).execute(api, session, target_id)
    if plan.operation != "create":
        raise ActionError("unsupported_runner_action")
    return CreateRunner(action=plan).execute(api, session, target_id)
