"""One declaration of how a Cilium agent is identified and what health means.

The access preflight and the Cilium collector both need to answer two
questions: which Pods are agents, and whether a health response actually
proves the exec capability. Answering them twice let the preflight select a
`cilium-operator-*` Pod by name prefix while the collector selected agents by
the DaemonSet's own selector, and let any parseable JSON -- `null`, `[]`, a
bare string -- count as a passing health read.

Nothing here builds or runs a command, so both callers can import it without a
cycle.
"""

from __future__ import annotations

from typing import Any

HEALTH_COMMAND = ("cilium-health", "status", "-o", "json")
AGENT_DAEMONSET = "cilium"


def matches_selector(labels: dict[str, str], selector: dict[str, Any]) -> bool:
    """Match a DaemonSet's declared Pod selector without guessing labels.

    An absent or empty selector matches nothing: a selector that claimed every
    Pod would send an exec into unrelated workloads.
    """
    if not selector:
        return False
    if not (selector.get("matchLabels") or selector.get("matchExpressions")):
        return False
    for key, value in (selector.get("matchLabels") or {}).items():
        if labels.get(key) != value:
            return False
    for expression in selector.get("matchExpressions") or []:
        if not isinstance(expression, dict):
            return False
        key = expression.get("key")
        operator = expression.get("operator")
        values = expression.get("values") or []
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


def agent_selector(
    daemonsets: list[dict[str, Any]], namespace: str
) -> dict[str, Any] | None:
    """Return the agent DaemonSet's own Pod selector, or None if unusable."""
    selected = [
        item
        for item in daemonsets
        if isinstance(item, dict)
        and (item.get("metadata") or {}).get("namespace") == namespace
        and (item.get("metadata") or {}).get("name") == AGENT_DAEMONSET
    ]
    if len(selected) != 1:
        return None
    selector = (selected[0].get("spec") or {}).get("selector") or None
    if selector and not (
        selector.get("matchLabels") or selector.get("matchExpressions")
    ):
        return None
    return selector


def is_ready(pod: dict[str, Any]) -> bool:
    """Report whether one Pod carries a true Ready condition."""
    return any(
        isinstance(condition, dict)
        and condition.get("type") == "Ready"
        and condition.get("status") == "True"
        for condition in (pod.get("status") or {}).get("conditions") or []
    )


def ready_agent_pods(
    pods: list[dict[str, Any]],
    selector: dict[str, Any],
    namespace: str,
    *,
    node: str | None = None,
) -> list[dict[str, Any]]:
    """Return the ready agent Pods the selector claims, in API order."""
    return [
        item
        for item in pods
        if isinstance(item, dict)
        and (item.get("metadata") or {}).get("namespace") == namespace
        and matches_selector((item.get("metadata") or {}).get("labels") or {}, selector)
        and (not node or (item.get("spec") or {}).get("nodeName") == node)
        and is_ready(item)
    ]


def valid_health(value: Any) -> bool:
    """Decide whether a decoded health response actually reports health.

    Parseable JSON is not a health response: `null`, `[]`, `"ok"` and `{}` all
    decode. A real response carries the queried agent under `local`, or its
    peers under `nodes`. One capture from the lab showed
    `{"local": {"name": ...}, "nodes": [...], "probeInterval", "timestamp"}`,
    but requiring `local.name` would rest on one Cilium version, and a false
    block on a degraded cluster is the failure this skill must not have. So the
    structure is required and the node name is reported as evidence instead --
    see `health_detail`.
    """
    if not isinstance(value, dict):
        return False
    return isinstance(value.get("local"), dict) or isinstance(value.get("nodes"), list)


def health_detail(value: Any) -> dict[str, Any]:
    """Summarize a validated health response without asserting its schema."""
    local = value.get("local") if isinstance(value, dict) else None
    peers = value.get("nodes") if isinstance(value, dict) else None
    name = local.get("name") if isinstance(local, dict) else None
    return {
        "local_node": name if isinstance(name, str) and name.strip() else None,
        "peer_count": len(peers) if isinstance(peers, list) else None,
    }
