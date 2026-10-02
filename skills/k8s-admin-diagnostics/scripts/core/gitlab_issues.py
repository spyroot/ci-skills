"""Exact-title bug issue creation with sequential idempotency and read-back."""

from __future__ import annotations

from argparse import Namespace
from typing import Any
from urllib.parse import urlencode

from .gitlab_actions import (
    ActionError,
    ActionPlan,
    _fields,
    _file_text,
    _id,
    _object,
    _pages,
    _positive,
    _required_text,
    _same,
)


def prepare(args: Namespace) -> tuple[dict[str, Any], None, None]:
    if args.action not in {"open-bug", "create-bug"}:
        raise ActionError("unsupported_issue_action")
    body: dict[str, Any] = {"title": _required_text(args.title, "title")}
    description = _file_text(args.description_file, "description_file")
    if description is not None:
        body["description"] = description
    labels = [_required_text(label, "label") for label in args.label]
    if len(set(labels)) != len(labels):
        raise ActionError("duplicate_label")
    if labels:
        body["labels"] = ",".join(labels)
    if args.milestone_id is not None:
        body["milestone_id"] = _positive(args.milestone_id, "milestone_id")
    return body, None, None


def apply(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
    base = f"projects/{target_id}/issues"
    matches = [
        item
        for item in _fields(
            _pages(
                api,
                session,
                f"{base}?{urlencode({'state': 'all', 'search': plan.body['title']})}",
            ),
            "issue_list",
            "iid",
            "title",
            "state",
        )
        if item.get("title") == plan.body["title"]
    ]
    if len(matches) > 1:
        raise ActionError("issue_title_ambiguous")
    if matches:
        if matches[0]["state"] != "opened":
            raise ActionError("existing_issue_closed")
        iid = _id(matches[0].get("iid"), "issue")
        current = _object(
            api.get_json(session, f"{base}/{iid}"), "issue", "iid", "title"
        )
        if not _same(current, plan.body):
            raise ActionError("existing_issue_differs")
        return {"action": "NO_OP", "iid": iid, "verified": True}
    created = _object(api.post_json(session, base, plan.body), "issue_create", "iid")
    iid = _id(created["iid"], "issue")
    observed = _object(
        api.get_json(session, f"{base}/{iid}"), "issue_readback", "iid", "title"
    )
    if (
        observed["iid"] != iid
        or observed.get("state") != "opened"
        or not _same(observed, plan.body)
    ):
        raise ActionError("issue_readback_mismatch")
    return {
        "action": "APPLIED",
        "iid": iid,
        "verified": True,
        "web_url": observed.get("web_url"),
    }
