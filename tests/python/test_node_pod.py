"""Existing-Pod Kubernetes transport selection and identity checks."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from tests.python.conftest import import_script_module

PODS = import_script_module("core.node_pod")
TARGET = import_script_module("core.target")
RUNTIME = import_script_module("core.runtime")


@pytest.fixture
def node_target(target_file):
    target_file.write_text(
        target_file.read_text(encoding="utf-8")
        + "\n[kubernetes.node_diagnostics]\n"
        + 'node = "node-a"\n'
        + "\n[kubernetes.node_diagnostics.cilium]\n"
        + 'namespace = "cilium"\nselector = "app=cilium"\n'
        + 'container = "cilium-agent"\n'
        + "\n[kubernetes.node_diagnostics.journal]\n"
        + 'namespace = "diagnostics"\nselector = "app=journal"\n'
        + 'container = "journal-reader"\n'
        + 'directory = "/host-journal"\nhost_path = "/var/log/journal"\n',
        encoding="utf-8",
    )
    return TARGET.load_target(target_file)


def _pod(*, journal=False):
    container = "journal-reader" if journal else "cilium-agent"
    namespace = "diagnostics" if journal else "cilium"
    spec = {"nodeName": "node-a", "containers": [{"name": container}]}
    if journal:
        spec["containers"][0]["volumeMounts"] = [
            {"name": "journal", "mountPath": "/host-journal"}
        ]
        spec["volumes"] = [
            {"name": "journal", "hostPath": {"path": "/var/log/journal"}}
        ]
    return {
        "metadata": {
            "name": "selected-pod",
            "uid": "selected-uid",
            "namespace": namespace,
        },
        "spec": spec,
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {"name": container, "state": {"running": {"startedAt": "now"}}}
            ],
        },
    }


def _result(argv, payload):
    return RUNTIME.CommandResult(tuple(argv), 0, json.dumps(payload), "")


def test_selected_cilium_pod_is_pinned_to_node_context_and_uid(
    monkeypatch, node_target
):
    calls = []
    selected = _pod()

    def fake_run(argv, **_kwargs):
        calls.append(tuple(argv))
        if "pods" in argv:
            return _result(argv, {"items": [selected]})
        return _result(argv, selected)

    def fake_tail(argv, **_kwargs):
        calls.append(tuple(argv))
        return _result(argv, {"cilium": {"state": "Ok"}})

    monkeypatch.setattr(PODS, "run_command", fake_run)
    monkeypatch.setattr(PODS, "run_command_tail", fake_tail)

    route = node_target.kubernetes.node_diagnostics.cilium
    reader = PODS.select_node_pod(node_target, route)
    output = reader.run("cilium-dbg", "status", "--output", "json")

    assert output.returncode == 0
    assert reader.evidence()["pod_uid"] == "selected-uid"
    assert any("spec.nodeName=node-a" in call for call in calls)
    assert all("--context" in call and "unit-context" in call for call in calls)
    assert any("exec" in call and "-t" not in call for call in calls)


@pytest.mark.parametrize("items", [[], [_pod(), _pod()]])
def test_zero_or_multiple_pods_block_before_exec(monkeypatch, node_target, items):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(tuple(argv))
        return _result(argv, {"items": items})

    monkeypatch.setattr(PODS, "run_command", fake_run)
    route = node_target.kubernetes.node_diagnostics.cilium

    with pytest.raises(PODS.NodePodError, match="pod_selection"):
        PODS.select_node_pod(node_target, route)
    assert len(calls) == 1
    assert "exec" not in calls[0]


def test_wrong_node_or_nonrunning_pod_blocks(monkeypatch, node_target):
    selected = _pod()
    selected["spec"]["nodeName"] = "node-b"
    monkeypatch.setattr(
        PODS,
        "run_command",
        lambda argv, **_kwargs: _result(argv, {"items": [selected]}),
    )
    route = node_target.kubernetes.node_diagnostics.cilium
    with pytest.raises(PODS.NodePodError, match="wrong_pod_or_container"):
        PODS.select_node_pod(node_target, route)


def test_journal_requires_readback_of_exact_host_mount(monkeypatch, node_target):
    route = node_target.kubernetes.node_diagnostics.journal
    selected = _pod(journal=True)
    selected["spec"]["volumes"][0]["hostPath"]["path"] = "/tmp"

    def fake_run(argv, **_kwargs):
        return _result(argv, {"items": [selected]})

    monkeypatch.setattr(PODS, "run_command", fake_run)
    with pytest.raises(PODS.NodePodError, match="host_directory_not_mounted"):
        PODS.select_node_pod(node_target, route.pod, journal=route)

    selected["spec"]["volumes"][0]["hostPath"]["path"] = "/var/log/journal"
    reader = PODS.select_node_pod(node_target, route.pod, journal=route)
    assert reader.evidence()["namespace"] == "diagnostics"


def test_existing_host_root_mount_can_expose_declared_journal_path(
    monkeypatch, node_target
):
    route = node_target.kubernetes.node_diagnostics.journal
    selected = _pod(journal=True)
    selected["spec"]["containers"][0]["volumeMounts"][0]["mountPath"] = "/host"
    selected["spec"]["volumes"][0]["hostPath"]["path"] = "/"
    selected["spec"]["volumes"][0]["hostPath"]["type"] = "Directory"
    route = TARGET.JournalPodRoute(
        route.pod, "/host/var/log/journal", "/var/log/journal"
    )
    monkeypatch.setattr(
        PODS,
        "run_command",
        lambda argv, **_kwargs: _result(argv, {"items": [selected]}),
    )
    assert PODS.select_node_pod(node_target, route.pod, journal=route).uid == (
        "selected-uid"
    )


def test_uid_change_after_selection_blocks_before_exec(monkeypatch, node_target):
    selected = _pod()
    current = deepcopy(selected)
    current["metadata"]["uid"] = "replacement-uid"

    def fake_run(argv, **_kwargs):
        return _result(argv, {"items": [selected]} if "pods" in argv else current)

    monkeypatch.setattr(PODS, "run_command", fake_run)
    monkeypatch.setattr(
        PODS,
        "run_command_tail",
        lambda *_args, **_kwargs: pytest.fail("executed a replacement Pod"),
    )
    route = node_target.kubernetes.node_diagnostics.cilium
    reader = PODS.select_node_pod(node_target, route)
    with pytest.raises(PODS.NodePodError, match="identity_changed"):
        reader.run("cilium-dbg", "status")
