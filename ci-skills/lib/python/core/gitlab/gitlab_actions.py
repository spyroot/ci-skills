"""Plan, apply, and independently verify bounded GitLab changes.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
import time
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Optional, Union

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.cli import output_mode, parser
from lib.python.core.gitlab.gitlab_api import GlabAPIClient
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
from lib.python.core.json_types import JSONObject, JSONValue, copy_json, is_json_value
from lib.python.core.logging import log_event
from lib.python.core.portable import write_portable_receipt
from lib.python.core.report import emit, plan_digest
from lib.python.core.runner_options import PROTECTION_VALUES, SingleValue
from lib.python.core.runtime import sanitize
from lib.python.core.target import GitLabOperationTarget
from lib.python.sys.status import BLOCKED, PASS, exit_code


class ActionError(ValueError):
    """An operation cannot be planned or independently verified."""


@dataclass(frozen=True)
class PreparedOperation:
    """Retain validated provider inputs before binding the target into an action.

    :param body: Read-only provider request fields validated by the operation parser.
    :param resource_id: Existing resource selector, absent for creation and list reads.
    :param token_out: Validated one-time token destination for runner creation;
        absent for operations that do not produce a registration credential.

    .. attribute :: body
        :type: JSONObject

        Provider request fields whose meaning is fixed by the selected operation.

    .. attribute :: resource_id
        :type: Optional[Union[int, str]]

        Existing numeric ID or wiki slug, absent when no existing resource is selected.

    .. attribute :: token_out
        :type: Optional[str]

        Runner creation's caller-selected token path, absent for all other operations.
    """

    body: JSONObject
    resource_id: Optional[Union[int, str]] = None
    token_out: Optional[str] = None


@dataclass(frozen=True)
class ActionPlan(SkillAction):
    """Fingerprint one selected GitLab operation before any provider write.

    :param kind: GitLab result kind for the operation.
    :param operation: Requested read or mutation verb.
    :param origin: Bound GitLab HTTPS origin.
    :param target_kind: Selected project or group category.
    :param target_reference: Caller-selected target path or numeric reference.
    :param target_file: Target TOML path used for selection.
    :param target_source: Resolution source for that target.
    :param body: Operation fields bound into the plan digest.
    :param resource_id: Optional existing resource ID selected for an action.
    :param token_out: Optional one-time sink for a created runner token.
    :param revision: Optional exact source revision bound to the action.
    :param project_ids: Optional project IDs selected for runner assignment.
    :param runner_snapshot: Optional before-state bound to runner deletion.

    .. attribute :: kind
        :type: str

        GitLab result kind for the operation.

    .. attribute :: operation
        :type: str

        Requested read or mutation verb.

    .. attribute :: origin
        :type: str

        Bound GitLab HTTPS origin.

    .. attribute :: target_kind
        :type: str

        Selected project or group category.

    .. attribute :: target_reference
        :type: str

        Caller-selected target path or numeric reference.

    .. attribute :: target_file
        :type: str

        Target TOML path used for selection.

    .. attribute :: target_source
        :type: str

        Resolution source for that target.

    .. attribute :: body
        :type: JSONObject

        Operation fields bound into the plan digest.

    .. attribute :: resource_id
        :type: Optional[Union[int, str]]

        Optional existing resource ID selected for an action.

    .. attribute :: token_out
        :type: Optional[str]

        Optional one-time sink for a created runner token.

    .. attribute :: revision
        :type: Optional[str]

        Optional exact source revision bound to the action.

    .. attribute :: project_ids
        :type: Optional[tuple[int, ...]]

        Optional project IDs selected for runner assignment.

    .. attribute :: runner_snapshot
        :type: Optional[JSONObject]

        Optional before-state bound to runner deletion.
    """

    kind: str
    operation: str
    origin: str
    target_kind: str
    target_reference: str
    target_file: str
    target_source: str
    body: JSONObject
    resource_id: Optional[Union[int, str]] = None
    token_out: Optional[str] = None
    revision: Optional[str] = None
    project_ids: Optional[tuple[int, ...]] = None
    runner_snapshot: Optional[JSONObject] = None

    @property
    def digest(self) -> str:
        """Fingerprint the exact selected target and proposed operation.

        :returns: SHA-256 plan digest; runner deletion includes its snapshot.
        :raises TypeError: If a supplied plan value cannot be represented as JSON.
        :raises ValueError: If canonical JSON encoding rejects the plan's structure.
        :raises RecursionError: If supplied containers are cyclic or excessively nested.
        """  # noqa: DOC502 - Shared canonical plan serialization propagates failures.
        data = {
            "schema_version": "1.0",
            "kind": self.kind,
            "operation": self.operation,
            "origin": self.origin,
            "target_kind": self.target_kind,
            "target_reference": self.target_reference,
            "target_file": self.target_file,
            "target_source": self.target_source,
            "body": self.body,
            "resource_id": self.resource_id,
            "token_out": self.token_out,
            "revision": self.revision,
            "project_ids": self.project_ids,
        }
        if self.kind == "gitlab_runner" and self.operation == "delete":
            data["runner_snapshot"] = self.runner_snapshot
        return plan_digest(data)

    def verified_target_id(
        self, session: BoundGitLabSession, access: JSONObject
    ) -> int:
        """Resolve the numeric target while preserving existing binding checks.

        :param session: Bound GitLab identity.
        :param access: Existing access-check result.
        :returns: Verified numeric project or group ID.
        :raises ActionError: If access or the selected target does not match.
        """
        if access.get("status") != PASS:
            raise ActionError("gitlab_access_not_pass")
        target = _object(access.get("target"), "access_target", "kind", "id")
        if target["kind"] != self.target_kind:
            raise ActionError("access_target_kind_mismatch")
        target_id = _id(target["id"], "target")
        if (
            session.origin != self.origin
            or session.target_reference != self.target_reference
        ):
            raise ActionError("bound_target_mismatch")
        return target_id

    def public(self) -> JSONObject:
        """Describe the plan without publishing issue/wiki text or a token.

        :returns: Safe plan metadata, including nonsecret runner tags.
        :raises ActionError: A runner plan contains tags with an invalid shape.
        :raises TypeError: If the body or plan contains a value unsupported by JSON.
        :raises ValueError: If canonical JSON encoding rejects the body or plan.
        :raises RecursionError: If supplied containers are cyclic or excessively nested.
        """  # noqa: DOC503 - Shared canonical plan serialization propagates failures.
        result: MutableMapping[str, JSONValue] = {
            "operation": self.operation,
            "target_kind": self.target_kind,
            "target_reference": self.target_reference,
            "resource_id": self.resource_id,
            "project_ids": list(self.project_ids)
            if self.project_ids is not None
            else None,
            "confirmation_ready": not (
                self.kind == "gitlab_runner"
                and (
                    (
                        self.operation == "assign"
                        and self.target_kind == "group"
                        and self.project_ids is None
                    )
                    or (self.operation == "delete" and self.runner_snapshot is None)
                )
            ),
            "fields": sorted(self.body),
            "body_sha256": plan_digest(self.body),
            "plan_digest": self.digest,
            "one_time_sink_required": self.operation == "create"
            and self.kind == "gitlab_runner",
        }
        if self.kind == "gitlab_runner" and "tag_list" in self.body:
            tags = self.body["tag_list"]
            if isinstance(tags, str):
                result["requested_tags"] = tags.split(",")
            elif isinstance(tags, Sequence):
                result["requested_tags"] = list(tags)
            else:
                raise ActionError("runner_tags_invalid_shape")
        if self.kind == "gitlab_runner" and self.operation == "delete":
            result["runner_snapshot"] = self.runner_snapshot
        return result


def _positive(value: object, name: str) -> int:
    """Convert an action argument to a positive integer.

    :param value: Supplied numeric value; booleans are rejected.
    :param name: Field name included in an invalid-input reason.
    :returns: Positive integer.
    :raises ActionError: The value is not convertible or is nonpositive.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ActionError(f"{name}_must_be_positive_integer")
    try:
        number = int(value)
    except (ValueError, OverflowError) as exc:
        raise ActionError(f"{name}_must_be_positive_integer") from exc
    if number <= 0:
        raise ActionError(f"{name}_must_be_positive_integer")
    return number


