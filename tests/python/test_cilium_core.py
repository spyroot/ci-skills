"""Tests for shared Cilium agent discovery and health-response validation.

Two defects are pinned here. Selecting agents by the `cilium-` name prefix also
matched `cilium-operator-*` and `cilium-envoy-*`, which carry no health
endpoint, so a preflight could exec into one and fail despite valid access.
And treating any successful `json.loads` as a passing read let `null`, `[]` and
a bare string count as a proven agent health check.
"""

from __future__ import annotations

import pytest
from conftest import import_script_module

READY = {"conditions": [{"type": "Ready", "status": "True"}]}
NOT_READY = {"conditions": [{"type": "Ready", "status": "False"}]}
AGENT_LABELS = {"k8s-app": "cilium"}


def _cilium():
    return import_script_module("core.cilium")


def _pod(name, labels, status=None, namespace="cilium", node="n1"):
    return {
        "metadata": {"namespace": namespace, "name": name, "labels": labels},
        "spec": {"nodeName": node},
        "status": status if status is not None else READY,
    }


def _daemonset(selector, namespace="cilium", name="cilium"):
    item = {"metadata": {"namespace": namespace, "name": name}}
    if selector is not None:
        item["spec"] = {"selector": selector}
    return item


@pytest.mark.parametrize(
    "value",
    (None, [], "ok", 0, {}, {"unrelated": 1}, {"local": "reachable"}, {"nodes": "x"}),
)
def test_valid_health_rejects_parseable_non_health_responses(value):
    """Parseable JSON is not a health response."""
    assert _cilium().valid_health(value) is False


@pytest.mark.parametrize(
    "value",
    (
        {"local": {"name": "ue-2"}, "nodes": []},
        {"local": {"status": "reachable"}},
        {"nodes": []},
    ),
)
def test_valid_health_accepts_a_structured_response(value):
    """A response carrying `local` or `nodes` reports health."""
    assert _cilium().valid_health(value) is True


def test_health_detail_reports_the_node_name_without_requiring_it():
    """The node name is evidence, not a gate: absent means absent, not invalid."""
    cilium = _cilium()
    captured = {"local": {"name": "ue-2.example.test"}, "nodes": [{}, {}]}

    assert cilium.health_detail(captured) == {
        "local_node": "ue-2.example.test",
        "peer_count": 2,
    }
    assert cilium.valid_health({"local": {"status": "reachable"}}) is True
    assert cilium.health_detail({"local": {"status": "reachable"}}) == {
        "local_node": None,
        "peer_count": None,
    }


def test_health_findings_keep_failed_peer_and_endpoint_paths():
    """A valid response can still contain failed probes on multiple paths."""
    cilium = _cilium()
    response = {
        "local": {"name": "node-a"},
        "nodes": [
            {
                "name": "node-b",
                "host": {"primary-address": {"icmp": {"status": "timeout"}}},
                "endpoint": {"http": {"status": "connection refused"}},
                "health-endpoint": {
                    "secondary-addresses": [{"http": {"status": "probe failed"}}]
                },
            }
        ],
    }

    assert cilium.valid_health(response) is True
    assert cilium.health_findings(response) == [
        {
            "component": "host",
            "peer": "node-b",
            "peer_index": 0,
            "path": "peer.host.primary-address.icmp.status",
            "message": "timeout",
            "action": "inspect_cilium_peer_connectivity",
        },
        {
            "component": "endpoint",
            "peer": "node-b",
            "peer_index": 0,
            "path": "peer.endpoint.http.status",
            "message": "connection refused",
            "action": "inspect_cilium_peer_connectivity",
        },
        {
            "component": "health-endpoint",
            "peer": "node-b",
            "peer_index": 0,
            "path": "peer.health-endpoint.secondary-addresses[0].http.status",
            "message": "probe failed",
            "action": "inspect_cilium_peer_connectivity",
        },
    ]


def test_daemon_findings_report_failed_components_without_optional_disabled_noise():
    cilium = _cilium()
    assert cilium.daemon_findings(
        {
            "cilium": {"state": "Failure", "msg": "agent unavailable"},
            "kubernetes": {"state": "Warning", "msg": "API delayed"},
            "hubble": {"state": "Disabled"},
        }
    ) == [
        {
            "component": "cilium",
            "state": "Failure",
            "message": "agent unavailable",
            "action": "inspect_cilium_daemon_component",
        },
        {
            "component": "kubernetes",
            "state": "Warning",
            "message": "API delayed",
            "action": "inspect_cilium_daemon_component",
        },
    ]


@pytest.mark.parametrize(
    "selector",
    (None, {}, {"matchLabels": {}}, {"matchLabels": {}, "matchExpressions": []}),
)
def test_agent_selector_refuses_a_selector_that_would_claim_every_pod(selector):
    """An empty selector must not become a match-everything selector."""
    assert _cilium().agent_selector([_daemonset(selector)], "cilium") is None


def test_agent_selector_requires_exactly_one_agent_daemonset():
    """Ambiguous or absent discovery fails closed."""
    cilium = _cilium()
    selector = {"matchLabels": AGENT_LABELS}

    assert cilium.agent_selector([], "cilium") is None
    assert cilium.agent_selector([_daemonset(selector, name="other")], "cilium") is None
    assert cilium.agent_selector([_daemonset(selector)], "other") is None
    assert cilium.agent_selector([_daemonset(selector)], "cilium") == selector


def test_ready_agent_pods_excludes_operator_and_envoy_siblings():
    """The prefix siblings share the name but are not agents."""
    cilium = _cilium()
    pods = [
        _pod("cilium-operator-x", {"name": "cilium-operator"}),
        _pod("cilium-envoy-y", {"k8s-app": "cilium-envoy"}),
        _pod("cilium-agent0", AGENT_LABELS),
    ]

    selected = cilium.ready_agent_pods(pods, {"matchLabels": AGENT_LABELS}, "cilium")

    assert [item["metadata"]["name"] for item in selected] == ["cilium-agent0"]


def test_ready_agent_pods_filters_by_readiness_namespace_and_node():
    """Only ready agents in the selected namespace, and on the named node."""
    cilium = _cilium()
    selector = {"matchLabels": AGENT_LABELS}
    pods = [
        _pod("agent-down", AGENT_LABELS, status=NOT_READY),
        _pod("agent-elsewhere", AGENT_LABELS, namespace="other"),
        _pod("agent-n2", AGENT_LABELS, node="n2"),
        _pod("agent-n1", AGENT_LABELS, node="n1"),
    ]

    assert [
        item["metadata"]["name"]
        for item in cilium.ready_agent_pods(pods, selector, "cilium")
    ] == ["agent-n2", "agent-n1"]
    assert [
        item["metadata"]["name"]
        for item in cilium.ready_agent_pods(pods, selector, "cilium", node="n1")
    ] == ["agent-n1"]


def test_match_expressions_are_evaluated_and_unknown_operators_fail_closed():
    """A selector term the code cannot evaluate must not match."""
    cilium = _cilium()

    assert cilium.matches_selector(
        {"tier": "net"}, {"matchExpressions": [{"key": "tier", "operator": "Exists"}]}
    )
    assert not cilium.matches_selector(
        {"tier": "net"},
        {"matchExpressions": [{"key": "tier", "operator": "DoesNotExist"}]},
    )
    assert not cilium.matches_selector(
        {"tier": "net"},
        {"matchExpressions": [{"key": "tier", "operator": "Gt", "values": ["1"]}]},
    )
