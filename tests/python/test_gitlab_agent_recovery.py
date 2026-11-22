"""Offline native ConfigMap recovery contracts for one GitLab Agent run."""

from __future__ import annotations

import json
from dataclasses import replace
from enum import StrEnum
from types import SimpleNamespace
from typing import Final
from unittest.mock import Mock, call, create_autospec

import pytest
from conftest import import_script_module

RECOVERY = import_script_module("lib.python.core.gitlab.cluster_agent.recovery")
SELECTION = import_script_module("lib.python.core.gitlab.cluster_agent.selection")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
SCOPE = import_script_module("lib.python.core.kubernetes.run_scope")
RESOURCES = import_script_module("lib.python.core.kubernetes.resources")
RUNTIME = import_script_module("lib.python.core.runtime")

PROJECT_ID: Final = 42
AGENT_ID: Final = 17
PROJECT: Final = "group/agent-consumer"
ORIGIN: Final = "https://gitlab.example.test"
AGENT: Final = "consumer-agent"
RUN_ID: Final = "owned-agent-run"
RELEASE: Final = "owned-agent-release"
NAMESPACE: Final = "consumer-agent-system"
CLUSTER: Final = "kube-system"
CLUSTER_UID: Final = "cluster-native-uid"
NAMESPACE_UID: Final = "selected-namespace-uid"
RECORD_UID: Final = "owned-configmap-uid"
OTHER_UID: Final = "replacement-configmap-uid"
CREATE_VERSION: Final = "120"
NEXT_VERSION: Final = "121"
ROLE: Final = "agent-recovery"
CONFIG_MAP: Final = "ConfigMap"
NAMESPACE_KIND: Final = "Namespace"
OWNERSHIP_KEY: Final = "ownership.json"
METADATA: Final = "metadata"
DATA: Final = "data"
UID: Final = "uid"
RESOURCE_VERSION: Final = "resourceVersion"
RECORD_UID_FIELD: Final = "ownership_record_uid"
PENDING: Final = "PENDING"
KIND: Final = "kind"
API_VERSION: Final = "apiVersion"
NAME: Final = "name"
NATIVE_INVALID: Final = "agent_recovery_native_invalid"
SCOPE_MISMATCH: Final = "agent_recovery_scope_mismatch"
DOCUMENT_INVALID: Final = "agent_recovery_document_invalid"
UID_MISMATCH: Final = "agent_recovery_resource_uid_mismatch"
WRITE_UNCONFIRMED: Final = "agent_recovery_write_unconfirmed"
READBACK_MISMATCH: Final = "agent_recovery_readback_mismatch"
POST: Final = "create"
PUT: Final = "replace"
SCOPE_CASE = SCOPE.RunScope(RUN_ID, NAMESPACE, False)
SELECTION_CASE = SELECTION.ConnectSelection(
    project=SELECTION.AgentProject(PROJECT_ID, PROJECT),
    agent_name=AGENT,
    scope=SCOPE_CASE,
    release=RELEASE,
)
RECORD_NAME: Final = SCOPE_CASE.bounded_name(
    ROLE, limit=SCOPE.DNS_LABEL_LIMIT, prefix=AGENT
)
COLLECTION_PATH: Final = f"/api/v1/namespaces/{NAMESPACE}/configmaps"
ITEM_PATH: Final = f"{COLLECTION_PATH}/{RECORD_NAME}"
PENDING_ROW: Final = "pending"
CREATED_ROW: Final = "created"
UPDATED_ROW: Final = "updated"
NATIVE_ROW: Final = "native"
LABELS: Final = {
    SCOPE.RUN_ID_LABEL: RUN_ID,
    RESOURCES.MANAGER_LABEL: RESOURCES.MANAGER_VALUE,
    RESOURCES.OPERATION_LABEL: RUN_ID,
}


def _configmap(document: dict[str, object], version: str) -> dict[str, object]:
    """Encode the payload and independently assigned native identity."""
    return {
        API_VERSION: "v1",
        KIND: CONFIG_MAP,
        METADATA: {
            NAME: RECORD_NAME,
            "namespace": NAMESPACE,
            "labels": LABELS,
            UID: RECORD_UID,
            RESOURCE_VERSION: version,
        },
        DATA: {OWNERSHIP_KEY: json.dumps(document)},
    }


