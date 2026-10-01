"""One declaration of what the Kubernetes collectors read.

Each collector's resource set, the Cilium health command, and the rule for
selecting Cilium agent Pods live here so the access gate certifies exactly the
reads the collectors perform. A second copy of any of these would let the gate
pass while a collector reads something it never verified.

`(resource, namespaced)` matches the argument shape `read_resources` builds:
a namespaced resource is listed with `-A`.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .runtime import read_json
from .target import Target

HEALTH_COMMAND = ("cilium-health", "status", "-o", "json")

STORAGE_READS: dict[str, tuple[str, bool]] = {
    "nodes": ("nodes", False),
    "pods": ("pods", True),
    "pvcs": ("persistentvolumeclaims", True),
    "pvs": ("persistentvolumes", False),
    "storageclasses": ("storageclasses", False),
    "csidrivers": ("csidrivers", False),
    "csinodes": ("csinodes", False),
    "attachments": ("volumeattachments", False),
    "deployments": ("deployments", True),
    "statefulsets": ("statefulsets", True),
    "daemonsets": ("daemonsets", True),
    "replicasets": ("replicasets", True),
}

EVENT_READS: dict[str, tuple[str, bool]] = {
    "core_events": ("events", True),
    "events_v1": ("events.events.k8s.io", True),
}

CILIUM_READS: dict[str, tuple[str, bool]] = {
    "daemonsets": ("daemonsets", True),
    "pods": ("pods", True),
    "operators": ("deployments", True),
    "ciliumnodes": ("ciliumnodes.cilium.io", False),
}

COLLECTOR_SETS: dict[str, dict[str, tuple[str, bool]]] = {
    "storage": STORAGE_READS,
    "events": EVENT_READS,
    "cilium": CILIUM_READS,
}


def collector_reads() -> dict[str, tuple[str, bool, tuple[str, ...]]]:
    """Return the union of every collector read, tagged with who needs it.

    Keyed by resource rather than by each collector's local alias, because the
    gate verifies a resource once however many collectors want it.
    """
    union: dict[str, tuple[str, bool, tuple[str, ...]]] = {}
    for collector, resources in COLLECTOR_SETS.items():
        for resource, namespaced in resources.values():
            existing = union.get(resource)
            serves = (*existing[2], collector) if existing else (collector,)
            union[resource] = (resource, namespaced, serves)
    return union


def resource_argv(target: Target, resource: str, namespaced: bool) -> list[str]:
    """Build the one list command both the collectors and the gate issue."""
    from .access import kubectl_argv

    return kubectl_argv(target, "get", resource, *(["-A"] if namespaced else []), "-o", "json")


def read_resources(
    target: Target,
    resources: dict[str, tuple[str, bool]],
    *,
    max_workers: int = 10,
) -> dict[str, tuple[Any | None, str | None]]:
    """Read every requested resource concurrently, keyed by the caller's alias."""
    if not resources:
        return {}
    results: dict[str, tuple[Any | None, str | None]] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(resources))) as pool:
        futures = {
            pool.submit(read_json, resource_argv(target, resource, namespaced)): key
            for key, (resource, namespaced) in resources.items()
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return results


def matches_selector(labels: dict[str, str], selector: dict[str, Any]) -> bool:
    """Evaluate a label selector's equality and expression terms."""
    for key, value in (selector.get("matchLabels") or {}).items():
        if labels.get(key) != value:
            return False
    for expression in selector.get("matchExpressions") or []:
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


def agent_selector(daemonsets: list[dict[str, Any]], namespace: str) -> dict[str, Any] | None:
    """Return the `cilium` DaemonSet's own Pod selector for one namespace.

    Selecting agents by this selector rather than by a name prefix keeps
    `cilium-operator-*` and `cilium-envoy-*` out: they share the prefix but are
    not agents and carry no health endpoint.
    """
    selected = [
        item for item in daemonsets
        if (item.get("metadata") or {}).get("namespace") == namespace
        and (item.get("metadata") or {}).get("name") == "cilium"
    ]
    if len(selected) != 1:
        return None
    return (selected[0].get("spec") or {}).get("selector") or None


def ready_agent_pods(
    pods: list[dict[str, Any]],
    selector: dict[str, Any],
    namespace: str,
) -> list[dict[str, Any]]:
    """Return the ready agent Pods the selector claims, in API order."""
    return [
        item for item in pods
        if (item.get("metadata") or {}).get("namespace") == namespace
        and matches_selector((item.get("metadata") or {}).get("labels") or {}, selector)
        and any(condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in (item.get("status") or {}).get("conditions") or [])
    ]
