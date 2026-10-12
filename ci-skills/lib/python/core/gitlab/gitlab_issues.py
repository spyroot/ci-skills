"""Exact-title bug issue creation with sequential idempotency and read-back.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import MutableMapping, Sequence
from urllib.parse import urlencode

from lib.python.core.gitlab.gitlab_actions import (
    ActionError,
    ActionPlan,
    PreparedOperation,
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
    """Validate a bug issue request without calling GitLab.

    :param args: Action, title, optional description file, labels, and milestone ID.
    :returns: Named provider body with no existing resource or token destination.
    :raises ActionError: Operation, title, labels, milestone, or description file
        is invalid or duplicated.
    """
    if args.action not in {"open-bug", "create-bug"}:
        raise ActionError("unsupported_issue_action")
    body: MutableMapping[str, JSONValue] = {
        "title": _required_text(args.title, "title")
    }
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
    return PreparedOperation(body)


def apply(
    api: GlabAPIClient, session: BoundGitLabSession, plan: ActionPlan, target_id: int
) -> JSONObject:
    """Create an exact-title issue or verify an existing matching issue.

    :param api: Authenticated GitLab API client.
    :param session: Bound GitLab host and credential session.
    :param plan: Confirmed issue body and target identity.
    :param target_id: Access-verified project ID.
    :returns: ``APPLIED``, ``NO_OP``, or unverified write record with issue IID
        and read-back evidence.
    :raises ActionError: Existing title is ambiguous, closed, or differs, or an
        uncertain write cannot be reconciled.
    :raises GitLabAPIError: A terminal provider request or unresolved create marker blocks creation.
    :raises OSError: The create lock or its persistent marker cannot be accessed.
    """  # noqa: DOC503 - create-lock acquisition and marker persistence propagate OSError
    base = f"projects/{target_id}/issues"
    title = _required_text(plan.body.get("title"), "title")
    with create_guard(
        plan.origin, plan.target_kind, target_id, "issue", title
    ) as guard:
        matches = _find_exact(api, session, base, title)
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
        guard.reconcile_absent()
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
        return _created_issue(api, session, plan, base, target_id, iid, guard)


def _created_issue(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    plan: ActionPlan,
    base: str,
    target_id: int,
    iid: int,
    guard: CreateGuard,
) -> JSONObject:
    """Verify a created issue while retaining evidence of a successful write.

    :param api: Authenticated GitLab API client.
    :param session: Bound host and credential session.
    :param plan: Confirmed issue fields for independent comparison.
    :param base: Selected project's issue collection endpoint.
    :param target_id: Verified project ID used in read-back failure records.
    :param iid: Positive issue IID returned by this invocation's POST.
    :param guard: Create marker cleared only after the title is observable.
    :returns: Verified creation or an explicit unverified-write report.
    :raises ActionError: The concrete plan lacks a valid creation title.
    :raises OSError: Clearing the persistent create marker fails.
    """  # noqa: DOC502 - title validation and marker cleanup propagate failures
    try:
        observed = _object(
            api.get_json(session, f"{base}/{iid}"),
            "issue_readback",
            "iid",
            "title",
        )
    except GitLabAPIError:
        return unverified_write(target_id, {"iid": iid}, "issue_readback_unavailable")
    except ActionError:
        return unverified_write(target_id, {"iid": iid}, "issue_readback_invalid_shape")
    if (
        observed["iid"] != iid
        or observed.get("state") != "opened"
        or not _same(observed, plan.body)
    ):
        # A title match is enough to make a future create detect this
        # resource, even if its state or other fields are wrong.
        if observed.get("title") == _required_text(plan.body.get("title"), "title"):
            guard.clear()
        return unverified_write(target_id, {"iid": iid}, "issue_readback_mismatch")
    guard.clear()
    return {
        "action": "APPLIED",
        "iid": iid,
        "verified": True,
        "web_url": observed.get("web_url"),
    }


def _find_exact(
    api: GlabAPIClient, session: BoundGitLabSession, base: str, title: str
) -> Sequence[JSONObject]:
    """Find at most one issue with an exact title in a project.

    :param api: Authenticated GitLab API client.
    :param session: Bound GitLab session.
    :param base: Project issue collection endpoint.
    :param title: Exact title to match after provider search.
    :returns: Zero or one validated issue records.
    :raises ActionError: Matches are ambiguous or page records are malformed.
    :raises GitLabAPIError: A collection page request fails.
    """  # noqa: DOC503 - collection page reads propagate GitLabAPIError
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
    api: GlabAPIClient,
    session: BoundGitLabSession,
    plan: ActionPlan,
    base: str,
    guard: CreateGuard,
) -> JSONObject:
    """Read back an uncertain issue POST without resending it.

    :param api: Authenticated GitLab API client.
    :param session: Bound GitLab session.
    :param plan: Plan supplying the expected title and body.
    :param base: Project issue collection endpoint.
    :param guard: Create marker cleared after verified reconciliation.
    :returns: Verified ``APPLIED`` record marked reconciled.
    :raises ActionError: Exact issue is absent or differs from the plan.
    :raises GitLabAPIError: Reconciliation read fails.
    :raises OSError: Clearing the reconciled create marker fails.
    """  # noqa: DOC503 - provider reads and create-marker cleanup propagate failures
    title = _required_text(plan.body.get("title"), "title")
    matches = _find_exact(api, session, base, title)
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
