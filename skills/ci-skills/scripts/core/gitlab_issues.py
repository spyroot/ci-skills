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


# Summary: Validate one bug issue request before contacting GitLab.
# Arguments: args supplies title, optional description path, labels, and milestone ID.
# Environment inputs: none; Stdout: none; Stderr: none.
# Exit classes: ActionError for invalid fields; Side effects: reads optional description file.
# Idempotency: same inputs yield the same body; Cleanup: file reader closes its handle.
def prepare(args: Namespace) -> tuple[dict[str, Any], None, None]:
    """Build the issue body for an open-bug or create-bug plan.

    :param args: Parsed issue action and requested fields.
    :returns: Validated issue body and two absent resource keys.
    :raises ActionError: If the action, labels, or milestone ID is invalid.
    """
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


# Summary: Create an absent open issue or verify an exact existing one.
# Arguments: api/session access GitLab, plan selects fields, target_id selects project.
# Environment inputs: authenticated session; Stdout: none; Stderr: none.
# Exit classes: ActionError or GitLabAPIError; Side effects: issue reads and possible POST.
# Idempotency: exact open issue returns NO_OP; Cleanup: guard releases its lock.
def apply(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
    """Return APPLIED only after the created issue matches an independent GET.

    :param api: GitLab client for list, create, and read-back calls.
    :param session: Authenticated GitLab session.
    :param plan: Confirmed issue creation plan.
    :param target_id: Numeric project ID that owns the issue.
    :returns: APPLIED or NO_OP, issue IID, and verification evidence.
    :raises ActionError: If matching issues are ambiguous or differ from the plan.
    :raises GitLabAPIError: If a provider request fails without recovery.
    """
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


# Summary: Search all issue states for one exact title.
# Arguments: api/session read GitLab, base is issue API path, title is exact.
# Environment inputs: authenticated session; Stdout: none; Stderr: none.
# Exit classes: ActionError for duplicate titles, GitLabAPIError for failed read.
# Side effects: GitLab list read; Idempotency: follows current issue state.
# Cleanup: caller owns API session.
def _find_exact(api: Any, session: Any, base: str, title: str) -> list[dict[str, Any]]:
    """Return zero or one issue whose title matches exactly.

    :param api: GitLab client used to page through issues.
    :param session: Authenticated GitLab session.
    :param base: Issue collection API path.
    :param title: Requested exact issue title.
    :returns: Empty list or one matching issue.
    :raises ActionError: If multiple issues share the exact title.
    """
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


# Summary: Verify an uncertain issue POST without repeating the write.
# Arguments: api/session read GitLab, plan and base select issue, guard tracks pending create.
# Environment inputs: authenticated session; Stdout: none; Stderr: none.
# Exit classes: ActionError or GitLabAPIError; Side effects: issue list and detail reads.
# Idempotency: no repeat POST; Cleanup: clears the create guard after verified read-back.
def _reconcile(
    api: Any, session: Any, plan: ActionPlan, base: str, guard: Any
) -> dict[str, Any]:
    """Read after an uncertain POST without sending another write.

    :param api: GitLab client used for list and read-back calls.
    :param session: Authenticated GitLab session.
    :param plan: Creation plan used for exact field comparison.
    :param base: Issue collection API path.
    :param guard: Pending create guard cleared after confirmed read-back.
    :returns: Reconciled APPLIED result with issue IID and URL.
    :raises ActionError: If the issue is absent or does not match the plan.
    """
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
