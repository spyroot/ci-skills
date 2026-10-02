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
from .gitlab_api import (
    GitLabAPIError,
    create_guard,
    uncertain_write,
    unverified_write,
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
    with create_guard(
        plan.origin, plan.target_kind, target_id, "issue", plan.body["title"]
    ) as guard:
        matches = _find_exact(api, session, base, plan.body["title"])
        if matches:
            if matches[0]["state"] != "opened":
                raise ActionError("existing_issue_closed")
            iid = _id(matches[0].get("iid"), "issue")
            current = _object(
                api.get_json(session, f"{base}/{iid}"), "issue", "iid", "title"
            )
            if not _same(current, plan.body):
                raise ActionError("existing_issue_differs")
            guard.clear()
            return {"action": "NO_OP", "iid": iid, "verified": True}
        guard.mark_pending()
        try:
            response = api.post_json(session, base, plan.body)
        except GitLabAPIError as exc:
            if not uncertain_write(exc):
                guard.clear()
                raise
            return _reconcile(api, session, plan, base, guard)
        created = _object(response, "issue_create", "iid")
        iid = _id(created["iid"], "issue")
        try:
            observed = _object(
                api.get_json(session, f"{base}/{iid}"),
                "issue_readback",
                "iid",
                "title",
            )
        except GitLabAPIError:
            return unverified_write(
                target_id, {"iid": iid}, "issue_readback_unavailable"
            )
        except ActionError:
            return unverified_write(
                target_id, {"iid": iid}, "issue_readback_invalid_shape"
            )
        if (
            observed["iid"] != iid
            or observed.get("state") != "opened"
            or not _same(observed, plan.body)
        ):
            # A title match is enough to make a future create detect this
            # resource, even if its state or other fields are wrong.
            if observed.get("title") == plan.body["title"]:
                guard.clear()
            return unverified_write(target_id, {"iid": iid}, "issue_readback_mismatch")
        guard.clear()
        return {
            "action": "APPLIED",
            "iid": iid,
            "verified": True,
            "web_url": observed.get("web_url"),
        }


def _find_exact(api: Any, session: Any, base: str, title: str) -> list[dict[str, Any]]:
    matches = [
        item
        for item in _fields(
            _pages(
                api,
                session,
                f"{base}?{urlencode({'state': 'all', 'search': title})}",
            ),
            "issue_list",
            "iid",
            "title",
            "state",
        )
        if item.get("title") == title
    ]
    if len(matches) > 1:
        raise ActionError("issue_title_ambiguous")
    return matches


def _reconcile(
    api: Any, session: Any, plan: ActionPlan, base: str, guard: Any
) -> dict[str, Any]:
    """Read after an uncertain POST; never resend the write."""
    matches = _find_exact(api, session, base, plan.body["title"])
    if not matches:
        raise ActionError("issue_create_outcome_uncertain_check_title_before_retry")
    iid = _id(matches[0].get("iid"), "issue")
    observed = _object(
        api.get_json(session, f"{base}/{iid}"), "issue_readback", "iid", "title"
    )
    if observed.get("state") != "opened" or not _same(observed, plan.body):
        raise ActionError("issue_create_reconciliation_mismatch")
    guard.clear()
    return {
        "action": "APPLIED",
        "iid": iid,
        "verified": True,
        "web_url": observed.get("web_url"),
        "reconciled": True,
    }