def _date(value: Optional[str], name: str) -> Optional[str]:
    """Validate an optional calendar date in exact ISO form.

    :param value: ``YYYY-MM-DD`` text, or ``None``.
    :param name: Field name included in an invalid-input reason.
    :returns: Original valid date text or ``None``.
    :raises ActionError: Format or calendar day is invalid.
    """
    if value is None:
        return None

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ActionError(f"{name}_must_be_yyyy_mm_dd")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ActionError(f"{name}_invalid") from exc
    return value


def _required_text(value: object, name: str) -> str:
    """Read a required nonblank action field.

    :param value: Candidate text field.
    :param name: Field name included in a missing-field reason.
    :returns: Text with surrounding whitespace removed.
    :raises ActionError: The field is absent, nontext, or blank.
    """
    if not isinstance(value, str) or not value.strip():
        raise ActionError(f"{name}_required")
    return value.strip()


def _file_text(value: Optional[str], name: str) -> Optional[str]:
    """Read optional UTF-8 action content from a bounded file.

    :param value: File path, or ``None`` when no file was requested.
    :param name: Field name included in file diagnostics.
    :returns: File text, or ``None`` when absent.
    :raises ActionError: The file exceeds 1 MiB, is unreadable, or is not UTF-8.
    """
    if value is None:
        return None
    path = Path(value).expanduser()
    try:
        if path.stat().st_size > 1_048_576:
            raise ActionError(f"{name}_too_large")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ActionError(f"{name}_unreadable") from exc