@pytest.fixture(scope="module")
def recovery_baseline() -> str:
    """One immutable schema-valid pending, read-back, and update response set."""
    pending = {
        "schema_version": "1.0",
        KIND: "gitlab_agent_ownership",
        "run_id": RUN_ID,
        "origin": ORIGIN,
        "project_id": PROJECT_ID,
        "project_path": PROJECT,
        "agent_id": None,
        "agent_name": AGENT,
        "registration_credential_id": None,
        "registration_credential_name": "owned-agent-credential",
        "preexisting_registration_credential_ids": [],
        "cluster_uid": CLUSTER_UID,
        "namespace": NAMESPACE,
        "namespace_uid": NAMESPACE_UID,
        "credential_resource_name": "owned-agent-secret",
        "credential_resource_uid": None,
        "ownership_record_name": RECORD_NAME,
        RECORD_UID_FIELD: None,
        "release": RELEASE,
        "release_revision": None,
        "release_storage_name": None,
        "release_storage_uid": None,
        "chart_version": SELECTION.CHART_VERSION,
        "chart_sha256": SELECTION.CHART_SHA256,
        "app_version": SELECTION.APP_VERSION,
        "image_digest": None,
        "created": {
            "agent": None,
            "registration_credential": None,
            "namespace": False,
            "credential_resource": None,
            "release": None,
            "ownership_record": True,
        },
        "state": PENDING,
        "resources": [],
    }
    updated = {
        **pending,
        RECORD_UID_FIELD: RECORD_UID,
        "agent_id": AGENT_ID,
        "created": {**pending["created"], "agent": True},
    }
    return json.dumps(
        {
            PENDING_ROW: pending,
            CREATED_ROW: _configmap(pending, CREATE_VERSION),
            UPDATED_ROW: updated,
            NATIVE_ROW: _configmap(updated, NEXT_VERSION),
        }
    )


@pytest.fixture
def recovery_service() -> tuple[RECOVERY.AgentRecoveryStore, Mock]:
    """Bind the store to a fake transport that can only supply native observations."""
    service = create_autospec(KUBERNETES.KubernetesService, instance=True)
    return (
        RECOVERY.AgentRecoveryStore(
            service, SELECTION_CASE, ORIGIN, CLUSTER_UID, NAMESPACE_UID
        ),
        service,
    )


def _observe(service: Mock, configmap: dict[str, object] | None) -> None:
    """Route reads for the exact ConfigMap and the two authoritative namespaces."""
    objects = {
        (CONFIG_MAP, RECORD_NAME, NAMESPACE): configmap,
        **{
            (NAMESPACE_KIND, name, ""): {
                API_VERSION: "v1",
                KIND: NAMESPACE_KIND,
                METADATA: {NAME: name, UID: uid},
            }
            for name, uid in ((CLUSTER, CLUSTER_UID), (NAMESPACE, NAMESPACE_UID))
        },
    }
    service.read.side_effect = lambda *identity: objects[identity]


def _acknowledge(
    service: Mock,
    response: dict[str, object],
    readback: dict[str, object] | None = None,
) -> None:
    """Report one native acknowledgement and expose an independent read result."""

    def write(*args: str, **_kwargs: object) -> RUNTIME.CommandResult:
        _observe(service, response if readback is None else readback)
        return RUNTIME.CommandResult(args, 0, json.dumps(response), "")

    service.command.side_effect = write


def _request_metadata(
    service: Mock, method: str, endpoint: str, document: dict[str, object]
) -> dict[str, object]:
    """Require one native verb and prove its complete stdin ownership document."""
    service.command.assert_called_once()
    args, kwargs = service.command.call_args
    assert args == (method, "--raw", endpoint, "-f", "-")
    payload = json.loads(kwargs["input_data"])
    assert payload[API_VERSION] == "v1"
    assert payload[KIND] == CONFIG_MAP
    assert set(payload[DATA]) == {OWNERSHIP_KEY}
    assert json.loads(payload[DATA][OWNERSHIP_KEY]) == document
    metadata = payload[METADATA]
    assert metadata[NAME] == RECORD_NAME
    assert metadata["namespace"] == NAMESPACE
    assert metadata["labels"] == LABELS
    last_command = next(
        index for index, item in enumerate(service.mock_calls) if item[0] == "command"
    )
    assert (
        call.read(CONFIG_MAP, RECORD_NAME, NAMESPACE)
        in service.mock_calls[last_command + 1 :]
    )
    service.apply.assert_not_called()
    service.delete.assert_not_called()
    return metadata


