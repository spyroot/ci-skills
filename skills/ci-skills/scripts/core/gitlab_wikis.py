"""Project wiki create and update with exact slug read-back."""

from __future__ import annotations

import re
from argparse import Namespace
from typing import Any
from urllib.parse import quote

from .gitlab_actions import (
    ActionError,
    ActionPlan,
    _fields,
    _file_text,
    _object,
    _pages,
    _required_text,
    _same,
)
from .gitlab_api import (
    GitLabAPIError,
    create_guard,
    uncertain_write,
    unverified_write,
)


# Summary: Validate wiki create or update fields before a provider call.
# Arguments: args supplies action, content file, title, and optional slug.
# Environment inputs: none; Stdout: none; Stderr: none.
# Exit classes: ActionError for invalid fields; Side effects: reads content file.
# Idempotency: same file and arguments give the same body; Cleanup: file reader closes its handle.
def prepare(args: Namespace) -> tuple[dict[str, Any], str | None, None]:
    """Build the wiki body and optional update slug.

    :param args: Parsed wiki action and requested content and fields.
    :returns: Validated body, optional update slug, and no extra resource key.
    :raises ActionError: If action, content, title, or slug is invalid.
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
        return body, None, None
    slug = _required_text(args.slug, "slug")
    if args.title is not None:
        body["title"] = _required_text(args.title, "title")
    return body, slug, None


# Summary: Normalize whitespace and hyphens for exact wiki title comparison.
# Arguments: value is a candidate title; Environment inputs: none.
# Stdout: none; Stderr: none; Exit classes: returns None for invalid title.
# Side effects: none; Idempotency: same title gives same key; Cleanup: none.
def _title_key(value: object) -> str | None:
    """Return GitLab's observed title comparison key.

    :param value: Candidate wiki title of unknown provider type.
    :returns: Normalized title or None for blank and non-string values.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    return re.sub(r"[\s-]+", " ", value).strip()


# Summary: Compare wiki content and GitLab-normalized title.
# Arguments: current is observed wiki, body is requested fields.
# Environment inputs: none; Stdout: none; Stderr: none; Exit classes: bool result.
# Side effects: none; Idempotency: same mappings give same result; Cleanup: none.
def _wiki_same(current: dict[str, Any], body: dict[str, Any]) -> bool:
    """Decide whether the observed wiki satisfies the requested body.

    :param current: Wiki fields returned by GitLab.
    :param body: Requested content and optional title.
    :returns: True when all requested fields match.
    """
    if not _same(current, {key: val for key, val in body.items() if key != "title"}):
        return False
    return "title" not in body or _title_key(current.get("title")) == _title_key(
        body["title"]
    )


