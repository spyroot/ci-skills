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
from .gitlab_api import (
    GitLabAPIError,
    create_guard,
    uncertain_write,
    unverified_write,
)


# Summary: Validate a milestone create, update, or date adjustment.
# Arguments: args supplies action, fields, optional description path, and ID.
# Environment inputs: none; Stdout: none; Stderr: none.
# Exit classes: ActionError for invalid fields; Side effects: reads optional description file.
# Idempotency: same arguments and file content yield the same plan
# Cleanup: file reader closes its handle.
def prepare(args: Namespace) -> tuple[dict[str, Any], int | None, None]:
    """Build the milestone body before any provider call.

    :param args: Parsed milestone action and requested fields.
    :returns: Validated body, optional milestone ID, and no extra resource key.
    :raises ActionError: If the action, dates, or field combination is invalid.
    """
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


# Summary: Apply one milestone plan and verify its observed GitLab state.
# Arguments: api and session call GitLab, plan names the change, target_id selects its scope.
# Environment inputs: authenticated session; Stdout: none; Stderr: none.
# Exit classes: ActionError or GitLabAPIError; Side effects: reads and may write one milestone.
# Idempotency: matching state returns NO_OP; Cleanup: caller owns API session.
def apply(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
    """Return APPLIED only after an independent milestone GET matches the plan.

    :param api: GitLab API client used for reads and the selected write.
    :param session: Authenticated GitLab session passed to the client.
    :param plan: Validated action, target, and desired milestone fields.
    :param target_id: Numeric project or group ID selected by the plan.
    :returns: Action, ID, and read-back status for the milestone.
    :raises ActionError: If the resource is ambiguous or observed state differs.
    :raises GitLabAPIError: If a provider request fails without recovery.
    """
    base = f"{plan.target_kind}s/{target_id}/milestones"
    if plan.operation == "create":
        return _create(api, session, plan, base, target_id)
    else:
        identifier = _positive(plan.resource_id, "milestone_id")
        current = _object(
            api.get_json(session, f"{base}/{identifier}"), "milestone", "id"
        )
        if _same(current, plan.body):
            return {"action": "NO_OP", "id": identifier, "verified": True}
        try:
            api.put_json(session, f"{base}/{identifier}", plan.body)
        except GitLabAPIError as exc:
            if not uncertain_write(exc):
                raise
            observed = _object(
                api.get_json(session, f"{base}/{identifier}"),
                "milestone_readback",
                "id",
                "title",
            )
            if observed["id"] != identifier or not _same(observed, plan.body):
                raise ActionError(
                    "milestone_update_outcome_uncertain_check_id_before_retry"
                ) from None
            return {
                "action": "APPLIED",
                "id": identifier,
                "verified": True,
                "title": observed["title"],
                "reconciled": True,
            }
    try:
        observed = _object(
            api.get_json(session, f"{base}/{identifier}"),
            "milestone_readback",
            "id",
            "title",
        )
    except GitLabAPIError:
        return unverified_write(
            target_id, {"id": identifier}, "milestone_readback_unavailable"
        )
    except ActionError:
        return unverified_write(
            target_id, {"id": identifier}, "milestone_readback_invalid_shape"
        )
    if observed["id"] != identifier or not _same(observed, plan.body):
        return unverified_write(
            target_id, {"id": identifier}, "milestone_readback_mismatch"
        )
    return {
        "action": "APPLIED",
        "id": identifier,
        "verified": True,
        "title": observed["title"],
    }


# Summary: Find milestones with one exact title and reject ambiguity.
# Arguments: api and session read GitLab, base is the collection URL, title is exact.
# Environment inputs: authenticated session; Stdout: none; Stderr: none.
# Exit classes: ActionError for duplicates, GitLabAPIError for a failed read.
# Side effects: GitLab list read.
# Idempotency: result follows current GitLab state; Cleanup: caller owns API session.
def _find_exact(api: Any, session: Any, base: str, title: str) -> list[dict[str, Any]]:
    """Return zero or one milestone with the requested title.

    :param api: GitLab API client used to page through the collection.
    :param session: Authenticated GitLab session.
    :param base: Milestone collection API path.
    :param title: Title compared exactly after the provider filter.
    :returns: Empty list or one matching milestone.
    :raises ActionError: If more than one exact title is present.
    """
    exact = [
        item
        for item in _fields(
            _pages(api, session, f"{base}?{urlencode({'title': title})}"),
            "milestone_list",
            "id",
            "title",
        )
        if item.get("title") == title
    ]
    if len(exact) > 1:
        raise ActionError("milestone_title_ambiguous")
    return exact


# Summary: Create an absent milestone while holding the exact-title guard.
# Arguments: api/session access GitLab, plan, base, target_id bind the create target.
# Environment inputs: authenticated session; Stdout: none; Stderr: none.
# Exit classes: ActionError or GitLabAPIError; Side effects: GitLab read and possible create.
# Idempotency: matching existing milestone returns NO_OP; Cleanup: guard releases its lock.
def _create(
    api: Any, session: Any, plan: ActionPlan, base: str, target_id: int
) -> dict[str, Any]:
    """Create an absent milestone or confirm an exact existing one.

    :param api: GitLab API client used for list, create, and read-back.
    :param session: Authenticated GitLab session.
    :param plan: Confirmed create plan with the exact requested title.
    :param base: Milestone collection API path.
    :param target_id: Numeric project or group ID.
    :returns: APPLIED or NO_OP with the independently observed milestone ID.
    :raises ActionError: If an existing or uncertain result cannot be reconciled.
    :raises GitLabAPIError: If a provider request fails without recovery.
    """
    with create_guard(
        plan.origin, plan.target_kind, target_id, "milestone", plan.body["title"]
    ) as guard:
        exact = _find_exact(api, session, base, plan.body["title"])
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
            guard.clear()
            return {"action": "NO_OP", "id": identifier, "verified": True}
        guard.reconcile_absent()
        guard.mark_pending()
        try:
            response = api.post_json(session, base, plan.body)
        except GitLabAPIError as exc:
            if not uncertain_write(exc):
                guard.clear()
                raise
            exact = _find_exact(api, session, base, plan.body["title"])
            if not exact:
                raise ActionError(
                    "milestone_create_outcome_uncertain_check_title_before_retry"
                ) from None
            identifier = _id(exact[0].get("id"), "milestone")
            reconciled = True
        else:
            created = _object(response, "milestone_create", "id")
            identifier = _id(created["id"], "milestone")
            reconciled = False
        try:
            observed = _object(
                api.get_json(session, f"{base}/{identifier}"),
                "milestone_readback",
                "id",
                "title",
            )
        except GitLabAPIError:
            return unverified_write(
                target_id, {"id": identifier}, "milestone_readback_unavailable"
            )
        except ActionError:
            return unverified_write(
                target_id, {"id": identifier}, "milestone_readback_invalid_shape"
            )
        if observed["id"] != identifier or not _same(observed, plan.body):
            if observed.get("title") == plan.body["title"]:
                guard.clear()
            return unverified_write(
                target_id, {"id": identifier}, "milestone_readback_mismatch"
            )
        guard.clear()
        result = {
            "action": "APPLIED",
            "id": identifier,
            "verified": True,
            "title": observed["title"],
        }
        if reconciled:
            result["reconciled"] = True
        return result