def test_recovery_creates_pending_then_updates_only_the_observed_native_identity(
    recovery_baseline: str, recovery_service: tuple[RECOVERY.AgentRecoveryStore, Mock]
) -> None:
    """Distinguish the initial null self-UID from the compare-and-swap update."""
    wire = json.loads(recovery_baseline)
    store, service = recovery_service
    _observe(service, None)
    _acknowledge(service, wire[CREATED_ROW])

    assert store.name == RECORD_NAME
    assert store.labels == LABELS
    created = store.create_pending(wire[PENDING_ROW])
    assert created == RECOVERY.RecoveryRecord(
        RECORD_UID, CREATE_VERSION, wire[PENDING_ROW]
    )
    metadata = _request_metadata(service, POST, COLLECTION_PATH, wire[PENDING_ROW])
    assert UID not in metadata
    assert RESOURCE_VERSION not in metadata

    service.reset_mock()
    assert store.read() == created
    service.command.assert_not_called()
    service.reset_mock()
    _acknowledge(service, wire[NATIVE_ROW])

    updated = store.replace(created, wire[UPDATED_ROW])

    assert updated == RECOVERY.RecoveryRecord(
        RECORD_UID, NEXT_VERSION, wire[UPDATED_ROW]
    )
    metadata = _request_metadata(service, PUT, ITEM_PATH, wire[UPDATED_ROW])
    assert metadata[UID] == RECORD_UID
    assert metadata[RESOURCE_VERSION] == CREATE_VERSION


class Boundary(StrEnum):
    """Identify the input that carries a single changed invariant."""

    NATIVE = "native"
    PAYLOAD = "payload"
    REPLACE = "replace"


