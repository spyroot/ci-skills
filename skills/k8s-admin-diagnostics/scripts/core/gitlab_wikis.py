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
        matches = [
            item
            for item in _fields(
                _pages(api, session, base), "wiki_list", "slug", "title"
            )
            if item.get("title") == plan.body["title"]
        ]
        if len(matches) > 1:
            raise ActionError("wiki_title_ambiguous")
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
            return {"action": "NO_OP", "slug": slug, "verified": True}
        created = _object(
            api.post_json(session, base, plan.body), "wiki_create", "slug"
        )
        slug = _required_text(created["slug"], "wiki_slug")
    else:
        slug = _required_text(str(plan.resource_id), "wiki_slug")
        endpoint = f"{base}/{quote(slug, safe='')}"
        current = _object(api.get_json(session, endpoint), "wiki", "slug", "content")
        if _same(current, plan.body):
            return {"action": "NO_OP", "slug": current["slug"], "verified": True}
        changed = _object(
            api.put_json(session, endpoint, plan.body), "wiki_update", "slug"
        )
        slug = _required_text(changed["slug"], "wiki_slug")
    observed = _object(
        api.get_json(session, f"{base}/{quote(slug, safe='')}"),
        "wiki_readback",
        "slug",
        "content",
    )
    if observed["slug"] != slug or not _same(observed, plan.body):
        raise ActionError("wiki_readback_mismatch")
    return {"action": "APPLIED", "slug": slug, "verified": True}
