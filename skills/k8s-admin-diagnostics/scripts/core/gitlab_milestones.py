"""Project or group milestone plans and independent GitLab read-back."""

from __future__ import annotations

from argparse import Namespace
from typing import Any
from urllib.parse import urlencode

from .gitlab_actions import (
    ActionError,
    ActionPlan,
    _date,
    _fields,
    _file_text,
    _id,
    _object,
    _pages,
    _positive,
    _required_text,
    _same,
)


def prepare(args: Namespace) -> tuple[dict[str, Any], int | None, None]:
    """Validate one milestone mutation without calling a provider."""
    body: dict[str, Any] = {}
    identifier: int | None = None
    if args.action == "create":
        body["title"] = _required_text(args.title, "title")
    elif args.action in {"update", "adjust-time"}:
        identifier = _positive(args.milestone_id, "milestone_id")
        if args.title is not None:
            if args.action == "adjust-time":
                raise ActionError("adjust_time_accepts_dates_only")
            body["title"] = _required_text(args.title, "title")
    else:
        raise ActionError("unsupported_milestone_action")
    description = _file_text(args.description_file, "description_file")
    if description is not None:
        if args.action == "adjust-time":
            raise ActionError("adjust_time_accepts_dates_only")
        body["description"] = description
    for field, value in (
        ("start_date", _date(args.start_date, "start_date")),
        ("due_date", _date(args.due_date, "due_date")),
    ):
        if value is not None:
            body[field] = value
    if args.state is not None:
        if args.action != "update":
            raise ActionError("state_only_valid_for_update")
        body["state_event"] = "close" if args.state == "closed" else "activate"
    if args.action != "create" and not body:
        raise ActionError("milestone_update_requires_field")
    if (
        body.get("start_date")
        and body.get("due_date")
        and body["start_date"] > body["due_date"]
    ):
        raise ActionError("start_date_after_due_date")
    return body, identifier, None


def apply(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
    """Find one exact title or ID, mutate once, then GET independently."""
    base = f"{plan.target_kind}s/{target_id}/milestones"
    if plan.operation == "create":
        exact = [
            item
            for item in _fields(
                _pages(
                    api, session, f"{base}?{urlencode({'title': plan.body['title']})}"
                ),
                "milestone_list",
                "id",
                "title",
            )
            if item.get("title") == plan.body["title"]
        ]
        if len(exact) > 1:
            raise ActionError("milestone_title_ambiguous")
        if exact:
            identifier = _id(exact[0].get("id"), "milestone")
            current = _object(
                api.get_json(session, f"{base}/{identifier}"),
                "milestone",
                "id",
                "title",
            )
            if not _same(current, plan.body):
                raise ActionError("existing_milestone_differs_use_update")
            return {"action": "NO_OP", "id": identifier, "verified": True}
        created = _object(
            api.post_json(session, base, plan.body), "milestone_create", "id"
        )
        identifier = _id(created["id"], "milestone")
    else:
        identifier = _positive(plan.resource_id, "milestone_id")
        current = _object(
            api.get_json(session, f"{base}/{identifier}"), "milestone", "id"
        )
        if _same(current, plan.body):
            return {"action": "NO_OP", "id": identifier, "verified": True}
        api.put_json(session, f"{base}/{identifier}", plan.body)
    observed = _object(
        api.get_json(session, f"{base}/{identifier}"),
        "milestone_readback",
        "id",
        "title",
    )
    if observed["id"] != identifier or not _same(observed, plan.body):
        raise ActionError("milestone_readback_mismatch")
    return {
        "action": "APPLIED",
        "id": identifier,
        "verified": True,
        "title": observed["title"],
    }