@pytest.mark.parametrize(
    ("boundary", "path", "value", "reason"),
    (
        (Boundary.NATIVE, (KIND,), "Secret", NATIVE_INVALID),
        (Boundary.NATIVE, (API_VERSION,), "v2", NATIVE_INVALID),
        (Boundary.NATIVE, (METADATA, NAME), "other-record", NATIVE_INVALID),
        (Boundary.NATIVE, (METADATA, "namespace"), "other-namespace", NATIVE_INVALID),
        (
            Boundary.NATIVE,
            (METADATA, "deletionTimestamp"),
            "2026-01-01T01:00:00Z",
            NATIVE_INVALID,
        ),
        (
            Boundary.NATIVE,
            (METADATA, "labels", SCOPE.RUN_ID_LABEL),
            "other-run",
            NATIVE_INVALID,
        ),
        (
            Boundary.NATIVE,
            (METADATA, "labels", RESOURCES.MANAGER_LABEL),
            "other",
            NATIVE_INVALID,
        ),
        (
            Boundary.NATIVE,
            (METADATA, "labels", RESOURCES.OPERATION_LABEL),
            "other-run",
            NATIVE_INVALID,
        ),
        (Boundary.NATIVE, (METADATA, RESOURCE_VERSION), "", NATIVE_INVALID),
        (Boundary.NATIVE, (METADATA, UID), OTHER_UID, UID_MISMATCH),
        (Boundary.PAYLOAD, ("origin",), "https://other.example.test", SCOPE_MISMATCH),
        (Boundary.PAYLOAD, ("project_id",), PROJECT_ID + 1, SCOPE_MISMATCH),
        (Boundary.PAYLOAD, ("cluster_uid",), OTHER_UID, SCOPE_MISMATCH),
        (Boundary.PAYLOAD, ("namespace_uid",), OTHER_UID, SCOPE_MISMATCH),
        (Boundary.PAYLOAD, ("unexpected",), False, DOCUMENT_INVALID),
        (Boundary.REPLACE, ("agent_id",), AGENT_ID + 1, UID_MISMATCH),
    ),
    ids=(
        "wrong-kind",
        "wrong-version",
        "wrong-name",
        "wrong-namespace",
        "deleting-configmap",
        "wrong-run-label",
        "wrong-manager-label",
        "wrong-operation-label",
        "missing-native-version",
        "replaced-native-uid",
        "foreign-origin",
        "foreign-project",
        "replaced-cluster",
        "replaced-namespace",
        "unknown-payload-field",
        "replace-established-agent-id",
    ),
)
def test_recovery_rejects_one_native_or_ownership_invariant(
    recovery_baseline: str,
    recovery_service: tuple[RECOVERY.AgentRecoveryStore, Mock],
    boundary: Boundary,
    path: tuple[str, ...],
    value: object,
    reason: str,
) -> None:
    """Change one field while retaining all other independently observed identities."""
    wire = json.loads(recovery_baseline)
    store, service = recovery_service
    native = wire[NATIVE_ROW]
    document = json.loads(native[DATA][OWNERSHIP_KEY])
    target = native if boundary is Boundary.NATIVE else document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    if boundary is Boundary.PAYLOAD:
        native[DATA][OWNERSHIP_KEY] = json.dumps(document)
    _observe(service, native)
    if boundary is Boundary.REPLACE:
        previous = store.read()
        assert previous is not None
        service.reset_mock()

    with pytest.raises(KUBERNETES.KubernetesError) as rejected:
        if boundary is Boundary.REPLACE:
            store.replace(previous, document)
        else:
            store.read()

    assert str(rejected.value) == reason
    if boundary is Boundary.REPLACE:
        assert service.mock_calls == []
    else:
        assert call.read(CONFIG_MAP, RECORD_NAME, NAMESPACE) in service.mock_calls
        assert all(item[0] == "read" for item in service.mock_calls)


@pytest.mark.parametrize("method", (POST, PUT), ids=("already-exists", "stale-rv"))
def test_recovery_does_not_retry_a_conflicting_native_write(
    recovery_baseline: str,
    recovery_service: tuple[RECOVERY.AgentRecoveryStore, Mock],
    method: str,
) -> None:
    """Preserve the create-only or compare-and-swap boundary after conflict."""
    wire = json.loads(recovery_baseline)
    store, service = recovery_service
    _observe(service, wire[CREATED_ROW] if method == PUT else None)
    previous = store.read()
    service.reset_mock()
    service.command.return_value = RUNTIME.CommandResult((), 1, "", "Conflict")

    with pytest.raises(KUBERNETES.KubernetesError) as rejected:
        if method == PUT:
            assert previous is not None
            store.replace(previous, wire[UPDATED_ROW])
        else:
            assert previous is None
            store.create_pending(wire[PENDING_ROW])

    assert str(rejected.value) == WRITE_UNCONFIRMED
    service.command.assert_called_once()
    args, kwargs = service.command.call_args
    endpoint = ITEM_PATH if method == PUT else COLLECTION_PATH
    assert args == (method, "--raw", endpoint, "-f", "-")
    metadata = json.loads(kwargs["input_data"])[METADATA]
    if method == PUT:
        assert (metadata[UID], metadata[RESOURCE_VERSION]) == (
            RECORD_UID,
            CREATE_VERSION,
        )
    else:
        assert UID not in metadata
        assert RESOURCE_VERSION not in metadata
    service.apply.assert_not_called()
    service.delete.assert_not_called()


def test_recovery_create_requires_matching_independent_payload_readback(
    recovery_baseline: str, recovery_service: tuple[RECOVERY.AgentRecoveryStore, Mock]
) -> None:
    """Reject a valid acknowledgement followed by a different native document."""
    wire = json.loads(recovery_baseline)
    store, service = recovery_service
    _observe(service, None)
    acknowledged = wire[CREATED_ROW]
    changed = {**wire[PENDING_ROW], RECORD_UID_FIELD: RECORD_UID}
    _acknowledge(service, acknowledged, _configmap(changed, CREATE_VERSION))

    with pytest.raises(KUBERNETES.KubernetesError) as rejected:
        store.create_pending(wire[PENDING_ROW])

    assert str(rejected.value) == READBACK_MISMATCH
    _request_metadata(service, POST, COLLECTION_PATH, wire[PENDING_ROW])


