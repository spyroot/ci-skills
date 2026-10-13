"""One declaration of how a Cilium agent is identified and what health means.

The access preflight and the Cilium collector both need to answer two
questions: which Pods are agents, and whether a health response actually
proves the exec capability. Answering them twice let the preflight select a
`cilium-operator-*` Pod by name prefix while the collector selected agents by
the DaemonSet's own selector, and let any parseable JSON -- `null`, `[]`, a
bare string -- count as a passing health read.

Nothing here builds or runs a command, so both callers can import it without a
cycle.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping, MutableSequence, Sequence
from typing import Optional, TypeGuard

from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.runtime import sanitize

HEALTH_COMMAND = ("cilium-health", "status", "-o", "json")
AGENT_DAEMONSET = "cilium"
HEALTHY_STATES = frozenset({"ok", "reachable", "healthy", "success"})


def valid_selector(selector: JSONValue) -> TypeGuard[JSONObject]:
    """Validate the label terms used to identify Cilium agent Pods.

    :param selector: Native Kubernetes label-selector JSON value.
    :returns: Whether at least one label or expression is present and every
        term uses supported Kubernetes keys, operators, and string values.
    """
    if not isinstance(selector, Mapping):
        return False
    labels = selector.get("matchLabels", {})
    expressions = selector.get("matchExpressions", [])
    if not isinstance(labels, Mapping) or not isinstance(expressions, list):
        return False
    if not labels and not expressions:
        return False
    if any(not key or not isinstance(value, str) for key, value in labels.items()):
        return False
    for expression in expressions:
        if not isinstance(expression, Mapping):
            return False
        key = expression.get("key")
        operator = expression.get("operator")
        values = expression.get("values", [])
        if not isinstance(key, str) or not key or not isinstance(operator, str):
            return False
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            return False
        if operator in {"In", "NotIn"}:
            if not values:
                return False
        elif operator in {"Exists", "DoesNotExist"}:
            if values:
                return False
        else:
            return False
    return True


def matches_selector(labels: JSONObject, selector: JSONObject) -> bool:
    """Match Pod labels against a validated DaemonSet selector.

    :param labels: Native Pod label mapping; nonstring label values fail closed.
    :param selector: Kubernetes matchLabels and matchExpressions declaration.
    :returns: Whether all terms match; empty or malformed selectors match nothing.
    """
    if not valid_selector(selector) or any(
        not isinstance(value, str) for value in labels.values()
    ):
        return False
    match_labels = selector.get("matchLabels", {})
    expressions = selector.get("matchExpressions", [])
    if not isinstance(match_labels, Mapping) or not isinstance(expressions, list):
        return False
    if any(labels.get(key) != value for key, value in match_labels.items()):
        return False
    for expression in expressions:
        if not isinstance(expression, Mapping):
            return False
        key, operator = expression["key"], expression["operator"]
        values = expression.get("values", [])
        if not isinstance(key, str) or not isinstance(values, list):
            return False
        if operator == "In" and labels.get(key) not in values:
            return False
        if operator == "NotIn" and labels.get(key) in values:
            return False
        if operator == "Exists" and key not in labels:
            return False
        if operator == "DoesNotExist" and key in labels:
            return False
    return True


def agent_selector(
    daemonsets: Sequence[JSONObject], namespace: str
) -> Optional[JSONObject]:
    """Read the unique Cilium agent DaemonSet's Pod selector.

    :param daemonsets: DaemonSet objects returned by the Kubernetes API.
    :param namespace: Namespace containing the expected Cilium DaemonSet.
    :returns: Its validated selector, or None when missing, ambiguous, or malformed.
    """
    selected: MutableSequence[KubernetesObject] = []
    try:
        for document in daemonsets:
            daemonset = KubernetesObject(document)
            if (
                daemonset.metadata.text("namespace") == namespace
                and daemonset.metadata.text("name") == AGENT_DAEMONSET
            ):
                selected.append(daemonset)
        if len(selected) != 1:
            return None
        selector = selected[0].object("spec").document.get("selector")
    except TypeError:
        return None
    return selector if valid_selector(selector) else None


def is_ready(pod: JSONObject) -> bool:
    """Check a Pod's explicit Ready condition without coercing malformed fields.

    :param pod: Kubernetes Pod object.
    :returns: Whether a condition is Ready=True; malformed conditions return false.
    """
    try:
        conditions = KubernetesObject(pod).object("status").objects("conditions")
        return any(
            condition.text("type") == "Ready" and condition.text("status") == "True"
            for condition in conditions
        )
    except TypeError:
        return False


def ready_agent_pods(
    pods: Sequence[JSONObject],
    selector: JSONObject,
    namespace: str,
    node: Optional[str] = None,
) -> Sequence[JSONObject]:
    """Select ready agent Pods in their API response order.

    :param pods: Candidate Kubernetes Pod objects.
    :param selector: Pod selector read from the agent DaemonSet.
    :param namespace: Required Pod namespace.
    :param node: Optional exact node name limiting selection.
    :returns: Matching ready Pods; malformed metadata or spec fields are excluded.
    """
    selected: MutableSequence[JSONObject] = []
    for document in pods:
        pod = KubernetesObject(document)
        try:
            if (
                pod.metadata.text("namespace") == namespace
                and matches_selector(pod.metadata.object("labels").document, selector)
                and (not node or pod.object("spec").text("nodeName") == node)
                and is_ready(document)
            ):
                selected.append(document)
        except TypeError:
            continue
    return selected


def valid_health(value: JSONValue) -> TypeGuard[JSONObject]:
    """Check whether decoded JSON has the shape of a Cilium health response.

    :param value: Decoded response from ``cilium-health status -o json``.
    :returns: Whether it is an object containing a ``local`` object or ``nodes``
        list; a scalar, empty object, or unrelated JSON fails.
    """
    if not isinstance(value, Mapping):
        return False
    return isinstance(value.get("local"), Mapping) or isinstance(
        value.get("nodes"), list
    )


def health_detail(value: JSONValue) -> JSONObject:
    """Extract optional local name and peer count from Cilium health data.

    :param value: Decoded health response; malformed parts are treated as absent.
    :returns: Mapping with ``local_node`` and ``peer_count`` values or ``None``.
    """
    local = value.get("local") if isinstance(value, Mapping) else None
    peers = value.get("nodes") if isinstance(value, Mapping) else None
    name = local.get("name") if isinstance(local, Mapping) else None
    return {
        "local_node": name if isinstance(name, str) and name.strip() else None,
        "peer_count": len(peers) if isinstance(peers, list) else None,
    }


def _failure_messages(value: JSONValue, path: str) -> Sequence[tuple[str, str]]:
    """Find explicit nonhealthy status messages in nested health data.

    :param value: Nested Cilium health object or list to inspect.
    :param path: Diagnostic path prefix for findings.
    :returns: ``(path, sanitized status)`` pairs; missing statuses are ignored.
    """
    if isinstance(value, list):
        return [
            finding
            for index, item in enumerate(value)
            for finding in _failure_messages(item, f"{path}[{index}]")
        ]
    if not isinstance(value, Mapping):
        return []
    findings: MutableSequence[tuple[str, str]] = []
    status = value.get("status")
    if (
        isinstance(status, str)
        and status.strip()
        and status.strip().casefold() not in HEALTHY_STATES
    ):
        findings.append((f"{path}.status", sanitize(status.strip(), 240)))
    for key, item in value.items():
        if key != "status" and isinstance(item, (Mapping, list)):
            findings.extend(_failure_messages(item, f"{path}.{key}"))
    return findings


def health_findings(value: JSONValue) -> Sequence[JSONObject]:
    """Report explicit local and peer failures from Cilium health data.

    :param value: Decoded Cilium health response.
    :returns: Finding records with component, path, message, and action;
        malformed or absent sections contribute no findings.
    """
    if not isinstance(value, Mapping):
        return []
    findings: MutableSequence[JSONObject] = [
        {
            "component": "local",
            "path": path,
            "message": message,
            "action": "inspect_cilium_local_health",
        }
        for path, message in _failure_messages(value.get("local"), "local")
    ]
    peers = value.get("nodes")
    if not isinstance(peers, list):
        return findings
    for index, peer in enumerate(peers):
        if not isinstance(peer, Mapping):
            continue
        name = peer.get("name")
        for path, message in _failure_messages(peer, "peer"):
            findings.append(
                {
                    "component": path.split(".")[1].split("[")[0],
                    "peer": sanitize(name, 240) if isinstance(name, str) else None,
                    "peer_index": index,
                    "path": path,
                    "message": message,
                    "action": "inspect_cilium_peer_connectivity",
                }
            )
    return findings


def daemon_findings(value: JSONValue) -> Sequence[JSONObject]:
    """Report Cilium daemon components whose explicit state needs inspection.

    :param value: Decoded daemon status object keyed by component name.
    :returns: Component, state, optional message, and action records; ``ok``
        and disabled optional components are omitted.
    """
    if not isinstance(value, Mapping):
        return []
    findings: MutableSequence[JSONObject] = []
    for name, component in value.items():
        if not isinstance(component, Mapping):
            continue
        state = component.get("state")
        if not isinstance(state, str) or not state.strip():
            continue
        normalized = state.strip().casefold()
        if normalized == "ok" or (normalized == "disabled" and name != "cilium"):
            continue
        message = component.get("msg")
        findings.append(
            {
                "component": name,
                "state": sanitize(state.strip(), 80),
                "message": sanitize(message, 240) if isinstance(message, str) else None,
                "action": "inspect_cilium_daemon_component",
            }
        )
    return findings


def valid_daemon_status(value: JSONValue) -> TypeGuard[JSONObject]:
    """Check that a Cilium daemon response reports its own component state.

    :param value: Decoded cilium-dbg status response.
    :returns: Whether cilium.state is a nonempty string.
    """
    if not isinstance(value, Mapping):
        return False
    component = value.get("cilium")
    if not isinstance(component, Mapping):
        return False
    state = component.get("state")
    return isinstance(state, str) and bool(state.strip())


def valid_node_health(value: JSONValue) -> TypeGuard[JSONObject]:
    """Check that Cilium health identifies its local node.

    :param value: Decoded cilium-health status response.
    :returns: Whether structured health contains a nonempty local.name string.
    """
    if not valid_health(value):
        return False
    local = value.get("local")
    if not isinstance(local, Mapping):
        return False
    name = local.get("name")
    return isinstance(name, str) and bool(name.strip())
