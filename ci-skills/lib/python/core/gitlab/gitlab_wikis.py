"""Project wiki create and update with exact slug read-back.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import re
from argparse import Namespace
from collections.abc import MutableMapping, Sequence
from typing import Optional
from urllib.parse import quote

from lib.python.core.gitlab.gitlab_actions import (
    ActionError,
    ActionPlan,
    PreparedOperation,
    _fields,
    _file_text,
    _object,
    _pages,
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
    """Validate a wiki create or update request without provider access.

    :param args: Action, content file, optional title, and update slug.
    :returns: Named provider body and optional existing wiki slug.
    :raises ActionError: Operation, content file, title, or slug is invalid.
    """
    if args.action not in {"create", "update"}:
        raise ActionError("unsupported_wiki_action")
    content = _file_text(args.content_file, "content_file")
    if content is None:
        raise ActionError("content_file_required")
    body = {"content": content}
    if args.action == "create":
        body["title"] = _required_text(args.title, "title")
        if args.slug is not None:
            raise ActionError("slug_only_valid_for_update")
        return PreparedOperation(body)
    slug = _required_text(args.slug, "slug")
    if args.title is not None:
        body["title"] = _required_text(args.title, "title")
    return PreparedOperation(body, resource_id=slug)


def _title_key(value: JSONValue) -> Optional[str]:
    """Normalize a wiki title for GitLab's observed hyphen-space behavior.

    :param value: Candidate title from a request or provider response.
    :returns: Whitespace-normalized title, or ``None`` for nontext/blank input.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    return re.sub(r"[\s-]+", " ", value).strip()


def _wiki_same(current: JSONObject, body: JSONObject) -> bool:
    """Compare wiki read-back against requested content and title.

    :param current: Wiki object returned by GitLab.
    :param body: Planned content and optional title.
    :returns: Whether content matches and titles match after normalization.
    """
    if not _same(current, {key: val for key, val in body.items() if key != "title"}):
        return False
    return "title" not in body or _title_key(current.get("title")) == _title_key(
        body["title"]
    )


def apply(
    api: GlabAPIClient, session: BoundGitLabSession, plan: ActionPlan, target_id: int
) -> JSONObject:
    """Create or update a wiki and verify its exact slug read-back.

    :param api: Authenticated GitLab API client.
    :param session: Bound GitLab session.
    :param plan: Confirmed wiki body and selected operation.
    :param target_id: Access-verified project ID.
    :returns: ``APPLIED``, ``NO_OP``, or unverified write record with slug.
    :raises ActionError: Slug, duplicate title, or reconciliation is invalid.
    :raises GitLabAPIError: A terminal provider request or unresolved create marker fails.
    :raises OSError: A creation lock or persistent marker cannot be accessed.
    """  # noqa: DOC503 - provider validation and create-lock handling propagate failures
    base = f"projects/{target_id}/wikis"
    if plan.operation == "create":
        return _create(api, session, plan, base, target_id)
    else:
        slug = _required_text(plan.resource_id, "wiki_slug")
        endpoint = f"{base}/{quote(slug, safe='')}"
        current = _object(api.get_json(session, endpoint), "wiki", "slug", "content")
        if _wiki_same(current, plan.body):
            return {"action": "NO_OP", "slug": current["slug"], "verified": True}
        try:
            response = api.put_json(session, endpoint, plan.body)
        except GitLabAPIError as exc:
            if not uncertain_write(exc):
                raise
            return _reconcile_update(api, session, plan, base, endpoint)
        changed = _object(response, "wiki_update", "slug")
        slug = _required_text(changed["slug"], "wiki_slug")
    try:
        observed = _object(
            api.get_json(session, f"{base}/{quote(slug, safe='')}"),
            "wiki_readback",
            "slug",
            "content",
        )
    except GitLabAPIError:
        return unverified_write(target_id, {"slug": slug}, "wiki_readback_unavailable")
    except ActionError:
        return unverified_write(
            target_id, {"slug": slug}, "wiki_readback_invalid_shape"
        )
    if observed["slug"] != slug or not _wiki_same(observed, plan.body):
        return unverified_write(target_id, {"slug": slug}, "wiki_readback_mismatch")
    return {"action": "APPLIED", "slug": slug, "verified": True}


def _reconcile_update(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    plan: ActionPlan,
    base: str,
    old_endpoint: str,
) -> JSONObject:
    """Read a wiki after an uncertain update without repeating the PUT.

    :param api: Authenticated GitLab API client.
    :param session: Bound GitLab session.
    :param plan: Planned content and optional changed title.
    :param base: Project wiki collection endpoint.
    :param old_endpoint: Original slug endpoint when title is unchanged.
    :returns: Verified ``APPLIED`` record marked reconciled.
    :raises ActionError: The new title is absent or read-back differs.
    :raises GitLabAPIError: Reconciliation read fails.
    """  # noqa: DOC503 - reconciliation reads propagate GitLabAPIError
    if "title" in plan.body:
        title = _required_text(plan.body.get("title"), "title")
        matches = _find_exact(api, session, base, title)
        if not matches:
            raise ActionError("wiki_update_outcome_uncertain_check_slug_before_retry")
        slug = _required_text(matches[0].get("slug"), "wiki_slug")
        endpoint = f"{base}/{quote(slug, safe='')}"
    else:
        endpoint = old_endpoint
    observed = _object(
        api.get_json(session, endpoint), "wiki_readback", "slug", "content"
    )
    if not _wiki_same(observed, plan.body):
        raise ActionError("wiki_update_outcome_uncertain_check_slug_before_retry")
    return {
        "action": "APPLIED",
        "slug": _required_text(observed["slug"], "wiki_slug"),
        "verified": True,
        "reconciled": True,
    }