def _select_target(
    target: GitLabOperationTarget, args: argparse.Namespace, kind: str
) -> tuple[str, str, str]:
    """Choose one project or group for a GitLab action.

    :param target: GitLab target with optional project and group defaults.
    :param args: Parsed action, explicit selectors, and runner type.
    :param kind: Registered action kind governing allowed target scope.
    :returns: Target kind, selected reference, and selector provenance.
    :raises ActionError: Selectors conflict, required scope is missing, or runner
        type does not match the selected scope.
    """
    project = getattr(args, "project", None)
    group = getattr(args, "group", None)
    if project and group:
        raise ActionError("project_and_group_are_mutually_exclusive")
    operation = args.action
    if kind in {
        "gitlab_issue",
        "gitlab_wiki",
        "gitlab_repository",
        "gitlab_merge_request",
    }:
        if group:
            raise ActionError("operation_requires_project")
        selected = project or target.gitlab.project
        if not selected:
            raise ActionError("project_target_required")
        return (
            "project",
            selected,
            "argv:--project" if project else "target:gitlab.project",
        )
    if kind == "gitlab_runner" and operation == "create":
        runner_type = _required_text(args.runner_type, "runner_type")
        if runner_type not in {"project", "group"}:
            raise ActionError("runner_type_must_be_project_or_group")
        if runner_type == "project":
            if group:
                raise ActionError("runner_type_project_requires_project")
            selected = project or target.gitlab.project
            if not selected:
                raise ActionError("project_target_required")
            return (
                "project",
                selected,
                "argv:--project" if project else "target:gitlab.project",
            )
        if project:
            raise ActionError("runner_type_group_requires_group")
        selected = group or target.gitlab.group
        if not selected:
            raise ActionError("group_target_required")
        return "group", selected, "argv:--group" if group else "target:gitlab.group"
    if project:
        return "project", project, "argv:--project"
    if group:
        return "group", group, "argv:--group"
    if target.gitlab.project and target.gitlab.group:
        raise ActionError("ambiguous_target_select_project_or_group")
    if target.gitlab.project:
        return "project", target.gitlab.project, "target:gitlab.project"
    if target.gitlab.group:
        return "group", target.gitlab.group, "target:gitlab.group"
    raise ActionError("project_or_group_target_required")


def make_plan(
    kind: str,
    args: argparse.Namespace,
    target: GitLabOperationTarget,
    target_source: str,
) -> ActionPlan:
    """Validate one action and fingerprint its target and input body.

    :param kind: Registered GitLab action command.
    :param args: Parsed command arguments.
    :param target: Selected GitLab target configuration.
    :param target_source: Location that selected the target.
    :returns: Confirmable plan bound to the exact requested operation.
    :raises ActionError: If the action or selected target is invalid.
    """
    target_kind, reference, selector = _select_target(target, args, kind)
    operation = "open-bug" if args.action == "create-bug" else args.action
    if kind == "gitlab_milestone":
        from lib.python.core.gitlab.gitlab_milestones import prepare

        prepared = prepare(args)
    elif kind == "gitlab_issue":
        from lib.python.core.gitlab.gitlab_issues import prepare

        prepared = prepare(args)
    elif kind == "gitlab_wiki":
        from lib.python.core.gitlab.gitlab_wikis import prepare

        prepared = prepare(args)
    elif kind == "gitlab_repository":
        from lib.python.core.gitlab.gitlab_repository import prepare

        prepared = prepare(args)
    elif kind == "gitlab_merge_request":
        from lib.python.core.gitlab.gitlab_merge_requests import prepare

        prepared = prepare(args)
    elif kind == "gitlab_runner":
        from lib.python.core.gitlab.gitlab_runners import prepare as prepare_runner

        prepared = prepare_runner(args, target, target_kind)
    else:
        raise ActionError("unsupported_gitlab_action_kind")
    return ActionPlan(
        kind=kind,
        operation=operation,
        origin=target.gitlab.url,
        target_kind=target_kind,
        target_reference=reference,
        target_file=str(target.source_file),
        target_source=f"{target_source};{selector}",
        body=prepared.body,
        resource_id=prepared.resource_id,
        token_out=prepared.token_out,
        revision=args.revision,
    )


