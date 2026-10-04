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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .access import check_gitlab_operation_access
from .cli import _failure, log_event, output_mode, parser, resolve_gitlab_target
from .credentials import bind_gitlab_session
from .gitlab_api import GitLabAPIError, GlabAPIClient
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

    # Summary: hash exact operation, target, and body into a plan identifier
    # Arguments: plan fields; Environment inputs: none
    # Stdout: none; Stderr: none; Exit classes: SHA-256 hex digest
    # Side effects: none; Idempotency: same plan gives same digest; Cleanup: none
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
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    # Summary: expose nonsecret plan shape and body digest
    # Arguments: plan fields; Environment inputs: none
    # Stdout: none; Stderr: none; Exit classes: public plan mapping
    # Side effects: none; Idempotency: same plan gives same map; Cleanup: none
    def public(self) -> dict[str, Any]:
        """Describe the plan without publishing issue/wiki text or a token."""
        return {
            "operation": self.operation,
            "target_kind": self.target_kind,
            "target_reference": self.target_reference,
            "resource_id": self.resource_id,
            "project_ids": list(self.project_ids)
            if self.project_ids is not None
            else None,
            "confirmation_ready": not (
                self.kind == "gitlab_runner"
                and self.operation == "assign"
                and self.target_kind == "group"
                and self.project_ids is None
            ),
            "fields": sorted(self.body),
            "body_sha256": hashlib.sha256(
                json.dumps(self.body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "plan_digest": self.digest,
            "one_time_sink_required": self.operation == "create"
            and self.kind == "gitlab_runner",
        }


# Summary: validate a positive nonboolean integer input
# Arguments: value and field name; Environment inputs: none
# Stdout: none; Stderr: none; Exit classes: integer or ActionError
# Side effects: none; Idempotency: same value gives same result; Cleanup: none
def _positive(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ActionError(f"{name}_must_be_positive_integer") from exc
    if number <= 0 or isinstance(value, bool):
        raise ActionError(f"{name}_must_be_positive_integer")
    return number


# Summary: validate an optional real ISO calendar date
# Arguments: date text and field name; Environment inputs: none
# Stdout: none; Stderr: none; Exit classes: date, None, or ActionError
# Side effects: none; Idempotency: same text gives same result; Cleanup: none
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


# Summary: trim a required nonempty action field
# Arguments: value and field name; Environment inputs: none
# Stdout: none; Stderr: none; Exit classes: text or ActionError
# Side effects: none; Idempotency: same text gives same result; Cleanup: none
def _required_text(value: str | None, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionError(f"{name}_required")
    return value.strip()


# Summary: read an optional bounded UTF-8 action body file
# Arguments: path and field name; Environment inputs: selected file bytes
# Stdout: none; Stderr: none; Exit classes: text, None, or ActionError
# Side effects: reads file; Idempotency: stable for unchanged file
# Cleanup: read_text closes file
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


# Summary: select valid project or group for the requested operation
# Arguments: target, parsed arguments, command kind; Environment inputs: none
# Stdout: none; Stderr: none; Exit classes: selection triple or ActionError
# Side effects: none; Idempotency: same inputs give same target; Cleanup: none
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


# Summary: construct a fingerprinted action plan from validated inputs
# Arguments: kind, arguments, target, target source; Environment inputs: body files
# Stdout: none; Stderr: none; Exit classes: ActionPlan or ActionError
# Side effects: may read body file; Idempotency: depends on body file bytes
# Cleanup: delegated file reads close
def make_plan(
    kind: str,
    args: argparse.Namespace,
    target: GitLabOperationTarget,
    target_source: str,
) -> ActionPlan:
    """Validate one action and fingerprint its exact target and input body."""
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


# Summary: require a response object with named fields
# Arguments: payload, source name, required keys; Environment inputs: none
# Stdout: none; Stderr: none; Exit classes: mapping or ActionError
# Side effects: none; Idempotency: same payload gives same result; Cleanup: none
def _object(payload: Any, name: str, *required: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or any(field not in payload for field in required):
        raise ActionError(f"{name}_invalid_shape")
    return payload


# Summary: require a response list of objects
# Arguments: payload and source name; Environment inputs: none
# Stdout: none; Stderr: none; Exit classes: list or ActionError
# Side effects: none; Idempotency: same payload gives same result; Cleanup: none
def _array(payload: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or any(
        not isinstance(item, dict) for item in payload
    ):
        raise ActionError(f"{name}_invalid_shape")
    return payload


# Summary: collect up to twenty full GitLab list pages for duplicate checks
# Arguments: API client, session, endpoint; Environment inputs: live GitLab API
# Stdout: none; Stderr: none; Exit classes: records or ActionError
# Side effects: read-only API calls; Idempotency: depends on live collection
# Cleanup: API client owns request resources
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


# Summary: validate required fields in GitLab list entries
# Arguments: records, source name, required keys; Environment inputs: none
# Stdout: none; Stderr: none; Exit classes: validated list or ActionError
# Side effects: none; Idempotency: same records give same list; Cleanup: none
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


# Summary: require a positive integer ID from a provider response
# Arguments: value and field name; Environment inputs: none
# Stdout: none; Stderr: none; Exit classes: integer or ActionError
# Side effects: none; Idempotency: same value gives same result; Cleanup: none
def _id(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ActionError(f"{name}_missing_id")
    return value


# Summary: compare requested action fields to live GitLab resource fields
# Arguments: current resource and requested body; Environment inputs: none
# Stdout: none; Stderr: none; Exit classes: boolean
# Side effects: none; Idempotency: same fields give same result; Cleanup: none
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


# Summary: dispatch one bound plan to its GitLab resource implementation
# Arguments: API, session, access receipt, plan; Environment inputs: live GitLab API
# Stdout: none; Stderr: none; Exit classes: verified action record or ActionError
# Side effects: delegated GitLab mutation and read-back
# Idempotency: resource-specific guard decides; Cleanup: delegated code owns it
def apply_plan(
    api: Any, session: Any, access: dict[str, Any], plan: ActionPlan
) -> dict[str, Any]:
    """Use the access-verified numeric target and the same bound credential."""
    if access.get("status") != PASS:
        raise ActionError("gitlab_access_not_pass")
    target = _object(access.get("target"), "access_target", "kind", "id")
    if target["kind"] != plan.target_kind:
        raise ActionError("access_target_kind_mismatch")
    target_id = _id(target["id"], "target")
    if (
        session.origin != plan.origin
        or session.target_reference != plan.target_reference
    ):
        raise ActionError("bound_target_mismatch")
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


# Summary: expose bounded GitLab action options through shared CLI parser
# Arguments: command kind; Environment inputs: catalog metadata
# Stdout: none; Stderr: none; Exit classes: parser or KeyError
# Side effects: none; Idempotency: same kind gives equivalent parser; Cleanup: none
def action_parser(kind: str) -> argparse.ArgumentParser:
    """Keep wrappers thin while exposing the existing universal CLI tier."""
    result = parser(
        f"Plan, apply, and verify {kind.replace('_', ' ')} changes.", kind=kind
    )
    choices = {
        "gitlab_milestone": ("create", "update", "adjust-time"),
        "gitlab_issue": ("open-bug", "create-bug"),
        "gitlab_wiki": ("create", "update"),
        "gitlab_runner": ("assign", "create"),
    }
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
            help="read exact group project IDs and print an apply-ready plan without writes",
        )
        result.add_argument(
            "--runner-id", type=int, help="numeric runner ID for assignment"
        )
        result.add_argument(
            "--runner-type",
            choices=("project", "group"),
            help="runner scope for create",
        )
        result.add_argument("--description", help="server-visible runner recovery key")
        result.add_argument(
            "--tag",
            action="append",
            default=[],
            help="runner tag; repeat to add several",
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
        "--receipt-out", metavar="PATH", help="write one sanitized apply receipt"
    )
    return result


# Summary: assemble a versioned action receipt with plan and record counts
# Arguments: plan, status, receipt fields; Environment inputs: UTC clock and host
# Stdout: none; Stderr: none; Exit classes: receipt mapping
# Side effects: none; Idempotency: timestamp varies; Cleanup: none
def _result(plan: ActionPlan, status: str, **fields: Any) -> dict[str, Any]:
    records = fields.pop("records", [])
    errors = fields.pop("errors", [])
    return {
        "schema_version": "1.0",
        "kind": plan.kind,
        "status": status,
        "captured_at": datetime.now(timezone.utc).isoformat(),
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


# Summary: extract a safe GitLab access failure class from its receipt
# Arguments: access receipt; Environment inputs: none
# Stdout: none; Stderr: none; Exit classes: source and reason pair
# Side effects: none; Idempotency: same receipt gives same pair; Cleanup: none
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


# Summary: emit a blocked action result retaining plan and write state
# Arguments: CLI args, plan, failure context, optional session and result
# Environment inputs: clock and selected output destination
# Stdout: machine result when selected; Stderr: human BLOCKED line
# Exit classes: BLOCKED exit code; Side effects: may write receipt and log
# Idempotency: output timestamp varies; Cleanup: receipt writer removes temporary
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
    data["summary"]["error_count"] = len(data["errors"])
    if session is not None:
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


# Summary: plan, optionally apply, and report one GitLab action
# Arguments: command kind and optional argv; Environment inputs: target, auth, live API
# Stdout: result or describe JSON; Stderr: structured human failure
# Exit classes: status exit code; Side effects: optional GitLab write and receipt
# Idempotency: guarded by plan/read-back; Cleanup: delegated receipt writer handles it
def run_action_cli(kind: str, argv: list[str] | None = None) -> int:
    """Adapter used by every operation entrypoint; no other CLI owns writes."""
    started = time.monotonic()
    args = action_parser(kind).parse_args(argv)
    if args.describe:
        from .catalog import COMMAND_BY_KIND, describe

        sys.stdout.write(
            json.dumps(describe(COMMAND_BY_KIND[kind]), indent=2, sort_keys=True) + "\n"
        )
        return 0
    if not args.action:
        return _failure(args, kind, "arguments", "action_required")
    if args.apply and args.dry_run:
        return _failure(args, kind, "arguments", "apply_and_dry_run_conflict")
    live_plan = getattr(args, "live_plan", False)
    if live_plan and (args.apply or args.dry_run):
        return _failure(args, kind, "arguments", "live_plan_mode_conflict")
    if args.timeout <= 0 or args.timeout > 300:
        return _failure(args, kind, "arguments", "timeout_out_of_range")
    if args.confirm_plan and not args.apply:
        return _failure(args, kind, "arguments", "confirm_plan_requires_apply")
    if args.receipt_out and not args.apply:
        return _failure(args, kind, "arguments", "receipt_out_requires_apply")
    if not args.apply and not live_plan and (args.output_dir or args.log_file):
        return _failure(args, kind, "arguments", "dry_run_cannot_write_output")
    plan: ActionPlan | None = None
    session: Any = None
    access: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    phase = "OFFLINE_PLAN"
    mutated: bool | None = False
    try:
        target, target_source = resolve_gitlab_target(
            args.target, args.binding, dry_run=not args.apply and not live_plan
        )
        plan = make_plan(kind, args, target, target_source)
        group_assignment = (
            plan.kind == "gitlab_runner"
            and plan.operation == "assign"
            and plan.target_kind == "group"
        )
        if live_plan and not group_assignment:
            raise ActionError("live_plan_requires_group_runner_assignment")
        if not args.apply and not live_plan:
            data = _result(plan, DRY_RUN, phase="OFFLINE_PLAN", mutated=False)
        else:
            phase = "ACCESS"
            if (
                args.apply
                and not group_assignment
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
            api = GlabAPIClient(timeout=args.timeout)
            access = check_gitlab_operation_access(session, api_client=api)
            if access.get("status") != PASS:
                source, reason = _access_failure(access)
                return _planned_failure(
                    args,
                    kind,
                    plan,
                    source,
                    reason,
                    phase=phase,
                    mutated=False,
                    session=session,
                    started=started,
                )
            if group_assignment:
                from .gitlab_runners import project_ids

                selected = _object(access.get("target"), "access_target", "kind", "id")
                if selected["kind"] != "group":
                    raise ActionError("access_target_kind_mismatch")
                plan = replace(
                    plan,
                    project_ids=project_ids(
                        api, session, _id(selected["id"], "target")
                    ),
                )
            if live_plan:
                phase = "LIVE_PLAN"
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
                        "project_ids": list(plan.project_ids or ()),
                    },
                )
            else:
                if not args.confirm_plan or args.confirm_plan != plan.digest:
                    raise ActionError("confirm_plan_mismatch_rerun_live_plan")
                phase = "APPLY"
                mutated = None
                record = apply_plan(api, session, access, plan)
                if isinstance(record, dict):
                    mutated = record.get("mutated", record.get("action") == "APPLIED")
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
        if args.receipt_out:
            write_portable_receipt(data, args.receipt_out)
        rendered = emit(data, output_mode(args), args.output_dir)
        log_event(
            args, kind, "result", data["status"], elapsed=time.monotonic() - started
        )
        sys.stdout.write(rendered)
        return exit_code(data["status"])
    except (ActionError, TargetError, GitLabAPIError, OSError, RuntimeError) as exc:
        reason = sanitize(str(exc), 240)
    except Exception:  # noqa: BLE001 - keep malformed provider failures structured
        reason = "unexpected_runtime_failure"
    if plan is None:
        return _failure(args, kind, "gitlab_action", reason)
    return _planned_failure(
        args,
        kind,
        plan,
        "gitlab_action",
        reason,
        phase=phase,
        mutated=mutated,
        session=session,
        access=access,
        result=data,
        started=started,
    )