def _find_exact(
    api: GlabAPIClient, session: BoundGitLabSession, base: str, title: str
) -> Sequence[JSONObject]:
    """Find at most one wiki with the normalized requested title.

    :param api: Authenticated GitLab API client.
    :param session: Bound GitLab session.
    :param base: Project wiki collection endpoint.
    :param title: Title to compare after GitLab normalization.
    :returns: Zero or one validated wiki records.
    :raises ActionError: Matches are ambiguous or page records are malformed.
    :raises GitLabAPIError: A collection page request fails.
    """  # noqa: DOC503 - collection page reads propagate GitLabAPIError
    matches = [
        item
        for item in _fields(_pages(api, session, base), "wiki_list", "slug", "title")
        if _title_key(item.get("title")) == _title_key(title)
    ]
    if len(matches) > 1:
        raise ActionError("wiki_title_ambiguous")
    return matches


def _create(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    plan: ActionPlan,
    base: str,
    target_id: int,
) -> JSONObject:
    """Create an exact-title wiki or verify an existing matching wiki.

    :param api: Authenticated GitLab API client.
    :param session: Bound GitLab session.
    :param plan: Confirmed wiki title and content.
    :param base: Project wiki collection endpoint.
    :param target_id: Access-verified project ID.
    :returns: ``APPLIED``, ``NO_OP``, or unverified write record with slug.
    :raises ActionError: Existing title is ambiguous or differs, or uncertain
        create cannot be reconciled.
    :raises GitLabAPIError: A terminal request or unresolved create marker blocks creation.
    :raises OSError: The create lock or persistent marker cannot be accessed.
    """  # noqa: DOC503 - create-lock acquisition and marker persistence propagate OSError
    title = _required_text(plan.body.get("title"), "title")
    with create_guard(plan.origin, plan.target_kind, target_id, "wiki", title) as guard:
        matches = _find_exact(api, session, base, title)
        if matches:
            slug = _required_text(matches[0].get("slug"), "wiki_slug")
            current = _object(
                api.get_json(session, f"{base}/{quote(slug, safe='')}"),
                "wiki",
                "slug",
                "content",
            )
            if not _wiki_same(current, plan.body):
                raise ActionError("existing_wiki_differs_use_update")
            guard.clear()
            return {"action": "NO_OP", "slug": slug, "verified": True}
        guard.reconcile_absent()
        guard.mark_pending()
        try:
            response = api.post_json(session, base, plan.body)
        except GitLabAPIError as exc:
            if not uncertain_write(exc):
                guard.clear()
                raise
            matches = _find_exact(api, session, base, title)
            if not matches:
                raise ActionError(
                    "wiki_create_outcome_uncertain_check_title_before_retry"
                ) from None
            slug = _required_text(matches[0].get("slug"), "wiki_slug")
            reconciled = True
        else:
            created = _object(response, "wiki_create", "slug")
            slug = _required_text(created["slug"], "wiki_slug")
            reconciled = False
        return _created_wiki(
            api, session, plan, base, target_id, slug, guard, reconciled
        )


def _created_wiki(
    api: GlabAPIClient,
    session: BoundGitLabSession,
    plan: ActionPlan,
    base: str,
    target_id: int,
    slug: str,
    guard: CreateGuard,
    reconciled: bool,
) -> JSONObject:
    """Read the created slug and preserve evidence when its contents cannot be verified.

    :param api: Authenticated GitLab API client.
    :param session: Bound host and credential session.
    :param plan: Confirmed wiki content and title.
    :param base: Selected project's wiki collection endpoint.
    :param target_id: Verified project ID used in failure records.
    :param slug: Exact slug returned or independently reconciled after creation.
    :param guard: Persistent create marker cleared after the title is observable.
    :param reconciled: Whether an uncertain POST was resolved by an exact-title read.
    :returns: Verified creation or explicit unverified-write evidence.
    :raises ActionError: The concrete plan lacks a valid creation title.
    :raises OSError: Clearing the persistent create marker fails.
    """  # noqa: DOC502 - title validation and marker cleanup propagate failures
    try:
        observed = _object(
            api.get_json(session, f"{base}/{quote(slug, safe='')}"),
            "wiki_readback",
            "slug",
            "content",
        )
    except GitLabAPIError:
        return unverified_write(target_id, {"slug": slug}, "wiki_readback_unavailable")
    except ActionError:
        return unverified_write(
            target_id, {"slug": slug}, "wiki_readback_invalid_shape"
        )
    if observed["slug"] != slug or not _wiki_same(observed, plan.body):
        if observed.get("title") == _required_text(plan.body.get("title"), "title"):
            guard.clear()
        return unverified_write(target_id, {"slug": slug}, "wiki_readback_mismatch")
    guard.clear()
    result: MutableMapping[str, JSONValue] = {
        "action": "APPLIED",
        "slug": slug,
        "verified": True,
    }
    if reconciled:
        result["reconciled"] = True
    return result