def _object(payload: object, name: str, *required: str) -> JSONObject:
    """Require a provider object with selected fields present.

    :param payload: Decoded provider value.
    :param name: Response name used in a shape error.
    :param *required: Field names that must exist in the object.
    :returns: Read-only string-keyed mapping containing finite JSON values.
    :raises ActionError: The value is not a JSON object or lacks a required field.
    :raises RecursionError: If untrusted containers are cyclic or excessively nested.
    """  # noqa: DOC503 - Recursive JSON validation propagates excessive nesting.
    if (
        not is_json_value(payload)
        or not isinstance(payload, Mapping)
        or any(field not in payload for field in required)
    ):
        raise ActionError(f"{name}_invalid_shape")
    return payload


def _array(payload: object, name: str) -> Sequence[JSONObject]:
    """Require a provider JSON array containing only objects.

    :param payload: Decoded provider value.
    :param name: Response name used in a shape error.
    :returns: Read-only sequence of validated provider JSON objects.
    :raises ActionError: If the value is not a nontext, non-byte sequence or
        contains an item outside the finite JSON object model.
    :raises RecursionError: If an item contains cyclic or excessively nested containers.
    """  # noqa: DOC503 - Nested object validation propagates excessive nesting.
    if not isinstance(payload, Sequence) or isinstance(
        payload, (str, bytes, bytearray)
    ):
        raise ActionError(f"{name}_invalid_shape")
    return [_object(item, name) for item in payload]


def _pages(
    api: GlabAPIClient, session: BoundGitLabSession, endpoint: str
) -> Sequence[JSONObject]:
    """Read a complete bounded GitLab collection for duplicate detection.

    :param api: Authenticated GitLab API client.
    :param session: Bound host and credential session.
    :param endpoint: Relative collection endpoint, possibly with query filters.
    :returns: Validated objects from at most 20 pages of 100 records each.
    :raises ActionError: A page is malformed or the collection still appears
        incomplete after the page bound.
    :raises GitLabAPIError: A page request fails.
    """  # noqa: DOC503 - get_json propagates GitLabAPIError for failed page reads.
    records: MutableSequence[JSONObject] = []
    separator = "&" if "?" in endpoint else "?"
    for page in range(1, 21):
        value = _array(
            api.get_json(session, f"{endpoint}{separator}per_page=100&page={page}"),
            "list_response",
        )
        records.extend(value)
        if len(value) < 100:
            return records
    raise ActionError("pagination_limit_blocks_duplicate_check")


def _fields(
    records: Sequence[JSONObject], name: str, *required: str
) -> Sequence[JSONObject]:
    """Validate fields used to identify duplicate provider resources.

    :param records: Provider objects from a bounded collection read.
    :param name: Resource name used in validation diagnostics.
    :param *required: Fields required on every record.
    :returns: Same records after required IDs, text, and description checks.
    :raises ActionError: An entry has missing or malformed required fields.
    """
    validated: MutableSequence[JSONObject] = []
    for item in records:
        selected = _object(item, name, *required)
        for field in required:
            if field in {"id", "iid"}:
                _id(selected[field], f"{name}_{field}")
            elif field in {"title", "state", "slug"}:
                _required_text(selected[field], f"{name}_{field}")
            elif (
                field == "description"
                and selected[field] is not None
                and not isinstance(selected[field], str)
            ):
                raise ActionError(f"{name}_description_invalid")
        validated.append(selected)
    return validated


