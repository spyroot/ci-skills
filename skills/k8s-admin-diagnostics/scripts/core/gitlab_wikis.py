"""Project wiki create and update with exact slug read-back."""

from __future__ import annotations

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


def prepare(args: Namespace) -> tuple[dict[str, Any], str | None, None]:
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


def apply(api: Any, session: Any, plan: ActionPlan, target_id: int) -> dict[str, Any]:
    base = f"projects/{target_id}/wikis"
    if plan.operation == "create":
        return _create(api, session, plan, base, target_id)
    else:
        slug = _required_text(str(plan.resource_id), "wiki_slug")
        endpoint = f"{base}/{quote(slug, safe='')}"
        current = _object(api.get_json(session, endpoint), "wiki", "slug", "content")
        if _same(current, plan.body):
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
    if observed["slug"] != slug or not _same(observed, plan.body):
        return unverified_write(target_id, {"slug": slug}, "wiki_readback_mismatch")
    return {"action": "APPLIED", "slug": slug, "verified": True}


def _reconcile_update(
    api: Any, session: Any, plan: ActionPlan, base: str, old_endpoint: str
) -> dict[str, Any]:
    """Read the desired wiki after an uncertain PUT without resending it."""
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
    if not _same(observed, plan.body):
        raise ActionError("wiki_update_outcome_uncertain_check_slug_before_retry")
    return {
        "action": "APPLIED",
        "slug": _required_text(observed["slug"], "wiki_slug"),
        "verified": True,
        "reconciled": True,
    }


def _find_exact(api: Any, session: Any, base: str, title: str) -> list[dict[str, Any]]:
    matches = [
        item
        for item in _fields(_pages(api, session, base), "wiki_list", "slug", "title")
        if item.get("title") == title
    ]
    if len(matches) > 1:
        raise ActionError("wiki_title_ambiguous")
    return matches


def _create(
    api: Any, session: Any, plan: ActionPlan, base: str, target_id: int
) -> dict[str, Any]:
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
            if not _same(current, plan.body):
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
        if observed["slug"] != slug or not _same(observed, plan.body):
            if observed.get("title") == plan.body["title"]:
                guard.clear()
            return unverified_write(target_id, {"slug": slug}, "wiki_readback_mismatch")
        guard.clear()
        result: dict[str, Any] = {"action": "APPLIED", "slug": slug, "verified": True}
        if reconciled:
            result["reconciled"] = True
        return result