# Summary: Apply a wiki plan and verify the slug and content by GET.
# Arguments: api/session access GitLab, plan binds change, target_id selects project.
# Environment inputs: authenticated session; Stdout: none; Stderr: none.
# Exit classes: ActionError or GitLabAPIError; Side effects: wiki reads and possible write.
# Idempotency: matching wiki returns NO_OP; Cleanup: caller owns API session.
def apply(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
    """Return APPLIED only after an independent wiki read matches the plan.

    :param api: GitLab client for wiki reads and the selected write.
    :param session: Authenticated GitLab session.
    :param plan: Confirmed create or update plan.
    :param target_id: Numeric project ID.
    :returns: APPLIED or NO_OP with verified wiki slug.
    :raises ActionError: If resource identity or requested fields are invalid.
    :raises GitLabAPIError: If a provider request fails without recovery.
    """
    base = f"projects/{target_id}/wikis"
    if plan.operation == "create":
        return _create(api, session, plan, base, target_id)
    else:
        slug = _required_text(str(plan.resource_id), "wiki_slug")
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


# Summary: Verify an uncertain wiki PUT without repeating the write.
# Arguments: api/session read GitLab, plan, base, old_endpoint locate the wiki.
# Environment inputs: authenticated session; Stdout: none; Stderr: none.
# Exit classes: ActionError or GitLabAPIError; Side effects: wiki list and detail reads.
# Idempotency: no repeat PUT; Cleanup: caller owns API session.
def _reconcile_update(
    api: Any, session: Any, plan: ActionPlan, base: str, old_endpoint: str
) -> dict[str, Any]:
    """Read the desired wiki after an uncertain PUT without resending it.

    :param api: GitLab client used for list and read-back calls.
    :param session: Authenticated GitLab session.
    :param plan: Requested fields and optional new title.
    :param base: Wiki collection API path.
    :param old_endpoint: Original slug endpoint when title is unchanged.
    :returns: Reconciled APPLIED result with verified slug.
    :raises ActionError: If no matching wiki exists or fields differ.
    """
    if "title" in plan.body:
        matches = _find_exact(api, session, base, plan.body["title"])
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


# Summary: Find zero or one wiki with an exact normalized title.
# Arguments: api/session read GitLab, base is wiki path, title is requested.
# Environment inputs: authenticated session; Stdout: none; Stderr: none.
# Exit classes: ActionError for duplicates, GitLabAPIError for failed read.
# Side effects: GitLab list read; Idempotency: follows current wiki state.
# Cleanup: caller owns API session.
def _find_exact(api: Any, session: Any, base: str, title: str) -> list[dict[str, Any]]:
    """Return matching wikis after GitLab title normalization.

    :param api: GitLab client used to list pages.
    :param session: Authenticated GitLab session.
    :param base: Wiki collection API path.
    :param title: Exact requested title after normalization.
    :returns: Empty list or one matching wiki.
    :raises ActionError: If more than one page has the title.
    """
    matches = [
        item
        for item in _fields(_pages(api, session, base), "wiki_list", "slug", "title")
        if _title_key(item.get("title")) == _title_key(title)
    ]
    if len(matches) > 1:
        raise ActionError("wiki_title_ambiguous")
    return matches


# Summary: Create an absent wiki under an exact-title guard.
# Arguments: api/session access GitLab, plan, base, target_id bind the create.
# Environment inputs: authenticated session; Stdout: none; Stderr: none.
# Exit classes: ActionError or GitLabAPIError; Side effects: wiki reads and possible POST.
# Idempotency: matching existing page returns NO_OP; Cleanup: guard releases its lock.
def _create(
    api: Any, session: Any, plan: ActionPlan, base: str, target_id: int
) -> dict[str, Any]:
    """Create a wiki page or confirm an exact existing one.

    :param api: GitLab client for list, create, and read-back calls.
    :param session: Authenticated GitLab session.
    :param plan: Confirmed create plan with title and content.
    :param base: Wiki collection API path.
    :param target_id: Numeric project ID.
    :returns: APPLIED or NO_OP with the verified wiki slug.
    :raises ActionError: If an existing or uncertain page cannot be reconciled.
    :raises GitLabAPIError: If a provider request fails without recovery.
    """
    with create_guard(
        plan.origin, plan.target_kind, target_id, "wiki", plan.body["title"]
    ) as guard:
        matches = _find_exact(api, session, base, plan.body["title"])
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
            matches = _find_exact(api, session, base, plan.body["title"])
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
        try:
            observed = _object(
                api.get_json(session, f"{base}/{quote(slug, safe='')}"),
                "wiki_readback",
                "slug",
                "content",
            )
        except GitLabAPIError:
            return unverified_write(
                target_id, {"slug": slug}, "wiki_readback_unavailable"
            )
        except ActionError:
            return unverified_write(
                target_id, {"slug": slug}, "wiki_readback_invalid_shape"
            )
        if observed["slug"] != slug or not _wiki_same(observed, plan.body):
            if observed.get("title") == plan.body["title"]:
                guard.clear()
            return unverified_write(target_id, {"slug": slug}, "wiki_readback_mismatch")
        guard.clear()
        result: dict[str, Any] = {"action": "APPLIED", "slug": slug, "verified": True}
        if reconciled:
            result["reconciled"] = True
        return result
