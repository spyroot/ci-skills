"""Plan, apply, and independently verify bounded GitLab changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import sys
import time
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .access import check_gitlab_operation_access
from .action import ExecutionContext, Executor, GitlabCallback, emit_result
from .cli import _failure, log_event, output_mode, parser, resolve_gitlab_target
from .credentials import bind_gitlab_session
from .gitlab_api import GitLabAPIError, GlabAPIClient
from .gitlab_session import GitLabService
from .portable import write_portable_receipt
from .report import emit
from .runtime import sanitize
from .status import BLOCKED, DRY_RUN, PARTIAL, PASS, PLANNED, exit_code
from .target import GitLabOperationTarget, TargetError


class ActionError(ValueError):
    """An operation cannot be planned or independently verified."""


@dataclass(frozen=True)
class ActionPlan:
    kind: str
    operation: str
    origin: str
    target_kind: str
    target_reference: str
    target_file: str
    target_source: str
    body: dict[str, Any]
    resource_id: int | str | None = None
    token_out: str | None = None
    revision: str | None = None
    project_ids: tuple[int, ...] | None = None
    runner_snapshot: dict[str, Any] | None = None

    @property
    def digest(self) -> str:
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
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def verified_target_id(self, session: Any, access: dict[str, Any]) -> int:
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

    def public(self) -> dict[str, Any]:
        """Describe the plan without publishing issue/wiki text or a token.

        :returns: Safe plan metadata, including nonsecret runner tags.
        """
        result = {
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
            "body_sha256": hashlib.sha256(
                json.dumps(self.body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "plan_digest": self.digest,
            "one_time_sink_required": self.operation == "create"
            and self.kind == "gitlab_runner",
        }
        if self.kind == "gitlab_runner" and "tag_list" in self.body:
            tags = self.body["tag_list"]
            result["requested_tags"] = (
                tags.split(",") if isinstance(tags, str) else list(tags)
            )
        if self.kind == "gitlab_runner" and self.operation == "delete":
            result["runner_snapshot"] = self.runner_snapshot
        return result


def _positive(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ActionError(f"{name}_must_be_positive_integer") from exc
    if number <= 0 or isinstance(value, bool):
        raise ActionError(f"{name}_must_be_positive_integer")
    return number


def _date(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ActionError(f"{name}_must_be_yyyy_mm_dd")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ActionError(f"{name}_invalid") from exc
    return value


def _required_text(value: str | None, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionError(f"{name}_required")
    return value.strip()


def _file_text(value: str | None, name: str) -> str | None:
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
    project = getattr(args, "project", None)
    group = getattr(args, "group", None)
    if project and group:
        raise ActionError("project_and_group_are_mutually_exclusive")
    operation = args.action
    if kind in {"gitlab_issue", "gitlab_wiki"}:
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
        from .gitlab_milestones import prepare

        body, resource_id, token_out = prepare(args)
    elif kind == "gitlab_issue":
        from .gitlab_issues import prepare

        body, resource_id, token_out = prepare(args)
    elif kind == "gitlab_wiki":
        from .gitlab_wikis import prepare

        body, resource_id, token_out = prepare(args)
    elif kind == "gitlab_runner":
        from .gitlab_runners import prepare

        body, resource_id, token_out = prepare(args, target, target_kind)
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
        body=body,
        resource_id=resource_id,
        token_out=token_out,
        revision=args.revision,
    )


def _object(payload: Any, name: str, *required: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or any(field not in payload for field in required):
        raise ActionError(f"{name}_invalid_shape")
    return payload


def _array(payload: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or any(
        not isinstance(item, dict) for item in payload
    ):
        raise ActionError(f"{name}_invalid_shape")
    return payload


def _pages(api: Any, session: Any, endpoint: str) -> list[dict[str, Any]]:
    """Search a bounded complete result set or refuse an unsafe duplicate guess."""
    records: list[dict[str, Any]] = []
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
    records: list[dict[str, Any]], name: str, *required: str
) -> list[dict[str, Any]]:
    """Reject malformed list entries before treating them as absent matches."""
    validated: list[dict[str, Any]] = []
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


def _id(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ActionError(f"{name}_missing_id")
    return value


def _same(current: dict[str, Any], body: dict[str, Any]) -> bool:
    for field, expected in body.items():
        if field == "state_event":
            actual = current.get("state")
            expected = "closed" if expected == "close" else "active"
        elif field == "labels" and isinstance(current.get("labels"), list):
            actual = set(current["labels"])
            expected = set(str(expected).split(","))
        elif field == "milestone_id" and isinstance(current.get("milestone"), dict):
            actual = current["milestone"].get("id")
        else:
            actual = current.get(field)
        if actual != expected:
            return False
    return True


def apply_plan(
    api: Any, session: Any, access: dict[str, Any], plan: ActionPlan
) -> dict[str, Any]:
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
        from .gitlab_milestones import apply

        return apply(api, session, plan, target_id)
    if plan.kind == "gitlab_issue":
        from .gitlab_issues import apply

        return apply(api, session, plan, target_id)
    if plan.kind == "gitlab_wiki":
        from .gitlab_wikis import apply

        return apply(api, session, plan, target_id)
    if plan.kind == "gitlab_runner":
        from .gitlab_runners import apply

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
    if kind in {"gitlab_milestone", "gitlab_issue", "gitlab_wiki"}:
        result.add_argument("--title", help="requested title")
    if kind in {"gitlab_milestone", "gitlab_issue"}:
        result.add_argument(
            "--description-file", metavar="PATH", help="UTF-8 description body"
        )
    if kind in {"gitlab_milestone", "gitlab_issue"}:
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
    if kind == "gitlab_runner":
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
        result.add_argument("--description", help="server-visible runner recovery key")
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
    return result


def _result(plan: ActionPlan, status: str, **fields: Any) -> dict[str, Any]:
    records = fields.pop("records", [])
    errors = fields.pop("errors", [])
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


def _verified_skill_revision(session: Any) -> str | None:
    """Read the source revision verified by the bound skill identity.

    :param session: Bound GitLab session, if live binding completed.
    :returns: Verified full source SHA, or ``None`` when unavailable.
    """
    skill = getattr(session, "skill", None)
    revision = skill.get("revision") if isinstance(skill, dict) else None
    if not isinstance(revision, dict) or revision.get("verified") is not True:
        return None
    value = revision.get("value")
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)
        else None
    )


def _access_failure(access: dict[str, Any]) -> tuple[str, str]:
    """Keep the failed GitLab identity or target check in an action result."""
    errors = access.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        source = errors[0].get("source")
        reason = errors[0].get("reason")
        if (
            source in {"user", "target"}
            and isinstance(reason, str)
            and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason)
        ):
            return f"gitlab_access.{source}", reason
    return "gitlab_access", "gitlab_access_not_pass"


def _planned_failure(
    args: argparse.Namespace,
    kind: str,
    plan: ActionPlan,
    source: str,
    reason: str,
    *,
    phase: str,
    mutated: bool | None,
    session: Any = None,
    access: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    cleanup_errors: list[dict[str, str]] | None = None,
    started: float,
) -> int:
    """Keep plan and known write state in every failure after planning."""
    error = {"source": source, "reason": sanitize(reason, 240)}
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
    data["status"] = BLOCKED
    data.setdefault("errors", []).append(error)
    if cleanup_errors:
        data["errors"].extend(cleanup_errors)
        data["cleanup"] = {**data.get("cleanup", {}), "status": BLOCKED}
    data["summary"]["error_count"] = len(data["errors"])
    if session is not None:
        data["tested_revision"] = _verified_skill_revision(session)
        for field in ("credential_source", "credential_digest", "skill"):
            value = getattr(session, field, None)
            if value is not None:
                data[field] = value
    if access is not None and access.get("status") == PASS:
        data["identity"] = access.get("identity")
        data["verified_target"] = access.get("target")
    if args.receipt_out:
        try:
            write_portable_receipt(data, args.receipt_out)
        except (OSError, RuntimeError, ValueError):
            data["errors"].append(
                {"source": "receipt_out", "reason": "receipt_unavailable"}
            )
            data["summary"]["error_count"] = len(data["errors"])
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
        print(f"BLOCKED: {error['reason']}", file=sys.stderr)
    else:
        sys.stdout.write(rendered)
    return exit_code(BLOCKED)


@dataclass
class GitLabAction:
    """Parsed workflow inputs and the existing plan/report state.

    .. attribute :: args
        This workflow's parsed arguments.

    .. attribute :: plan
        Prepared provider operation.

    .. attribute :: session
        Bound credential and target identity.

    .. attribute :: access
        Existing access-check evidence.

    .. attribute :: report
        Existing operation report.

    .. attribute :: phase
        Last entered operation phase.

    .. attribute :: mutated
        Known write state, or None for an uncertain outcome.

    .. attribute :: failure_source
        Existing failure classification.

    .. attribute :: started
        Monotonic start time for diagnostics.

    """

    args: argparse.Namespace
    plan: ActionPlan | None = None
    session: Any = None
    access: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    phase: str = "OFFLINE_PLAN"
    mutated: bool | None = False
    failure_source: str = "arguments"
    started: float = dataclass_field(default_factory=time.monotonic)

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> GitLabAction:
        """Bind a workflow's own arguments without provider access.

        :param args: Arguments from the existing action parser.
        :returns: Unexecuted action state.
        """
        return cls(args=args)

    def prepare(self) -> None:
        """Validate and plan using the existing target and operation adapters.

        :raises ActionError: If arguments or the selected operation are invalid.
        """
        args, kind = self.args, self.args.kind
        if args.describe:
            from .catalog import COMMAND_BY_KIND, describe

            self.report = describe(COMMAND_BY_KIND[kind])
            return
        if not args.action:
            raise ActionError("action_required")
        runner_read = kind == "gitlab_runner" and args.action in {"get", "list"}
        if runner_read and (args.apply or args.confirm_plan or args.live_plan):
            raise ActionError("runner_read_rejects_apply_options")
        if runner_read and args.dry_run and (args.output_dir or args.log_file):
            raise ActionError("dry_run_cannot_write_output")
        if args.apply and args.dry_run:
            raise ActionError("apply_and_dry_run_conflict")
        live_plan = getattr(args, "live_plan", False)
        if live_plan and (args.apply or args.dry_run):
            raise ActionError("live_plan_mode_conflict")
        if args.timeout <= 0 or args.timeout > 300:
            raise ActionError("timeout_out_of_range")
        if args.confirm_plan and not args.apply:
            raise ActionError("confirm_plan_requires_apply")
        if args.receipt_out and not (args.apply or (runner_read and not args.dry_run)):
            raise ActionError("receipt_out_requires_live_run")
        if (
            not args.apply
            and not live_plan
            and not runner_read
            and (args.output_dir or args.log_file)
        ):
            raise ActionError("dry_run_cannot_write_output")
        self.failure_source = "gitlab_action"
        runner_delete = kind == "gitlab_runner" and args.action == "delete"
        target, target_source = resolve_gitlab_target(
            args.target,
            args.binding,
            dry_run=not args.apply
            and not live_plan
            and (not runner_read or args.dry_run),
        )
        plan = make_plan(kind, args, target, target_source)
        self.plan = plan
        group_assignment = (
            plan.kind == "gitlab_runner"
            and plan.operation == "assign"
            and plan.target_kind == "group"
        )
        if live_plan and not (group_assignment or runner_delete):
            raise ActionError("live_plan_requires_group_assignment_or_runner_delete")
        self.plan = plan
        self.target = target
        if (runner_read and args.dry_run) or (
            not args.apply and not live_plan and not runner_read
        ):
            self.report = _result(plan, DRY_RUN, phase="OFFLINE_PLAN", mutated=False)

    def authenticate(self) -> GitLabService | None:
        """Bind credentials and verify the target using the existing access path.

        :returns: Verified provider service, or None for offline modes.
        :raises ActionError: If confirmation, identity, or access is invalid.
        """
        if self.report is not None:
            return None
        args, plan, target = self.args, self.plan, self.target
        group_assignment = (
            plan.kind == "gitlab_runner"
            and plan.operation == "assign"
            and plan.target_kind == "group"
        )
        runner_delete = plan.kind == "gitlab_runner" and plan.operation == "delete"
        self.phase = "ACCESS"
        if (
            args.apply
            and not (group_assignment or runner_delete)
            and (not args.confirm_plan or args.confirm_plan != plan.digest)
        ):
            raise ActionError("confirm_plan_mismatch_rerun_dry_run")
        session = bind_gitlab_session(
            target,
            target_kind=plan.target_kind,
            target_reference=plan.target_reference,
            target_source=plan.target_source,
            revision=args.revision,
        )
        self.session = session
        if (
            plan.kind == "gitlab_runner"
            and plan.operation in {"tag", "get", "list", "delete"}
            and _verified_skill_revision(session) is None
        ):
            raise ActionError("skill_revision_unverified")
        api = GlabAPIClient(timeout=args.timeout)
        access = check_gitlab_operation_access(session, api_client=api)
        self.access = access
        if access.get("status") != PASS:
            self.failure_source, reason = _access_failure(access)
            self.session = session
            raise ActionError(reason)
        if group_assignment:
            from .gitlab_runners import project_ids

            selected = _object(access.get("target"), "access_target", "kind", "id")
            if selected["kind"] != "group":
                raise ActionError("access_target_kind_mismatch")
            plan = replace(
                plan,
                project_ids=project_ids(api, session, _id(selected["id"], "target")),
            )
        if runner_delete:
            from .gitlab_runners import delete_snapshot

            selected = _object(access.get("target"), "access_target", "kind", "id")
            plan = replace(
                plan,
                runner_snapshot=delete_snapshot(
                    api, session, plan, _id(selected["id"], "target")
                ),
            )
        self.session, self.access, self.plan = session, access, plan
        if getattr(args, "live_plan", False):
            self.phase = "LIVE_PLAN"
            data = _result(
                plan,
                PLANNED,
                phase="LIVE_PLAN",
                mutated=False,
                identity=access.get("identity"),
                verified_target=access.get("target"),
                credential_source=session.credential_source,
                credential_digest=session.credential_digest,
                skill=session.skill,
                readback={
                    "verified": True,
                    **(
                        {"runner_snapshot": plan.runner_snapshot}
                        if runner_delete
                        else {"project_ids": list(plan.project_ids or ())}
                    ),
                },
            )
            self.report = data
            self.report["tested_revision"] = _verified_skill_revision(session)
        selected = _object(access.get("target"), "access_target", "kind", "id")
        return GitLabService(api, session, _id(selected["id"], "target"), access)

    def begin(self) -> None:
        """Preserve confirmation and bound-target guards before an operation.

        :raises ActionError: If confirmation or bound target does not match.
        """
        plan, args = self.plan, self.args
        if plan.kind == "gitlab_runner" and plan.operation in {"get", "list"}:
            self.phase = "READ"
            return
        if not args.confirm_plan or args.confirm_plan != plan.digest:
            raise ActionError("confirm_plan_mismatch_rerun_live_plan")
        self.phase, self.mutated = "APPLY", None
        plan.verified_target_id(self.session, self.access)

    def finish(self, record: Any) -> None:
        """Use the existing result contract for a read or mutation.

        :param record: Typed observation or provider action record.
        """
        plan, session, access = self.plan, self.session, self.access
        if self.phase == "READ":
            observation = record
            data = _result(
                plan,
                PASS,
                phase="READ",
                mutated=False,
                records=observation.records,
                identity=access.get("identity"),
                verified_target=access.get("target"),
                credential_source=session.credential_source,
                credential_digest=session.credential_digest,
                skill=session.skill,
                readback={
                    "verified": True,
                    "record_count": len(observation.records),
                    "truncated": observation.truncated,
                },
                related_job=observation.related_job,
            )
        else:
            if isinstance(record, dict):
                self.mutated = record.get("mutated", record.get("action") == "APPLIED")
            errors = [
                {
                    "source": f"project:{error['project_id']}",
                    "reason": error["reason"],
                }
                for error in record.get("errors", [])
            ]
            cleanup = record.get("cleanup", {"status": "NOT_APPLICABLE"})
            complete = (
                record.get("verified") is True
                and not errors
                and cleanup.get("status") not in {"BLOCKED", "PARTIAL"}
            )
            data = _result(
                plan,
                PASS if complete else PARTIAL,
                phase="APPLY",
                mutated=record.get("mutated", record.get("action") == "APPLIED"),
                result_action=record.get("action"),
                records=[record],
                errors=errors,
                identity=access.get("identity"),
                verified_target=access.get("target"),
                readback=record,
                credential_source=session.credential_source,
                credential_digest=session.credential_digest,
                skill=session.skill,
                cleanup=cleanup,
            )
            mutated = data["mutated"]
            self.mutated = mutated
        data["tested_revision"] = _verified_skill_revision(session)
        self.report = data

    def failure(self, exc: Exception) -> int:
        """Retain the existing structured CLI failure and unknown-write evidence.

        :param exc: Failure raised by execution or output handling.
        :returns: Existing blocked exit code after emitting the failure report.
        """
        reason = (
            sanitize(str(exc), 240)
            if isinstance(
                exc, (ActionError, TargetError, GitLabAPIError, OSError, RuntimeError)
            )
            else "unexpected_runtime_failure"
        )
        cleanup_errors = [
            {"source": f"cleanup.{failure.callback}", "reason": failure.reason}
            for failure in getattr(exc, "cleanup_failures", ())
        ]
        if self.plan is None:
            return _failure(
                self.args,
                self.args.kind,
                self.failure_source,
                reason,
                cleanup_errors=cleanup_errors,
            )
        return _planned_failure(
            self.args,
            self.args.kind,
            self.plan,
            self.failure_source,
            reason,
            phase=self.phase,
            mutated=self.mutated,
            session=self.session,
            access=self.access,
            result=self.report,
            cleanup_errors=cleanup_errors,
            started=self.started,
        )


class GitLabAuth(GitlabCallback[GitLabService]):
    """Prepare an action and establish its existing authenticated service."""

    def collect(self) -> None:
        """Validate arguments before provider access."""
        self.action.prepare()

    def run(self) -> GitLabService | None:
        """Bind the requested service or produce the offline response.

        :returns: Verified service when live execution was selected.
        """
        return self.action.authenticate()

    def update(self) -> None:
        """Share authentication and any completed offline report."""
        if self.result is not None:
            self.context.services["gitlab"] = self.result
        self.context.report = self.action.report

    @property
    def complete(self) -> bool:
        """Continue only when the action needs a live operation.

        :returns: False for describe, offline plan, or completed live plan.
        """
        return self.action.report is None


class GitLabOperation(GitlabCallback[Any]):
    """Shared plan and report hooks for concrete GitLab operations."""

    @property
    def plan(self) -> ActionPlan:
        """Resolve typed CLI inputs or an existing direct-call plan.

        :returns: The operation's prepared plan.
        """
        return (
            self.action.plan if isinstance(self.action, GitLabAction) else self.action
        )

    def collect(self) -> None:
        """Apply the existing workflow guards at the operation boundary."""
        if isinstance(self.action, GitLabAction):
            self.action.begin()

    def update(self) -> None:
        """Adapt typed results to the existing CLI output contract."""
        if isinstance(self.action, GitLabAction):
            self.action.finish(self.result)
            self.context.report = self.action.report

    def run(self) -> dict[str, Any]:
        """Dispatch operations not yet moved from the existing adapter.

        :returns: Existing provider action record.
        """
        service = self.gitlab
        return apply_plan(service.api, service.session, service.access, self.plan)


def operation_callback(action: GitLabAction) -> GitLabOperation:
    """Choose a concrete existing operation without executing it.

    :param action: Arguments and plan state owned by this workflow.
    :returns: The selected operation callback.
    """
    from .gitlab_milestones import CreateMilestone, UpdateMilestone
    from .gitlab_runners import (
        AttachToProject,
        CreateRunner,
        DeleteRunner,
        ReadRunner,
        SetTag,
    )

    callbacks = {
        "gitlab_runner": {
            "assign": AttachToProject,
            "create": CreateRunner,
            "delete": DeleteRunner,
            "get": ReadRunner,
            "list": ReadRunner,
            "tag": SetTag,
        },
        "gitlab_milestone": {
            "create": CreateMilestone,
            "update": UpdateMilestone,
            "adjust-time": UpdateMilestone,
        },
    }
    callback = callbacks.get(action.args.kind, {}).get(
        action.args.action, GitLabOperation
    )
    return callback(action=action)


def run_action_cli(kind: str, argv: list[str] | None = None) -> int:
    """Keep the existing callable CLI adapter on the same callback workflow.

    :param kind: Registered GitLab action command.
    :param argv: Optional argument vector; defaults to process arguments.
    :returns: Exit code for the existing structured report.
    """
    args = action_parser(kind).parse_args(argv)
    action = GitLabAction.from_args(args)
    try:
        result = Executor(
            context=ExecutionContext.from_args(args),
            callbacks=[GitLabAuth(action=action), operation_callback(action)],
        ).run()
        return emit_result(result, args)
    except Exception as exc:  # noqa: BLE001 - preserve the existing structured CLI boundary
        return action.failure(exc)
