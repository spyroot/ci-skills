"""Verify runner namespace ownership and GitLab protection read-back offline.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from argparse import Namespace
from typing import TYPE_CHECKING, Final
from unittest.mock import Mock

import pytest
from conftest import import_script_module
from test_gitlab_runner_lifecycle import FakeAPI, _bound_session, _list, _plan, _runner
from test_tool_pod import MemoryCluster

NAMESPACES = import_script_module("lib.python.core.kubernetes.runner_scope")
SCOPES = import_script_module("lib.python.core.runner_scope")
WORKFLOWS = import_script_module("lib.python.core.toolbox_actions")
RUNNER = import_script_module("lib.python.core.runner")
RUNNERS = import_script_module("lib.python.core.gitlab.gitlab_runners")
RESOURCES = import_script_module("lib.python.core.kubernetes.resources")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject
    from lib.python.core.toolbox_actions import WorkflowAction

NAMESPACE: Final = "consumer-runner-scope"
NAMESPACE_UID: Final = "unit-namespace-uid"
REPLACEMENT_UID: Final = "replacement-namespace-uid"
OPERATION: Final = "consumer-tools"
NAMESPACE_KIND: Final = "Namespace"
NAMESPACE_KEY: Final = (NAMESPACE_KIND.casefold(), "", NAMESPACE)
RUNNER_ID: Final = 23
PROJECT_ID: Final = 42
RUNNER_ENDPOINT: Final = f"runners/{RUNNER_ID}"
EXPECTED_PROTECTION: Final = "not_protected"


def _action(cluster: MemoryCluster, *, deleting: bool = False) -> WorkflowAction:
    """Bind a real workflow action to the existing in-memory Kubernetes service.

    :param cluster: Offline namespace storage and mutation journal.
    :param deleting: Whether the caller selects the existing delete action.
    :returns: Minimal real action accepted by the namespace callbacks.
    """
    action = WORKFLOWS.WorkflowAction(
        Namespace(action="delete" if deleting else "create", namespace=NAMESPACE)
    )
    action.runner = RUNNER.RunnerAction(
        namespace=NAMESPACE,
        name=OPERATION,
        source_root=RUNNER.RUNTIME_ROOT,
        labels=(),
        timeout=1,
    )
    action.context.kubernetes = cluster
    return action


def _namespace(*, owned: bool) -> JSONObject:
    """Build native namespace state with explicit borrowed or owned metadata.

    :param owned: Whether the namespace carries the operation's ownership labels.
    :returns: Native read-back fixture with an immutable namespace UID.
    """
    labels = (
        {
            RESOURCES.MANAGER_LABEL: RESOURCES.MANAGER_VALUE,
            RESOURCES.OPERATION_LABEL: OPERATION,
        }
        if owned
        else {}
    )
    return {
        "apiVersion": "v1",
        "kind": NAMESPACE_KIND,
        "metadata": {"name": NAMESPACE, "uid": NAMESPACE_UID, "labels": labels},
    }


def test_missing_explicit_namespace_blocks_without_kubernetes_mutation() -> None:
    """Stop before mutation when a caller-selected namespace does not exist."""
    cluster = MemoryCluster()
    action = _action(cluster)
    callback = NAMESPACES.ObserveGitHubNamespace(action)
    callback.bind(action.context)
    with pytest.raises(KUBERNETES.KubernetesError, match="selected_namespace_missing"):
        callback.run()
    assert cluster.applied == []
    assert cluster.deleted == []
    assert action.context.runner_scopes == {}


def test_deleting_runner_preserves_borrowed_namespace_and_uid() -> None:
    """Leave a caller-owned namespace present after runner deletion."""
    cluster = MemoryCluster()
    cluster.objects[NAMESPACE_KEY] = _namespace(owned=False)
    action = _action(cluster, deleting=True)
    before = NAMESPACES.ObserveGitHubNamespace(action)
    before.bind(action.context)
    before.run()
    deletion = NAMESPACES.DeleteGitHubNamespace(action)
    deletion.bind(action.context)
    assert deletion.run()["verified"] is True
    observed = next(iter(action.context.runner_scopes.values()))
    assert observed.uid == NAMESPACE_UID
    assert observed.ownership == SCOPES.ScopeOwnership.BORROWED
    assert observed.state == SCOPES.ScopeState.PRESENT
    assert observed.retention == SCOPES.ScopeRetention.PRESERVED
    assert cluster.objects[NAMESPACE_KEY] == _namespace(owned=False)
    assert cluster.applied == []
    assert cluster.deleted == []


def test_namespace_replacement_blocks_deletion_of_new_uid() -> None:
    """Refuse deletion after the selected namespace has been replaced."""
    cluster = MemoryCluster()
    cluster.objects[NAMESPACE_KEY] = _namespace(owned=True)
    action = _action(cluster, deleting=True)
    before = NAMESPACES.ObserveGitHubNamespace(action)
    before.bind(action.context)
    before.run()
    replacement = _namespace(owned=True)
    replacement["metadata"]["uid"] = REPLACEMENT_UID
    cluster.objects[NAMESPACE_KEY] = replacement
    deletion = NAMESPACES.DeleteGitHubNamespace(action)
    deletion.bind(action.context)
    with pytest.raises(KUBERNETES.KubernetesError, match="namespace_uid_changed"):
        deletion.run()
    assert cluster.objects[NAMESPACE_KEY] == replacement
    assert cluster.deleted == []


def test_created_namespace_cleanup_refuses_replacement_uid() -> None:
    """Keep a replacement namespace intact during cleanup of the original creation."""
    cluster = MemoryCluster()
    resources = RESOURCES.ResourceSet(cluster, "", OPERATION)
    created = resources.apply(
        {
            "apiVersion": "v1",
            "kind": NAMESPACE_KIND,
            "metadata": {"name": NAMESPACE},
        }
    )
    created_uid = created["uid"]
    assert isinstance(created_uid, str)
    replacement = _namespace(owned=True)
    replacement["metadata"]["uid"] = REPLACEMENT_UID
    cluster.objects[NAMESPACE_KEY] = replacement
    with pytest.raises(
        KUBERNETES.KubernetesError, match="resource_delete_identity_mismatch"
    ):
        resources.cleanup_created(
            expected_uids={(NAMESPACE_KIND, NAMESPACE): created_uid}
        )
    assert cluster.objects[NAMESPACE_KEY] == replacement
    assert cluster.deleted == []
    assert resources.created == [(NAMESPACE_KIND, NAMESPACE)]


@pytest.mark.parametrize("removed", (True, False), ids=("removed", "still-present"))
def test_owned_namespace_delete_binds_uid_and_requires_absence_readback(
    monkeypatch: pytest.MonkeyPatch, removed: bool
) -> None:
    """Delete the observed owned object and reject a provider no-effect result.

    :param monkeypatch: Replace only native deletion while retaining real ownership logic.
    :param removed: Whether the provider actually removes the selected namespace.
    """
    cluster = MemoryCluster()
    cluster.objects[NAMESPACE_KEY] = _namespace(owned=True)
    action = _action(cluster, deleting=True)
    before = NAMESPACES.ObserveGitHubNamespace(action)
    before.bind(action.context)
    before.run()
    native_delete = Mock(wraps=cluster.delete) if removed else Mock()
    monkeypatch.setattr(cluster, "delete", native_delete)
    deletion = NAMESPACES.DeleteGitHubNamespace(action)
    deletion.bind(action.context)
    if removed:
        assert deletion.run()["verified"] is True
        observed = next(iter(action.context.runner_scopes.values()))
        assert observed.uid == NAMESPACE_UID
        assert observed.state == SCOPES.ScopeState.ABSENT
        assert observed.cleanup_status == "PASS"
        assert NAMESPACE_KEY not in cluster.objects
    else:
        with pytest.raises(KUBERNETES.KubernetesError):
            deletion.run()
        assert NAMESPACE_KEY in cluster.objects
    native_delete.assert_called_once_with(
        NAMESPACE_KIND, NAMESPACE, "", expected_uid=NAMESPACE_UID
    )
    assert cluster.applied == []


def test_gitlab_runner_protection_mismatch_fails_after_independent_reads() -> None:
    """Reject a runner whose actual protection differs from the explicit choice."""
    plan = _plan("get", {}, RUNNER_ID)
    api = FakeAPI(
        {
            ("GET", RUNNER_ENDPOINT): [_runner(RUNNER_ID)],
            ("GET", _list()): [[{"id": RUNNER_ID}]],
        }
    )
    with pytest.raises(
        RUNNERS.ActionError, match="runner_protection_readback_mismatch"
    ):
        RUNNERS.read_runner_record(
            api,
            _bound_session(plan),
            "project",
            PROJECT_ID,
            RUNNER_ID,
            expected_protection=EXPECTED_PROTECTION,
        )
    assert api.calls == [("GET", RUNNER_ENDPOINT), ("GET", _list())]
