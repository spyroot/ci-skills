"""Project or group milestone plans and independent GitLab read-back.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import MutableMapping, Sequence
from typing import Optional
from urllib.parse import urlencode

from lib.python.core.gitlab.gitlab_action import GitLabOperation
from lib.python.core.gitlab.gitlab_actions import (
    ActionError,
    ActionPlan,
    PreparedOperation,
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
from lib.python.core.gitlab.gitlab_api import (
    CreateGuard,
    GitLabAPIError,
    GlabAPIClient,
    create_guard,
    uncertain_write,
    unverified_write,
)
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
from lib.python.core.json_types import JSONObject, JSONValue


def prepare(args: Namespace) -> PreparedOperation:
    """Validate one milestone mutation without calling a provider.

    :param args: Selected milestone arguments.
    :returns: Named provider fields and an optional existing milestone ID.
    :raises ActionError: If the requested fields or operation are invalid.
    """
    body: MutableMapping[str, JSONValue] = {}
    identifier: Optional[int] = None
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
    start_date = _date(args.start_date, "start_date")
    due_date = _date(args.due_date, "due_date")
    for field, value in (("start_date", start_date), ("due_date", due_date)):
        if value is not None:
            body[field] = value
    if args.state is not None:
        if args.action != "update":
            raise ActionError("state_only_valid_for_update")
        body["state_event"] = "close" if args.state == "closed" else "activate"
    if args.action != "create" and not body:
        raise ActionError("milestone_update_requires_field")
    if start_date is not None and due_date is not None and start_date > due_date:
        raise ActionError("start_date_after_due_date")
    return PreparedOperation(body, resource_id=identifier)


class UpdateMilestone(GitLabOperation):
    """Update milestone fields and verify the provider result."""

    def run(self) -> JSONObject:
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


def apply(
    api: GlabAPIClient, session: BoundGitLabSession, plan: ActionPlan, target_id: int
) -> JSONObject:
    """Dispatch an existing milestone caller through its concrete callback.

    :param api: Existing API client.
    :param session: Bound GitLab session.
    :param plan: Confirmed milestone action.
    :param target_id: Verified scope identifier.
    :returns: Operation result and independent read-back evidence.
    :raises ActionError: Selected fields, duplicate identity, or read-back is invalid.
    :raises GitLabAPIError: A terminal provider request or unresolved create fails.
    :raises OSError: A create lock or persistent marker cannot be accessed.
    :raises BaseException: An operation or lifecycle cleanup fails after finalization.
    """  # noqa: DOC502 - operation and lifecycle errors propagate from execute
    callback = CreateMilestone if plan.operation == "create" else UpdateMilestone
    return callback(action=plan).execute(api, session, target_id)


def _find_exact(
    api: GlabAPIClient, session: BoundGitLabSession, base: str, title: str
) -> Sequence[JSONObject]:
    """Find at most one milestone with an exact title in a scope.

    :param api: Authenticated GitLab API client.
    :param session: Bound GitLab session.
    :param base: Project or group milestone collection endpoint.
    :param title: Exact title to match after provider filtering.
    :returns: Zero or one validated milestone records.
    :raises ActionError: Matches are ambiguous or page records are malformed.
    :raises GitLabAPIError: A collection page request fails.
    """  # noqa: DOC503 - collection page reads propagate GitLabAPIError
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
    """Create one exact-title milestone and verify it before clearing the create marker."""

    def run(self) -> JSONObject:
        """Create the milestone or verify the existing exact-title record.

        :returns: Provider record with independent read-back evidence.
        :raises ActionError: If the exact milestone cannot be verified.
        :raises GitLabAPIError: A terminal read/write or unresolved create marker blocks creation.
        :raises OSError: The create lock or its persistent marker cannot be accessed.
        """  # noqa: DOC503 - create-lock acquisition and marker persistence propagate OSError
        api, session = self.gitlab.api, self.gitlab.session
        plan, target_id = self.plan, self.gitlab.target_id
        base = f"{plan.target_kind}s/{target_id}/milestones"
        title = _required_text(plan.body.get("title"), "title")
        with create_guard(
            plan.origin, plan.target_kind, target_id, "milestone", title
        ) as guard:
            exact = _find_exact(api, session, base, title)
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
                exact = _find_exact(api, session, base, title)
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
            return self._readback(identifier, reconciled, guard)

    def _readback(
        self, identifier: int, reconciled: bool, guard: CreateGuard
    ) -> JSONObject:
        """Verify the created identity and retain partial evidence on read-back failure.

        :param identifier: Positive milestone ID returned or independently reconciled.
        :param reconciled: Whether the create response was uncertain and resolved by title.
        :param guard: Locked create marker cleared after the milestone is observable.
        :returns: Verified creation or explicit unverified-write evidence.
        :raises ActionError: The concrete plan lacks a valid creation title.
        :raises OSError: Clearing the persistent create marker fails.
        """  # noqa: DOC502 - title validation and marker cleanup propagate failures
        api, session = self.gitlab.api, self.gitlab.session
        plan, target_id = self.plan, self.gitlab.target_id
        base = f"{plan.target_kind}s/{target_id}/milestones"
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
            if observed.get("title") == _required_text(plan.body.get("title"), "title"):
                guard.clear()
            return unverified_write(
                target_id, {"id": identifier}, "milestone_readback_mismatch"
            )
        guard.clear()
        result: MutableMapping[str, JSONValue] = {
            "action": "APPLIED",
            "id": identifier,
            "verified": True,
            "title": observed["title"],
        }
        if reconciled:
            result["reconciled"] = True
        return result