def _id(value: object, name: str) -> int:
    """Require a positive numeric provider ID.

    :param value: Candidate ID; booleans are rejected.
    :param name: Field name used in the missing-ID reason.
    :returns: Positive integer ID.
    :raises ActionError: The value is not a positive integer.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ActionError(f"{name}_missing_id")
    return value


def _same(current: JSONObject, body: JSONObject) -> bool:
    """Compare a read-back resource against fields in a planned body.

    :param current: Resource returned by GitLab after a write or read.
    :param body: Fields expected by the planned action.
    :returns: Whether every expected field matches, including normalized state,
        label set, milestone IDs, and terminal newlines removed by GitLab from
        descriptions. Other whitespace and content must match exactly.
    """
    for field, expected in body.items():
        labels = current.get("labels")
        milestone = current.get("milestone")
        if field == "state_event":
            actual = current.get("state")
            expected = "closed" if expected == "close" else "active"
        elif field == "labels" and isinstance(labels, list):
            if any(not isinstance(label, str) for label in labels):
                return False
            actual_labels = {label for label in labels if isinstance(label, str)}
            if actual_labels != set(str(expected).split(",")):
                return False
            continue
        elif field == "milestone_id" and isinstance(milestone, Mapping):
            actual = milestone.get("id")
        else:
            actual = current.get(field)
        if (
            field == "description"
            and isinstance(actual, str)
            and isinstance(expected, str)
        ):
            actual, expected = (text.rstrip("\r\n") for text in (actual, expected))
        if actual != expected:
            return False
    return True


def apply_plan(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    access: JSONObject,
    plan: ActionPlan,
) -> JSONObject:
    """Apply a plan using its verified target and bound credential.

    :param api: Bound GitLab API client.
    :param session: Authenticated session used for access verification.
    :param access: Live access result containing the numeric target.
    :param plan: Confirmed action plan to dispatch.
    :returns: Provider action record and read-back evidence.
    :raises ActionError: If target access or the action kind is invalid.
    """
    target_id = plan.verified_target_id(session, access)
    if plan.kind == "gitlab_milestone":
        from lib.python.core.gitlab.gitlab_milestones import apply

        return apply(api, session, plan, target_id)
    if plan.kind == "gitlab_issue":
        from lib.python.core.gitlab.gitlab_issues import apply

        return apply(api, session, plan, target_id)
    if plan.kind == "gitlab_wiki":
        from lib.python.core.gitlab.gitlab_wikis import apply

        return apply(api, session, plan, target_id)
    if plan.kind == "gitlab_repository":
        from lib.python.core.gitlab.gitlab_repository import apply

        return apply(api, session, plan, target_id)
    if plan.kind == "gitlab_merge_request":
        from lib.python.core.gitlab.gitlab_merge_requests import apply

        return apply(api, session, plan, target_id)
    if plan.kind == "gitlab_runner":
        from lib.python.core.gitlab.gitlab_runners import apply

        return apply(api, session, plan, target_id)
    raise ActionError("unsupported_gitlab_action_kind")


def action_parser(kind: str) -> argparse.ArgumentParser:
    """Expose the shared plan/apply interface for one GitLab action kind.

    :param kind: Registered GitLab action kind.
    :returns: Parser with universal and kind-specific options.
    :raises KeyError: If the action kind is not registered.
    """  # noqa: DOC502 - registration errors propagate from parser()
    result = parser(
        f"Plan, apply, and verify {kind.replace('_', ' ')} changes.", kind=kind
    )
    choices = {
        "gitlab_milestone": ("create", "update", "adjust-time"),
        "gitlab_issue": ("open-bug", "create-bug"),
        "gitlab_wiki": ("create", "update"),
        "gitlab_repository": ("commit",),
        "gitlab_merge_request": ("create", "update"),
        "gitlab_runner": ("assign", "create", "delete", "get", "list", "tag"),
    }
    result.set_defaults(kind=kind)
    result.add_argument(
        "action", nargs="?", choices=choices[kind], help="operation to plan or apply"
    )
    result.add_argument(
        "--project", metavar="PATH_OR_ID", help="select exact GitLab project"
    )
    if kind in {"gitlab_milestone", "gitlab_runner"}:
        result.add_argument(
            "--group", metavar="PATH_OR_ID", help="select exact GitLab group"
        )
    _resource_arguments(result, kind)
    if kind == "gitlab_runner":
        _runner_arguments(result)
    _execution_arguments(result)
    return result


def _resource_arguments(result: argparse.ArgumentParser, kind: str) -> None:
    """Declare native resource inputs for the selected GitLab operation.

    :param result: Caller-owned parser to extend with resource input options.
    :param kind: Registered operation kind selecting the applicable input fields.
    """
    if kind in {
        "gitlab_milestone",
        "gitlab_issue",
        "gitlab_wiki",
        "gitlab_merge_request",
    }:
        result.add_argument("--title", help="requested title")
    if kind in {"gitlab_milestone", "gitlab_issue"}:
        result.add_argument(
            "--description-file", metavar="PATH", help="UTF-8 description body"
        )
    if kind in {"gitlab_milestone", "gitlab_issue", "gitlab_merge_request"}:
        result.add_argument("--milestone-id", type=int, help="numeric milestone ID")
    if kind == "gitlab_milestone":
        result.add_argument("--start-date", help="start date YYYY-MM-DD")
        result.add_argument("--due-date", help="due date YYYY-MM-DD")
        result.add_argument(
            "--state", choices=("active", "closed"), help="requested milestone state"
        )
    if kind == "gitlab_issue":
        result.add_argument(
            "--label", action="append", default=[], help="label; repeat to add several"
        )
    if kind == "gitlab_wiki":
        result.add_argument("--slug", help="exact wiki slug for update")
        result.add_argument("--content-file", metavar="PATH", help="UTF-8 wiki content")
    if kind == "gitlab_repository":
        result.add_argument("--branch", help="destination branch for the commit")
        result.add_argument(
            "--start-branch", help="source branch when creating a branch"
        )
        result.add_argument("--commit-message", help="commit message")
        result.add_argument(
            "--actions-file",
            metavar="PATH",
            help="JSON array of native GitLab create or update file actions",
        )
    if kind == "gitlab_merge_request":
        result.add_argument("--source-branch", help="source branch for MR creation")
        result.add_argument("--target-branch", help="target branch for MR creation")
        result.add_argument(
            "--mr-iid", type=int, help="project-local MR IID for update"
        )


def _runner_arguments(result: argparse.ArgumentParser) -> None:
    """Declare runner selectors, filters, and creation-only inputs.

    :param result: Caller-owned runner parser to extend with its field options.
    """
    result.add_argument(
        "--live-plan",
        action="store_true",
        help="read group assignment or runner deletion identity for an apply-ready plan",
    )
    result.add_argument(
        "--runner-id",
        type=int,
        help="numeric runner ID; required for assign, tag, and delete",
    )
    result.add_argument(
        "--job-id", type=int, help="get the runner related to one project job ID"
    )
    result.add_argument(
        "--runner-type",
        choices=("project", "group"),
        help="runner scope for create",
    )
    result.add_argument(
        "--description", action=SingleValue, help="server-visible runner recovery key"
    )
    result.add_argument(
        "--protected",
        choices=tuple(PROTECTION_VALUES),
        action=SingleValue,
        help="create: protected or unprotected runner refs; omission uses provider default",
    )
    result.add_argument(
        "--limit",
        type=int,
        default=10,
        help="maximum runner records for list (1-100)",
    )
    result.add_argument(
        "--filter",
        action="append",
        default=[],
        choices=(
            "protected",
            "unprotected",
            "online",
            "offline",
            "shared",
            "dedicated",
            "paused",
            "active",
        ),
        help="list runner state filter; repeat to combine conditions",
    )
    result.add_argument(
        "--tag",
        action="append",
        default=[],
        help="runner tag for create or tag; repeat to add several",
    )
    result.add_argument(
        "--token-out", metavar="PATH", help="create-only one-time token destination"
    )


def _execution_arguments(result: argparse.ArgumentParser) -> None:
    """Declare confirmation, bounded requests, and receipt output options.

    :param result: Caller-owned operation parser to extend with execution settings.
    """
    result.add_argument(
        "--apply", action="store_true", help="perform the planned change"
    )
    result.add_argument(
        "--confirm-plan", metavar="SHA256", help="exact dry-run or live-plan digest"
    )
    result.add_argument(
        "--timeout", type=int, default=25, metavar="SECONDS", help="per-request timeout"
    )
    result.add_argument(
        "--receipt-out", metavar="PATH", help="write one portable live receipt"
    )


def _result(
    plan: ActionPlan, status: str, **fields: JSONValue
) -> MutableMapping[str, JSONValue]:
    """Assemble an action report from a plan and operation fields.

    :param plan: Plan supplying kind, target, revision claim, and digest.
    :param status: Shared status token for the operation.
    :param **fields: Additional report fields; ``records`` and ``errors`` supply
        counted lists when present.
    :returns: Timestamped report with plan metadata, records, errors, counts,
        and supplied operation fields.
    :raises ActionError: Records or errors are not sequences of JSON objects.
    """  # noqa: DOC502 - report-record validation propagates from _array
    records = _array(fields.pop("records", []), "report_records")
    errors = _array(fields.pop("errors", []), "report_errors")
    return {
        "schema_version": "1.0",
        "kind": plan.kind,
        "status": status,
        "captured_at": datetime.now(UTC).isoformat(),
        "execution_host": socket.getfqdn(),
        "tested_revision": plan.revision,
        "origin": plan.origin,
        "target": {"kind": plan.target_kind, "reference": plan.target_reference},
        "target_file": plan.target_file,
        "target_source": plan.target_source,
        "operation": plan.operation,
        "plan_digest": plan.digest,
        "plan": plan.public(),
        "records": records,
        "errors": errors,
        "summary": {"record_count": len(records), "error_count": len(errors)},
        **fields,
    }


def _verified_skill_revision(session: BoundGitLabSession) -> Optional[str]:
    """Read the source revision verified by the bound skill identity.

    :param session: Concrete session created by successful credential binding.
    :returns: Verified full source SHA, or ``None`` when unavailable.
    """
    revision = session.skill.get("revision")
    if not isinstance(revision, Mapping) or revision.get("verified") is not True:
        return None
    value = revision.get("value")
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)
        else None
    )


def _access_failure(access: JSONObject) -> tuple[str, str]:
    """Extract a safe source and reason from a failed access report.

    :param access: GitLab access result containing optional error records.
    :returns: Scoped ``gitlab_access.user`` or ``gitlab_access.target`` reason
        when valid; otherwise the generic access failure pair.
    """
    errors = access.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        source = errors[0].get("source")
        reason = errors[0].get("reason")
        if (
            isinstance(source, str)
            and source in {"user", "target"}
            and isinstance(reason, str)
            and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason)
        ):
            return f"gitlab_access.{source}", reason
    return "gitlab_access", "gitlab_access_not_pass"


def _retain_failure(
    data: MutableMapping[str, JSONValue],
    error: JSONObject,
    cleanup_errors: Sequence[Mapping[str, str]],
) -> None:
    """Append operation and cleanup failures without losing partial write evidence.

    :param data: Caller-owned action report to mark blocked and update in place.
    :param error: Sanitized source and reason for the operation failure.
    :param cleanup_errors: Additional cleanup failures, empty when cleanup succeeded.
    :raises ActionError: Existing errors, cleanup, or summary fields are malformed.
    """
    data["status"] = BLOCKED
    raw_errors = data.get("errors", [])
    if not isinstance(raw_errors, list):
        raise ActionError("failure_errors_invalid_shape")
    errors = [_object(item, "failure_error") for item in raw_errors]
    errors.append(error)
    if cleanup_errors:
        errors.extend(cleanup_errors)
        cleanup = _object(data.get("cleanup", {}), "failure_cleanup")
        data["cleanup"] = {**cleanup, "status": BLOCKED}
    data["errors"] = errors
    summary = dict(_object(data["summary"], "failure_summary"))
    summary["error_count"] = len(errors)
    data["summary"] = summary


def _retain_failure_identity(
    data: MutableMapping[str, JSONValue],
    session: Optional[BoundGitLabSession],
    access: Optional[JSONObject],
) -> None:
    """Retain only authenticated identity evidence available before failure.

    :param data: Caller-owned action report to update in place.
    :param session: Bound session, absent when authentication never completed.
    :param access: Access observation, absent when access checking never completed.
    """
    if session is not None:
        data["tested_revision"] = _verified_skill_revision(session)
        data["credential_source"] = session.credential_source
        data["credential_digest"] = session.credential_digest
        data["skill"] = copy_json(session.skill)
    if access is not None and access.get("status") == PASS:
        data["identity"] = access.get("identity")
        data["verified_target"] = access.get("target")


def _failure_receipt(data: MutableMapping[str, JSONValue], destination: str) -> None:
    """Write a requested receipt and retain failure evidence if delivery fails.

    :param data: Caller-owned blocked report whose errors and count may be updated.
    :param destination: Requested local portable-receipt output path.
    :raises ActionError: Existing report errors or summary fields are malformed.
    """  # noqa: DOC502 - report-shape errors propagate from _retain_failure
    try:
        write_portable_receipt(data, destination)
    except (OSError, RuntimeError, ValueError):
        _retain_failure(
            data, {"source": "receipt_out", "reason": "receipt_unavailable"}, ()
        )


def _emit_failure(
    args: argparse.Namespace,
    data: MutableMapping[str, JSONValue],
    kind: str,
    source: str,
    reason: str,
    started: float,
) -> int:
    """Emit the retained failure using existing output fallback and elapsed logging.

    :param args: Parsed output format, directory, log, and receipt settings.
    :param data: Prepared blocked report, updated if receipt delivery fails.
    :param kind: Registered command kind for the diagnostic log.
    :param source: Stable failure source for the diagnostic log.
    :param reason: Already sanitized reason for human-readable stderr output.
    :param started: Monotonic operation start used to calculate elapsed seconds.
    :returns: Blocked process exit code after report output.
    :raises ActionError: Retained receipt failure evidence is malformed.
    :raises OSError: Final report output or stdout/stderr writes fail.
    :raises RuntimeError: Rendering fails even after the JSON output fallback.
    :raises TypeError: Report values cannot be serialized as supported JSON.
    :raises ValueError: Report shape, output mode, or serialization is invalid.
    """  # noqa: DOC502 - receipt, rendering, and stream errors propagate from their owners
    if args.receipt_out:
        _failure_receipt(data, args.receipt_out)
    mode = output_mode(args)
    try:
        rendered = emit(data, mode, args.output_dir)
    except (OSError, RuntimeError):
        rendered = emit(data, "json")
    try:
        log_event(
            args,
            kind,
            "failure",
            BLOCKED,
            elapsed=time.monotonic() - started,
            error_class=source,
        )
    except OSError:
        pass
    if mode == "human":
        print(f"BLOCKED: {reason}", file=sys.stderr)
    else:
        sys.stdout.write(rendered)
    return exit_code(BLOCKED)


def _planned_failure(
    args: argparse.Namespace,
    kind: str,
    plan: ActionPlan,
    source: str,
    reason: str,
    phase: str,
    mutated: Optional[bool],
    started: float,
    result: MutableMapping[str, JSONValue],
    session: Optional[BoundGitLabSession] = None,
    access: Optional[JSONObject] = None,
    cleanup_errors: Sequence[Mapping[str, str]] = (),
) -> int:
    """Emit a blocked report while preserving plan and known write state.

    :param args: Parsed output and optional receipt settings.
    :param kind: Action command kind for diagnostic logging.
    :param plan: Concrete prepared action retained in the failure report.
    :param source: Failure source recorded in report errors.
    :param reason: Failure reason sanitized before reporting.
    :param phase: Operation phase in which failure occurred.
    :param mutated: Known write state, or None when the write outcome is uncertain.
    :param started: Monotonic operation start for elapsed logging.
    :param result: Partial report to augment; empty before a report was assembled.
    :param session: Bound session, absent when authentication never completed.
    :param access: Access observation, absent when access checking never completed.
    :param cleanup_errors: Cleanup errors appended to the report, empty on success.
    :returns: Blocked process exit code after report and optional receipt output.
    :raises ActionError: Retained error, cleanup, or summary evidence is malformed.
    :raises OSError: Final report or stdout/stderr output cannot be written.
    :raises RuntimeError: Rendering fails even after the JSON output fallback.
    :raises TypeError: A report value cannot be serialized as supported JSON.
    :raises ValueError: Report shape, output mode, or serialization is invalid.
    """  # noqa: DOC502 - report validation and output errors propagate from the helpers
    safe_reason = sanitize(reason, 240)
    data = result or _result(
        plan,
        BLOCKED,
        phase=phase,
        mutated=mutated,
        readback={"verified": False},
        cleanup={
            "status": "BLOCKED" if mutated is None else "NOT_APPLICABLE",
            "reason": "write_outcome_unverified"
            if mutated is None
            else "no_write_attempted",
        },
    )
    _retain_failure(data, {"source": source, "reason": safe_reason}, cleanup_errors)
    _retain_failure_identity(data, session, access)
    return _emit_failure(args, data, kind, source, safe_reason, started)
