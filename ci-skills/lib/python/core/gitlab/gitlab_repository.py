"""Commit caller-supplied GitLab file actions and verify their resulting content.

The shared plan/apply workflow binds the branch, message, and native action JSON.
Only create and update actions are supported; GitLab validates their native fields.
No commit is retried after an uncertain write without independent reconciliation.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import hashlib
import json
from argparse import Namespace
from collections.abc import Mapping, Sequence
from typing import Final, Optional
from urllib.parse import quote, urlencode

from lib.python.core.gitlab.gitlab_actions import (
    ActionError,
    ActionPlan,
    PreparedOperation,
    _array,
    _file_text,
    _object,
    _required_text,
)
from lib.python.core.gitlab.gitlab_api import (
    GitLabAPIError,
    GlabAPIClient,
    create_guard,
    uncertain_write,
    unverified_write,
)
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
from lib.python.core.json_types import JSONObject, JSONValue, copy_json_object

_CONTENT_ACTIONS: Final = frozenset(("create", "update"))


def _content(action: JSONObject) -> bytes:
    """Decode caller content solely for independent byte comparison.

    :param action: Native create or update action from the input JSON array.
    :returns: Intended file bytes, including empty files and trailing newlines.
    :raises ActionError: The action, content, or encoding cannot be verified.
    """
    operation = action.get("action")
    if not isinstance(operation, str) or operation not in _CONTENT_ACTIONS:
        raise ActionError("repository_action_requires_create_or_update")
    value = action.get("content")
    if not isinstance(value, str):
        raise ActionError("repository_action_content_required")
    encoding = action.get("encoding", "text")
    try:
        if encoding == "text":
            return value.encode("utf-8")
        if encoding == "base64":
            return base64.b64decode(value)
    except ValueError as exc:
        raise ActionError("repository_action_content_invalid") from exc
    raise ActionError("repository_action_encoding_unsupported")


def _desired(actions: Sequence[JSONObject]) -> dict[str, bytes]:
    """Collect final requested bytes without rewriting native action objects.

    :param actions: Ordered GitLab create and update actions.
    :returns: Final content keyed by the exact caller-supplied file paths.
    :raises ActionError: A path or content cannot support independent verification.
    """  # noqa: DOC503 - content validation propagates ActionError.
    expected: dict[str, bytes] = {}
    for action in actions:
        path = action.get("file_path")
        if not isinstance(path, str) or not path.strip():
            raise ActionError("repository_file_path_required")
        expected[path] = _content(action)
    return expected


def prepare(args: Namespace) -> PreparedOperation:
    """Load one commit's native actions into the existing immutable plan digest.

    :param args: Commit verb, branch, optional start branch, message, and JSON file.
    :returns: Provider body retaining the caller's native action fields and content.
    :raises ActionError: A required argument, JSON source, or verifiable action is invalid.
    """  # noqa: DOC503 - shared argument and file validation propagate ActionError.
    if args.action != "commit":
        raise ActionError("unsupported_repository_action")
    source = _file_text(args.actions_file, "actions_file")
    if source is None:
        raise ActionError("actions_file_required")
    try:
        actions = _array(json.loads(source), "repository_actions")
    except (ValueError, RecursionError) as exc:
        raise ActionError("repository_actions_invalid_json_array") from exc
    if not actions:
        raise ActionError("repository_actions_required")
    _desired(actions)
    _required_text(args.commit_message, "commit_message")
    body: dict[str, JSONValue] = {
        "branch": _required_text(args.branch, "branch"),
        "commit_message": args.commit_message,
        "actions": actions,
    }
    if args.start_branch is not None:
        body["start_branch"] = _required_text(args.start_branch, "start_branch")
    return PreparedOperation(body)


def _branch_sha(
    api: GlabAPIClient, session: BoundGitLabSession, base: str, branch: str
) -> Optional[str]:
    """Read one exact branch without treating other provider failures as absence.

    :param api: Bound provider transport.
    :param session: Access-verified GitLab session.
    :param base: Selected project's repository endpoint.
    :param branch: Exact branch name to read.
    :returns: Current head SHA, or None for a provider-confirmed missing branch.
    :raises ActionError: The branch response lacks its exact name or commit identity.
    :raises GitLabAPIError: A provider request fails other than with HTTP 404.
    """
    try:
        item = _object(
            api.get_json(session, f"{base}/branches/{quote(branch, safe='')}"),
            "repository_branch",
            "name",
            "commit",
        )
    except GitLabAPIError as exc:
        if exc.reason == "provider_404":
            return None
        raise
    if item["name"] != branch:
        raise ActionError("repository_branch_identity_mismatch")
    commit = _object(item["commit"], "repository_branch_commit", "id")
    return _required_text(commit["id"], "repository_commit_sha")


def _file(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    base: str,
    path: str,
    sha: str,
) -> Optional[JSONObject]:
    """Read a file at an immutable commit for content and concurrency evidence.

    :param api: Bound provider transport.
    :param session: Access-verified GitLab session.
    :param base: Selected project's repository endpoint.
    :param path: Exact repository file path.
    :param sha: Immutable commit used as the read reference.
    :returns: Native file object, or None after HTTP 404.
    :raises ActionError: GitLab returns a different file, commit, or encoding.
    :raises GitLabAPIError: A provider read fails other than with HTTP 404.
    """
    endpoint = f"{base}/files/{quote(path, safe='')}?{urlencode({'ref': sha})}"
    try:
        item = _object(
            api.get_json(session, endpoint),
            "repository_file",
            "file_path",
            "commit_id",
            "content",
            "encoding",
        )
    except GitLabAPIError as exc:
        if exc.reason == "provider_404":
            return None
        raise
    if (
        item["file_path"] != path
        or item["commit_id"] != sha
        or item["encoding"] != "base64"
    ):
        raise ActionError("repository_file_identity_mismatch")
    return item


def _same_files(
    observed: Mapping[str, Optional[JSONObject]], expected: Mapping[str, bytes]
) -> bool:
    """Compare provider file content with the caller's exact intended bytes.

    :param observed: File objects read at one immutable commit.
    :param expected: Intended bytes keyed by exact repository paths.
    :returns: Whether every requested file exists with matching content.
    :raises ActionError: A provider file has malformed base64 content.
    """
    for path, content in expected.items():
        item = observed[path]
        if item is None:
            return False
        encoded = item.get("content")
        if not isinstance(encoded, str):
            raise ActionError("repository_file_content_invalid")
        try:
            actual = base64.b64decode(encoded)
        except ValueError as exc:
            raise ActionError("repository_file_content_invalid") from exc
        if actual != content:
            return False
    return True


def _verify(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    base: str,
    plan: ActionPlan,
    sha: str,
    expected: Mapping[str, bytes],
) -> bool:
    """Independently verify commit identity, message, files, and final branch head.

    :param api: Bound provider transport.
    :param session: Access-verified GitLab session.
    :param base: Selected project's repository endpoint.
    :param plan: Confirmed native commit body.
    :param sha: Returned or independently reconciled candidate commit SHA.
    :param expected: Intended bytes keyed by exact repository paths.
    :returns: Whether all read-back evidence matches the requested commit.
    :raises ActionError: Commit, branch, or file evidence is malformed.
    :raises GitLabAPIError: A required independent provider read fails.
    """  # noqa: DOC503 - independent provider and shape checks propagate failures.
    commit = _object(
        api.get_json(session, f"{base}/commits/{quote(sha, safe='')}"),
        "repository_commit",
        "id",
        "message",
    )
    message = commit["message"]
    if (
        commit["id"] != sha
        or not isinstance(message, str)
        or message.rstrip("\n") != str(plan.body["commit_message"]).rstrip("\n")
    ):
        return False
    observed = {path: _file(api, session, base, path, sha) for path in expected}
    branch = _required_text(plan.body["branch"], "branch")
    return _same_files(observed, expected) and (
        _branch_sha(api, session, base, branch) == sha
    )


def _result(
    action: str, branch: str, sha: str, expected: Mapping[str, bytes]
) -> JSONObject:
    """Return bounded read-back evidence without exposing committed file contents.

    :param action: Existing shared APPLIED or NO_OP action status.
    :param branch: Verified destination branch.
    :param sha: Independently verified commit SHA.
    :param expected: Verified file bytes used only to compute content fingerprints.
    :returns: Shared action record with exact branch, commit, and path evidence.
    """
    return {
        "action": action,
        "branch": branch,
        "commit_sha": sha,
        "verified": True,
        "files": [
            {"file_path": path, "content_sha256": hashlib.sha256(content).hexdigest()}
            for path, content in expected.items()
        ],
    }


def apply(
    api: GlabAPIClient, session: BoundGitLabSession, plan: ActionPlan, target_id: int
) -> JSONObject:
    """Commit once through GitLab and reconcile retries against exact live content.

    :param api: Bound provider transport; POST is never retried blindly.
    :param session: Access-verified GitLab session.
    :param plan: Confirmed commit body including native create or update actions.
    :param target_id: Verified numeric project identifier.
    :returns: Verified APPLIED/NO_OP or an explicit unverified-write action record.
    :raises ActionError: Inputs or before-state are invalid or the branch moves before POST.
    :raises GitLabAPIError: Preflight, terminal POST, or pending-write reconciliation fails.
    :raises OSError: Accessing or clearing the shared persistent create guard fails.
    """  # noqa: DOC503 - provider, plan, and guard operations propagate failures.
    base = f"projects/{target_id}/repository"
    branch = _required_text(plan.body.get("branch"), "branch")
    actions = _array(plan.body.get("actions"), "repository_actions")
    expected = _desired(actions)
    with create_guard(
        plan.origin, plan.target_kind, target_id, "repository", branch
    ) as guard:
        before = _branch_sha(api, session, base, branch)
        if before is not None and _verify(api, session, base, plan, before, expected):
            guard.clear()
            return _result("NO_OP", branch, before, expected)
        if before is None:
            guard.reconcile_absent()
        else:
            guard.require_ready()
        source_sha = before
        if source_sha is None and "start_branch" in plan.body:
            source_branch = _required_text(plan.body["start_branch"], "start_branch")
            source = _object(
                api.get_json(
                    session, f"{base}/commits/{quote(source_branch, safe='')}"
                ),
                "repository_start_commit",
                "id",
            )
            source_sha = _required_text(source["id"], "repository_start_commit_sha")
        body = copy_json_object(plan.body)
        guarded_actions = []
        for action in actions:
            guarded = copy_json_object(action)
            if action.get("action") == "update" and source_sha is not None:
                path = str(action["file_path"])
                current = _file(api, session, base, path, source_sha)
                if current is not None and "last_commit_id" not in guarded:
                    guarded["last_commit_id"] = _required_text(
                        current.get("last_commit_id"), "repository_file_last_commit_id"
                    )
            guarded_actions.append(guarded)
        body["actions"] = guarded_actions
        if before is None and source_sha is not None:
            body.pop("start_branch", None)
            body["start_sha"] = source_sha
        if _branch_sha(api, session, base, branch) != before:
            raise ActionError("repository_branch_changed_before_commit")
        guard.mark_pending()
        reconciled = False
        sha: Optional[str] = None
        try:
            response = api.post_json(session, f"{base}/commits", body)
        except GitLabAPIError as exc:
            if not uncertain_write(exc):
                guard.clear()
                raise
            reconciled = True
        else:
            if isinstance(response, Mapping):
                candidate = response.get("id")
                if isinstance(candidate, str) and candidate:
                    sha = candidate
        try:
            if reconciled:
                sha = _branch_sha(api, session, base, branch)
                if sha == before:
                    sha = None
            verified = sha is not None and _verify(
                api, session, base, plan, sha, expected
            )
        except (ActionError, GitLabAPIError):
            verified = False
        if not verified or sha is None:
            return unverified_write(
                target_id,
                {"branch": branch, "commit_sha": sha},
                "repository_commit_readback_unverified",
            )
        guard.clear()
        result = dict(_result("APPLIED", branch, sha, expected))
        if reconciled:
            result["reconciled"] = True
        return result
