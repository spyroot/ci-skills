"""Existing-Pod Kubernetes transport selection and identity checks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from lib.python.core.delivery_state import DeliveryState
    from lib.python.core.json_types import JSONObject, JSONValue
    from lib.python.core.kubernetes.kubernetes import KubernetesService
    from lib.python.core.runtime import CommandResult
    from lib.python.core.target import Target

import pytest
from conftest import authenticated_kubernetes_service, import_script_module

PODS = import_script_module("lib.python.core.kubernetes.node_pod")
TARGET = import_script_module("lib.python.core.target")
RUNTIME = import_script_module("lib.python.core.runtime")


@pytest.fixture
def node_service(target_file: Path) -> KubernetesService:
    """Bind existing Cilium and journal Pod routes to retained cluster access.

    :param target_file: Fixture target file receiving node diagnostics fields.
    :returns: Authenticated service with both declared node-local routes.
    """
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
    return authenticated_kubernetes_service(TARGET.load_target(target_file))


@pytest.fixture
def node_target(node_service: KubernetesService) -> Target:
    """Expose the frozen target shared by node-local operations.

    :param node_service: Service owning this test's retained access observation.
    :returns: Exact credential-bound target used by the observation.
    """
    return node_service.target


@pytest.fixture
def node_state(node_service: KubernetesService) -> DeliveryState:
    """Expose the invocation that authenticated the node-local target.

    :param node_service: Service shared by target selection and later operations.
    :returns: Initialized state consumed by the actual provider guards.
    """
    assert node_service.state is not None
    return node_service.state


def _pod(*, journal: bool = False) -> JSONObject:
    """Build a running selected Pod with optional journal host mount.

    :param journal: Whether to use the diagnostics journal route and volume.
    :returns: Pod mapping with node, UID, container, and running status.
    """
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


def _result(argv: Sequence[str], payload: JSONValue) -> RUNTIME.CommandResult:
    """Wrap a JSON Pod or tool response in a successful native result.

    :param argv: Kubernetes command recorded in the result.
    :param payload: JSON-serializable list or resource body.
    :returns: Successful runtime command result.
    """
    return RUNTIME.CommandResult(tuple(argv), 0, json.dumps(payload), "")


def test_selected_cilium_pod_is_pinned_to_node_context_and_uid(
    monkeypatch: pytest.MonkeyPatch, node_target: Target, node_state: DeliveryState
) -> None:
    """Pin Pod exec to selected node, context, and UID without TTY.

    :param monkeypatch: Pytest fixture replacing list, read-back, and exec calls.
    :param node_target: Target declaring the Cilium Pod route.
    :param node_state: Initialized invocation retained for provider operations.

    :returns: None; assertions in the body validate the regression.
    """
    calls = []
    selected = _pod()

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return Pod list or exact Pod detail for the selected route.

        :param argv: Kubernetes list/read command recorded for assertions.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Successful Pod list or detail result.

        """
        calls.append(tuple(argv))
        if "pods" in argv:
            return _result(argv, {"items": [selected]})
        return _result(argv, selected)

    def fake_tail(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Record exec argv and return Cilium daemon status.

        :param argv: Selected Pod exec command.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Successful Cilium status result.

        """
        calls.append(tuple(argv))
        return _result(argv, {"cilium": {"state": "Ok"}})

    monkeypatch.setattr(PODS, "run_command", fake_run)
    monkeypatch.setattr(PODS, "run_command_tail", fake_tail)

    route = node_target.kubernetes.node_diagnostics.cilium
    reader = PODS.select_node_pod(node_target, route, state=node_state)
    output = reader.run("cilium-dbg", "status", "--output", "json")

    assert output.returncode == 0
    assert reader.evidence()["pod_uid"] == "selected-uid"
    assert any("spec.nodeName=node-a" in call for call in calls)
    assert all("--context" in call and "unit-context" in call for call in calls)
    assert any("exec" in call and "-t" not in call for call in calls)


@pytest.mark.parametrize("items", [[], [_pod(), _pod()]])
def test_zero_or_multiple_pods_block_before_exec(
    monkeypatch: pytest.MonkeyPatch,
    node_target: Target,
    node_state: DeliveryState,
    items: Sequence[JSONValue],
) -> None:
    """Reject non-unique Pod selection before any exec command.

    :param monkeypatch: Pytest fixture replacing the Pod list call.
    :param node_target: Target declaring the Cilium Pod route.
    :param node_state: Initialized invocation retained for provider operations.
    :param items: Zero or two matching Pod records.

    :returns: None; assertions in the body validate the regression.
    """
    calls = []

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return the parametrized Pod list and record the sole call.

        :param argv: Kubernetes Pod list command.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Successful list result containing zero or two Pods.

        """
        calls.append(tuple(argv))
        return _result(argv, {"items": items})

    monkeypatch.setattr(PODS, "run_command", fake_run)
    route = node_target.kubernetes.node_diagnostics.cilium

    with pytest.raises(PODS.NodePodError, match="pod_selection"):
        PODS.select_node_pod(node_target, route, state=node_state)
    assert len(calls) == 1
    assert "exec" not in calls[0]


def test_wrong_node_or_nonrunning_pod_blocks(
    monkeypatch: pytest.MonkeyPatch, node_target: Target, node_state: DeliveryState
) -> None:
    """Reject a selected Pod whose node differs from the target route.

    :param monkeypatch: Pytest fixture returning a Pod on another node.
    :param node_target: Target declaring node-a as the Cilium route.
    :param node_state: Initialized invocation retained for provider operations.

    :returns: None; assertions in the body validate the regression.
    """
    selected = _pod()
    selected["spec"]["nodeName"] = "node-b"
    monkeypatch.setattr(
        PODS,
        "run_command",
        lambda argv, **_kwargs: _result(argv, {"items": [selected]}),
    )
    route = node_target.kubernetes.node_diagnostics.cilium
    with pytest.raises(PODS.NodePodError, match="wrong_pod_or_container"):
        PODS.select_node_pod(node_target, route, state=node_state)


def test_journal_requires_readback_of_exact_host_mount(
    monkeypatch: pytest.MonkeyPatch, node_target: Target, node_state: DeliveryState
) -> None:
    """Reject wrong hostPath and accept exact declared journal mount.

    :param monkeypatch: Pytest fixture replacing Pod list/read-back.
    :param node_target: Target declaring the journal Pod and host path.
    :param node_state: Initialized invocation retained for provider operations.

    :returns: None; assertions in the body validate the regression.
    """
    route = node_target.kubernetes.node_diagnostics.journal
    selected = _pod(journal=True)
    selected["spec"]["volumes"][0]["hostPath"]["path"] = "/tmp"

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return the current journal Pod for host-mount comparison.

        :param argv: Kubernetes Pod list/read command.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Successful list containing the selected journal Pod.

        """
        return _result(argv, {"items": [selected]})

    monkeypatch.setattr(PODS, "run_command", fake_run)
    with pytest.raises(PODS.NodePodError, match="host_directory_not_mounted"):
        PODS.select_node_pod(node_target, route.pod, journal=route, state=node_state)

    selected["spec"]["volumes"][0]["hostPath"]["path"] = "/var/log/journal"
    reader = PODS.select_node_pod(
        node_target, route.pod, journal=route, state=node_state
    )
    assert reader.evidence()["namespace"] == "diagnostics"


def test_existing_host_root_mount_can_expose_declared_journal_path(
    monkeypatch: pytest.MonkeyPatch, node_target: Target, node_state: DeliveryState
) -> None:
    """Accept an existing host-root mount exposing the journal subpath.

    :param monkeypatch: Pytest fixture returning a Pod with root hostPath.
    :param node_target: Target declaring the journal host path.
    :param node_state: Initialized invocation retained for provider operations.

    :returns: None; assertions in the body validate the regression.
    """
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
    assert PODS.select_node_pod(
        node_target, route.pod, journal=route, state=node_state
    ).uid == ("selected-uid")


def test_uid_change_after_selection_blocks_before_exec(
    monkeypatch: pytest.MonkeyPatch, node_target: Target, node_state: DeliveryState
) -> None:
    """Block Pod replacement detected between selection and exec.

    :param monkeypatch: Pytest fixture returning mismatched detail UID.
    :param node_target: Target declaring the Cilium Pod route.
    :param node_state: Initialized invocation retained for provider operations.

    :returns: None; assertions in the body validate the regression.
    """
    selected = _pod()
    current = deepcopy(selected)
    current["metadata"]["uid"] = "replacement-uid"

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> CommandResult:
        """Return original Pod list then replacement UID on detail read.

        :param argv: Kubernetes list or exact-resource read command.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Native result containing selected or replacement Pod.

        """
        return _result(argv, {"items": [selected]} if "pods" in argv else current)

    monkeypatch.setattr(PODS, "run_command", fake_run)
    monkeypatch.setattr(
        PODS,
        "run_command_tail",
        lambda *_args, **_kwargs: pytest.fail("executed a replacement Pod"),
    )
    route = node_target.kubernetes.node_diagnostics.cilium
    reader = PODS.select_node_pod(node_target, route, state=node_state)
    with pytest.raises(PODS.NodePodError, match="identity_changed"):
        reader.run("cilium-dbg", "status")
