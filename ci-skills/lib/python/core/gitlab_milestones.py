"""Project or group milestone plans and independent GitLab read-back."""

from __future__ import annotations

from argparse import Namespace
from typing import Any
from urllib.parse import urlencode

from .gitlab_actions import (
    ActionError,
    ActionPlan,
    GitLabOperation,
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


def prepare(args: Namespace) -> tuple[dict[str, Any], int | None, None]:
    """Validate one milestone mutation without calling a provider.

    :param args: Selected milestone arguments.
    :returns: Provider fields, optional milestone ID, and no token destination.
    :raises ActionError: If the requested fields or operation are invalid.
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


class UpdateMilestone(GitLabOperation):
    """Update milestone fields and verify the provider result."""

    def run(self) -> dict[str, Any]:
        """Apply one bounded milestone update.

        :returns: Applied or no-op record with read-back evidence.
        :raises ActionError: If an uncertain update cannot be verified.
        :raises GitLabAPIError: If a terminal provider operation fails.
        """
        api, session = self.gitlab.api, self.gitlab.session
        plan, target_id = self.plan, self.gitlab.target_id
        base = f"{plan.target_kind}s/{target_id}/milestones"
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


def apply(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
    """Dispatch an existing milestone caller through its concrete callback.

    :param api: Existing API client.
    :param session: Bound GitLab session.
    :param plan: Confirmed milestone action.
    :param target_id: Verified scope identifier.
    :returns: Operation result and independent read-back evidence.
    """
    callback = CreateMilestone if plan.operation == "create" else UpdateMilestone
    return callback(action=plan).execute(api, session, target_id)


def _find_exact(api: Any, session: Any, base: str, title: str) -> list[dict[str, Any]]:
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


class CreateMilestone(GitLabOperation):
    """CreateMilestone using the existing GitLab operation and read-back."""

    def run(self) -> dict[str, Any]:
        """Execute the selected action.

        :returns: Provider record with independent read-back evidence.
        :raises ActionError: If the exact milestone cannot be verified.
        :raises GitLabAPIError: If a terminal provider operation fails.
        """
        api, session = self.gitlab.api, self.gitlab.session
        plan, target_id = self.plan, self.gitlab.target_id
        base = f"{plan.target_kind}s/{target_id}/milestones"
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
