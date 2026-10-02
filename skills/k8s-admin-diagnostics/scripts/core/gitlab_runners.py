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
from .gitlab_api import GitLabAPIError
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
    if args.apply and not args.token_out:
        raise ActionError("token_out_required_for_runner_create_apply")
    token_out = (
        str(Path(args.token_out).expanduser().absolute()) if args.token_out else None
    )
    return body, None, token_out


def _assignment(
    api: Any, session: Any, runner_id: int, project_id: int
) -> dict[str, Any]:
    scope = f"projects/{project_id}/runners"
    assigned = [
        item
        for item in _fields(_pages(api, session, scope), "runner_list", "id")
        if item.get("id") == runner_id
    ]
    if len(assigned) > 1:
        raise ActionError("runner_assignment_ambiguous")
    if assigned:
        return {"project_id": project_id, "action": "NO_OP", "verified": True}
    api.post_json(session, scope, {"runner_id": runner_id})
    observed = [
        item
        for item in _fields(_pages(api, session, scope), "runner_list", "id")
        if item.get("id") == runner_id
    ]
    if len(observed) != 1:
        raise ActionError("runner_assignment_readback_mismatch")
    return {"project_id": project_id, "action": "APPLIED", "verified": True}


def _assign(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
    runner_id = _positive(plan.resource_id, "runner_id")
    runner = _object(api.get_json(session, f"runners/{runner_id}"), "runner", "id")
    if runner["id"] != runner_id:
        raise ActionError("runner_id_mismatch")
    if plan.target_kind == "project":
        result = _assignment(api, session, runner_id, target_id)
        return {
            "action": result["action"],
            "id": runner_id,
            "verified": True,
            "projects": [result],
        }
    projects = _pages(
        api,
        session,
        f"groups/{target_id}/projects?include_subgroups=true&with_shared=false",
    )
    identifiers = [
        _id(item.get("id"), "project")
        for item in _fields(projects, "group_projects", "id")
    ]
    if len(set(identifiers)) != len(identifiers):
        raise ActionError("group_projects_duplicate_id")
    if not identifiers:
        raise ActionError("group_projects_empty")
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for project_id in identifiers:
        try:
            results.append(_assignment(api, session, runner_id, project_id))
        except (ActionError, GitLabAPIError) as exc:
            errors.append({"project_id": project_id, "reason": str(exc)})
    return {
        "action": "PARTIAL" if errors else "APPLIED",
        "id": runner_id,
        "verified": not errors,
        "projects": results,
        "errors": errors,
    }


def _create(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
    scope = f"{plan.target_kind}s/{target_id}/runners"
    matching = [
        item
        for item in _fields(
            _pages(api, session, scope), "runner_list", "id", "description"
        )
        if item.get("description") == plan.body["description"]
    ]
    if matching:
        raise ActionError("runner_description_exists_recover_instead_of_retry")
    if not plan.token_out:
        raise ActionError("token_out_required_for_runner_create_apply")
    destination = Path(plan.token_out)
    try:
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as exc:
        raise ActionError("token_out_unavailable") from exc
    body = dict(plan.body)
    body[f"{plan.target_kind}_id"] = target_id
    try:
        try:
            created = _object(
                api.post_json(session, "user/runners", body), "runner_create", "id"
            )
        except GitLabAPIError as exc:
            raise ActionError(
                "runner_create_outcome_uncertain_check_description_before_retry"
            ) from exc
        token = created.get("token")
        if not isinstance(token, str) or not token:
            raise ActionError("runner_create_missing_one_time_token")
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            if destination.stat().st_size == 0:
                destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    runner_id = _id(created["id"], "runner")
    observed = _object(
        api.get_json(session, f"runners/{runner_id}"),
        "runner_readback",
        "id",
        "description",
    )
    if (
        observed["id"] != runner_id
        or observed["description"] != plan.body["description"]
    ):
        raise ActionError("runner_readback_mismatch_token_saved")
    scoped = [
        item
        for item in _fields(_pages(api, session, scope), "runner_list", "id")
        if item.get("id") == runner_id
    ]
    if len(scoped) != 1:
        raise ActionError("runner_scope_readback_mismatch_token_saved")
    return {"action": "APPLIED", "id": runner_id, "verified": True, "token_saved": True}


def apply(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
    if plan.operation == "assign":
        return _assign(api, session, plan, target_id)
    return _create(api, session, plan, target_id)
