"""Project runner assignment and one-time-token runner creation."""

from __future__ import annotations

import os
from argparse import Namespace
from pathlib import Path
from typing import Any

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
from .gitlab_api import GitLabAPIError, create_guard, uncertain_write
from .target import GitLabOperationTarget


def prepare(
    args: Namespace, target: GitLabOperationTarget, target_kind: str
) -> tuple[dict[str, Any], int | None, str | None]:
    if args.action == "assign":
        identifier = _positive(args.runner_id or target.gitlab.runner_id, "runner_id")
        if args.token_out or args.description or args.tag or args.runner_type:
            raise ActionError("assign_accepts_runner_id_only")
        return {}, identifier, None
    if args.action != "create":
        raise ActionError("unsupported_runner_action")
    body: dict[str, Any] = {
        "runner_type": f"{target_kind}_type",
        "description": _required_text(args.description, "description"),
    }
    tags = [_required_text(tag, "tag") for tag in args.tag]
    if len(set(tags)) != len(tags):
        raise ActionError("duplicate_tag")
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


def project_ids(api: Any, session: Any, group_id: int) -> tuple[int, ...]:
    """Resolve the complete, validated group membership for a live plan."""
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
    """Use runner details; project runner lists include inherited availability."""
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
    """Read direct associations once for every project in the selected set."""
    direct = _direct_projects(api, session, runner_id)
    return {project_id: project_id in direct for project_id in identifiers}


def _rollback_assignments(
    api: Any,
    session: Any,
    runner_id: int,
    attempted: list[int],
    baseline: dict[int, bool],
) -> dict[str, Any]:
    """Remove only assignments attempted here, then read all affected states."""
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


def _runner_readback(
    api: Any, session: Any, scope: str, runner_id: int, description: str
) -> None:
    observed = _object(
        api.get_json(session, f"runners/{runner_id}"),
        "runner_readback",
        "id",
        "description",
    )
    if observed["id"] != runner_id or observed["description"] != description:
        raise ActionError("runner_readback_mismatch")
    scoped = [
        item
        for item in _fields(_pages(api, session, scope), "runner_list", "id")
        if item["id"] == runner_id
    ]
    if len(scoped) != 1:
        raise ActionError("runner_scope_readback_mismatch")


def _rollback_created_runner(
    api: Any, session: Any, scope: str, runner_id: int, description: str
) -> dict[str, Any]:
    """Delete only the runner independently identified as this new record."""
    try:
        observed = _object(
            api.get_json(session, f"runners/{runner_id}"),
            "runner_cleanup_readback",
            "id",
            "description",
        )
        if observed["id"] != runner_id or observed["description"] != description:
            return {"status": "BLOCKED", "reason": "runner_cleanup_identity_mismatch"}
        api.delete_json(session, f"runners/{runner_id}")
        remaining = [
            item
            for item in _fields(_pages(api, session, scope), "runner_list", "id")
            if item["id"] == runner_id
        ]
        if remaining:
            return {"status": "BLOCKED", "reason": "runner_cleanup_readback_mismatch"}
        return {"status": "PASS", "deleted_runner_id": runner_id}
    except (ActionError, GitLabAPIError) as exc:
        return {"status": "BLOCKED", "reason": str(exc)}


def _create(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
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
                _runner_readback(api, session, scope, runner_id, description)
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
    if plan.operation == "assign":
        return _assign(api, session, plan, target_id)
    return _create(api, session, plan, target_id)