@pytest.mark.parametrize(
    ("changed", "reason"),
    (
        (None, None),
        ("empty", None),
        ("duplicate", "agent_recovery_inventory_ambiguous"),
        ("incomplete", "agent_recovery_inventory_unverified"),
        ("agent_selector", SCOPE_MISMATCH),
        ("namespace_selector", SCOPE_MISMATCH),
        ("namespace_uid", SCOPE_MISMATCH),
        ("resource_version", READBACK_MISMATCH),
        ("listed_origin", SCOPE_MISMATCH),
    ),
)
def test_locate_agent_run_rechecks_recorded_native_scope(
    recovery_baseline: str,
    monkeypatch: pytest.MonkeyPatch,
    changed: str | None,
    reason: str | None,
) -> None:
    """Isolate one inventory, selector, or direct read-back change per case."""
    journal = json.loads(recovery_baseline)[UPDATED_ROW]
    selected = replace(
        SELECTION_CASE, release=SELECTION.release_name(SCOPE_CASE, AGENT)
    )
    journal["release"] = selected.release
    listed = _configmap(journal, NEXT_VERSION)
    observed = _configmap(journal, NEXT_VERSION)
    items = [listed]
    metadata = {}
    namespace_uid = NAMESPACE_UID
    agent_name = None
    namespace = None
    if changed == "empty":
        items.clear()
    elif changed == "duplicate":
        items.append(_configmap(journal, NEXT_VERSION))
    elif changed == "incomplete":
        metadata["continue"] = "fixture-offset"
    elif changed == "agent_selector":
        agent_name = "different-agent"
    elif changed == "namespace_selector":
        namespace = "different-namespace"
    elif changed == "namespace_uid":
        namespace_uid = OTHER_UID
    elif changed == "resource_version":
        observed["metadata"]["resourceVersion"] = "123"
    elif changed == "listed_origin":
        listed["data"][OWNERSHIP_KEY] = json.dumps(
            {**journal, "origin": "https://other.example.test"}
        )
    kube = create_autospec(KUBERNETES.KubernetesService, instance=True)
    kube.json.return_value = {
        "apiVersion": "v1",
        "kind": "List",
        "items": items,
        "metadata": metadata,
    }
    kube.read.return_value = observed
    scope = Mock(return_value=(CLUSTER_UID, namespace_uid))
    monkeypatch.setattr(RECOVERY, "read_agent_cluster_scope", scope)
    authorities = RECOVERY.AgentAuthorities(
        SimpleNamespace(session=SimpleNamespace(origin=ORIGIN)),
        Mock(),
        kube,
        selected.project,
        Mock(),
        CLUSTER_UID,
    )
    options = {
        "agent_name": agent_name,
        "namespace": namespace,
        "deadline": RECOVERY.RequestDeadline(30, monotonic=lambda: 0),
    }
    if reason is not None:
        with pytest.raises(KUBERNETES.KubernetesError, match=reason):
            RECOVERY.locate_agent_run(authorities, RUN_ID, **options)
    else:
        result = RECOVERY.locate_agent_run(authorities, RUN_ID, **options)
        if changed == "empty":
            assert result is None
            kube.read.assert_not_called()
        else:
            assert result == (
                selected,
                RECOVERY.RecoveryRecord(RECORD_UID, NEXT_VERSION, journal),
            )
            kube.read.assert_called_once_with(CONFIG_MAP, RECORD_NAME, NAMESPACE)
            scope.assert_called_once_with(kube, NAMESPACE)
    query = kube.json.call_args.args
    assert query[:4] == ("get", "configmaps", "--all-namespaces", "--chunk-size=0")
    assert set(query[5].split(",")) == {
        f"{key}={value}" for key, value in LABELS.items()
    }
    kube.apply.assert_not_called()
    kube.delete.assert_not_called()
