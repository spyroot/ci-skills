"""Reusable Kubernetes label selector matching for Cilium discovery."""

from __future__ import annotations

from typing import Any


def matches_selector(labels: dict[str, str], selector: dict[str, Any]) -> bool:
    """Match a DaemonSet's declared Pod selector without guessing names."""
    if not isinstance(selector, dict) or not selector:
        return False
    match_labels = selector.get("matchLabels", {})
    expressions = selector.get("matchExpressions", [])
    if not isinstance(match_labels, dict) or not isinstance(expressions, list):
        return False
    if any(labels.get(key) != value for key, value in match_labels.items()):
        return False
    for expression in expressions:
        if not isinstance(expression, dict):
            return False
        key = expression.get("key")
        operator = expression.get("operator")
        values = expression.get("values", [])
        if operator == "In" and labels.get(key) not in values:
            return False
        if operator == "NotIn" and labels.get(key) in values:
            return False
        if operator == "Exists" and key not in labels:
            return False
        if operator == "DoesNotExist" and key in labels:
            return False
        if operator not in {"In", "NotIn", "Exists", "DoesNotExist"}:
            return False
    return True


def find_cilium_daemonset(items: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Resolve the one actual Cilium agent DaemonSet and its Pod selector."""
    matches = [
        item
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("metadata"), dict)
        and item["metadata"].get("name") == "cilium"
        and item["metadata"].get("namespace")
    ]
    if len(matches) != 1:
        raise ValueError("cilium_namespace_not_unique")
    selector = matches[0].get("spec", {}).get("selector")
    if not isinstance(selector, dict) or not selector:
        raise ValueError("cilium_selector_missing")
    return matches[0]["metadata"]["namespace"], selector
