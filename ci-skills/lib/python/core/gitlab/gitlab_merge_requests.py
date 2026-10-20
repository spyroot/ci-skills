"""Create project merge requests and assign milestones with exact IID read-back.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import MutableMapping, Sequence
from urllib.parse import urlencode

from lib.python.core.gitlab.gitlab_action import GitLabOperation
from lib.python.core.gitlab.gitlab_actions import (
    ActionError,
    ActionPlan,
    PreparedOperation,
    _fields,
    _id,
    _object,
    _pages,
    _positive,
    _required_text,
    _same,
)
from lib.python.core.gitlab.gitlab_api import (
    GitLabAPIError,
    GlabAPIClient,
    create_guard,
    uncertain_write,
    unverified_write,
)
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
from lib.python.core.json_types import JSONObject, JSONValue


def prepare(args: Namespace) -> PreparedOperation:
    """Validate MR creation or milestone assignment without provider access.

    :param args: Action, source and target branches, title, MR IID, and milestone ID.
    :returns: Creation fields or a milestone-only update with the selected MR IID.
    :raises ActionError: The action, required values, or action-specific fields are invalid.
    """
    if args.action == "update":
        if any(
            value is not None
            for value in (args.source_branch, args.target_branch, args.title)
        ):
            raise ActionError("mr_update_accepts_milestone_only")
        return PreparedOperation(
            {"milestone_id": _positive(args.milestone_id, "milestone_id")},
            resource_id=_positive(args.mr_iid, "mr_iid"),
        )
    if args.action != "create":
        raise ActionError("unsupported_mr_action")
    if args.mr_iid is not None:
        raise ActionError("mr_iid_only_valid_for_update")
    body: MutableMapping[str, JSONValue] = {
        "source_branch": _required_text(args.source_branch, "source_branch"),
        "target_branch": _required_text(args.target_branch, "target_branch"),
        "title": _required_text(args.title, "title"),
    }
    if args.milestone_id is not None:
        body["milestone_id"] = _positive(args.milestone_id, "milestone_id")
    return PreparedOperation(body)


def apply(
    api: GlabAPIClient, session: BoundGitLabSession, plan: ActionPlan, target_id: int
) -> JSONObject:
    """Execute the selected MR operation through the shared callback lifecycle.

    :param api: Existing authenticated GitLab API adapter.
    :param session: Bound GitLab host and credential session.
    :param plan: Confirmed create or milestone-assignment action.
    :param target_id: Access-verified project ID.
    :returns: Applied, no-op, or unverified-write evidence with numeric MR ID and IID.
    :raises ActionError: The operation, selected identity, or read-back is invalid.
    :raises GitLabAPIError: A provider request or unresolved creation fails.
    :raises OSError: Accessing a create lock or marker fails.
    :raises BaseException: The operation or lifecycle cleanup fails after finalization.
    """  # noqa: DOC503 - operation and lifecycle errors propagate from execute
    if plan.operation not in {"create", "update"}:
        raise ActionError("unsupported_mr_action")
    callback = CreateMergeRequest if plan.operation == "create" else UpdateMergeRequest
    return callback(action=plan).execute(api, session, target_id)


def _read(
    api: GlabAPIClient, session: BoundGitLabSession, target_id: int, iid: int
) -> JSONObject:
    """Read and validate one project MR by its exact IID.

    :param api: Authenticated GitLab API adapter.
    :param session: Bound GitLab session.
    :param target_id: Access-verified target project ID.
    :param iid: Project-local MR IID selected by the caller or creation response.
    :returns: Provider MR with validated identity, title, and native milestone ID.
    :raises ActionError: The response identity or milestone shape is invalid.
    :raises GitLabAPIError: The exact MR read fails.
    """  # noqa: DOC503 - the provider read propagates GitLabAPIError
    current = _object(
        api.get_json(session, f"projects/{target_id}/merge_requests/{iid}"),
        "mr",
        "id",
        "iid",
        "title",
        "target_project_id",
        "milestone",
    )
    _id(current["id"], "mr")
    if (
        _id(current["iid"], "mr_iid") != iid
        or _id(current["target_project_id"], "mr_target_project") != target_id
    ):
        raise ActionError("mr_identity_mismatch")
    _required_text(current["title"], "mr_title")
    if current["milestone"] is not None:
        milestone = _object(current["milestone"], "mr_milestone", "id")
        _id(milestone["id"], "mr_milestone")
    return current


def _record(current: JSONObject, action: str) -> JSONObject:
    """Return the existing operation evidence shape from a validated MR.

    :param current: Exact MR read-back with validated ID, IID, title, and milestone.
    :param action: Existing applied or no-op action token.
    :returns: Verified MR identity and its native milestone ID, absent as null.
    :raises ActionError: The milestone object does not contain its native ID.
    """  # noqa: DOC502 - milestone extraction uses the shared object validator
    milestone = current["milestone"]
    return {
        "action": action,
        "id": current["id"],
        "iid": current["iid"],
        "title": current["title"],
        "milestone_id": None
        if milestone is None
        else _object(milestone, "mr_milestone", "id")["id"],
        "verified": True,
    }


def _find_exact(
    api: GlabAPIClient, session: BoundGitLabSession, plan: ActionPlan, target_id: int
) -> Sequence[JSONObject]:
    """Find one open MR for the exact source and target branches in this project.

    :param api: Authenticated GitLab API adapter.
    :param session: Bound GitLab session.
    :param plan: Creation plan containing the source and target branches.
    :param target_id: Access-verified project ID for both branches.
    :returns: Zero or one matching open MR records.
    :raises ActionError: The collection is malformed, incomplete, or ambiguous.
    :raises GitLabAPIError: A bounded collection read fails.
    """  # noqa: DOC503 - bounded page reads propagate GitLabAPIError
    expected = {
        "state": "opened",
        "source_branch": _required_text(
            plan.body.get("source_branch"), "source_branch"
        ),
        "target_branch": _required_text(
            plan.body.get("target_branch"), "target_branch"
        ),
    }
    endpoint = f"projects/{target_id}/merge_requests?{urlencode(expected)}"
    matches = [
        item
        for item in _fields(
            _pages(api, session, endpoint),
            "mr_list",
            "id",
            "iid",
            "source_project_id",
            "target_project_id",
            *expected,
        )
        if _same(item, expected)
        and _id(item["source_project_id"], "mr_source_project") == target_id
        and _id(item["target_project_id"], "mr_target_project") == target_id
    ]
    if len(matches) > 1:
        raise ActionError("mr_branches_ambiguous")
    return matches


def _readback(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    plan: ActionPlan,
    target_id: int,
    identifier: int,
    iid: int,
    reconciled: bool,
) -> JSONObject:
    """Verify a write against the exact MR IID and preserve unverified evidence.

    :param api: Authenticated GitLab API adapter.
    :param session: Bound GitLab session.
    :param plan: Expected creation fields or assigned milestone ID.
    :param target_id: Access-verified target project ID.
    :param identifier: Global MR ID from the creation response or initial read.
    :param iid: Project-local MR IID to read independently.
    :param reconciled: Whether the provider write had an uncertain response.
    :returns: Verified applied record or existing partial-write evidence.
    """
    resource = {"id": identifier, "iid": iid}
    try:
        observed = _read(api, session, target_id, iid)
    except GitLabAPIError:
        return unverified_write(target_id, resource, "mr_readback_unavailable")
    except ActionError:
        return unverified_write(target_id, resource, "mr_readback_invalid_shape")
    expected: MutableMapping[str, JSONValue] = dict(plan.body)
    if plan.operation == "create":
        expected.update(state="opened", source_project_id=target_id)
    if observed["id"] != identifier or not _same(observed, expected):
        return unverified_write(target_id, resource, "mr_readback_mismatch")
    result = dict(_record(observed, "APPLIED"))
    if reconciled:
        result["reconciled"] = True
    return result


class CreateMergeRequest(GitLabOperation):
    """Create one MR for caller-selected branches or verify its existing open record."""

    def run(self) -> JSONObject:
        """Create once, reconcile uncertain outcomes, and read back the exact MR.

        :returns: Applied, no-op, or unverified-write record with MR ID and IID.
        :raises ActionError: Existing branches differ from the plan or creation is unresolved.
        :raises GitLabAPIError: A provider request or pending creation marker fails.
        :raises OSError: Accessing the shared creation lock or marker fails.
        """  # noqa: DOC503 - create-lock and marker operations propagate OSError
        api, session = self.gitlab.api, self.gitlab.session
        plan, target_id = self.plan, self.gitlab.target_id
        base = f"projects/{target_id}/merge_requests"
        branches = urlencode(
            {
                field: _required_text(plan.body.get(field), field)
                for field in ("source_branch", "target_branch")
            }
        )
        with create_guard(
            plan.origin, plan.target_kind, target_id, "merge_request", branches
        ) as guard:
            matches = _find_exact(api, session, plan, target_id)
            if matches:
                iid = _id(matches[0]["iid"], "mr_iid")
                current = _read(api, session, target_id, iid)
                expected = {
                    **plan.body,
                    "state": "opened",
                    "source_project_id": target_id,
                }
                if current["id"] != matches[0]["id"] or not _same(current, expected):
                    raise ActionError("existing_mr_differs_use_update")
                guard.clear()
                return _record(current, "NO_OP")
            guard.reconcile_absent()
            guard.mark_pending()
            try:
                response = api.post_json(session, base, plan.body)
            except GitLabAPIError as exc:
                if not uncertain_write(exc):
                    guard.clear()
                    raise
                matches = _find_exact(api, session, plan, target_id)
                if not matches:
                    raise ActionError(
                        "mr_create_outcome_uncertain_check_branches_before_retry"
                    ) from None
                created = matches[0]
                reconciled = True
            else:
                created = _object(response, "mr_create", "id", "iid")
                reconciled = False
            result = _readback(
                api,
                session,
                plan,
                target_id,
                _id(created["id"], "mr"),
                _id(created["iid"], "mr_iid"),
                reconciled,
            )
            if result.get("verified") is True:
                guard.clear()
            return result


class UpdateMergeRequest(GitLabOperation):
    """Assign a milestone to one exact MR without changing other MR fields."""

    def run(self) -> JSONObject:
        """Apply a milestone-only update and read back its native milestone ID.

        :returns: Applied, no-op, or unverified-write record with MR ID and IID.
        :raises ActionError: The selected MR IID or provider record is invalid.
        :raises GitLabAPIError: A terminal provider read or write fails.
        """  # noqa: DOC503 - provider reads propagate GitLabAPIError
        api, session = self.gitlab.api, self.gitlab.session
        plan, target_id = self.plan, self.gitlab.target_id
        iid = _positive(plan.resource_id, "mr_iid")
        current = _read(api, session, target_id, iid)
        if _same(current, plan.body):
            return _record(current, "NO_OP")
        reconciled = False
        try:
            api.put_json(
                session, f"projects/{target_id}/merge_requests/{iid}", plan.body
            )
        except GitLabAPIError as exc:
            if not uncertain_write(exc):
                raise
            reconciled = True
        return _readback(
            api,
            session,
            plan,
            target_id,
            _id(current["id"], "mr"),
            iid,
            reconciled,
        )
