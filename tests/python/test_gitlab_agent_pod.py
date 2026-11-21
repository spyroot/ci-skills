"""Offline correlation of a native KAS connection to an owned Agent Pod."""

from __future__ import annotations

import json
from typing import Final
from unittest.mock import Mock, call, create_autospec

import pytest
from conftest import import_script_module

AGENT = import_script_module("lib.python.core.kubernetes.gitlab_agent")

POD: Final = "Pod"
REPLICA_SET: Final = AGENT.REPLICA_SET_KIND
DEPLOYMENT: Final = "Deployment"
LEASE: Final = AGENT.LEASE_KIND
CONNECTION: Final = "connection"
METADATA: Final = "metadata"
NAMESPACE: Final = "agent-system"
OTHER_NAMESPACE: Final = "other-agent-system"
POD_NAME: Final = "agent-abc-pod"
REPLICA_SET_NAME: Final = "agent-abc"
DEPLOYMENT_NAME: Final = "agent"
POD_UID: Final = "owned-pod-uid"
REPLICA_SET_UID: Final = "owned-replica-set-uid"
DEPLOYMENT_UID: Final = "owned-deployment-uid"
REPLACEMENT_UID: Final = "recreated-controller-uid"
KIND: Final = "kind"
NAME: Final = "name"
UID: Final = "uid"
NAMESPACE_FIELD: Final = "namespace"
NAMESPACE_KIND: Final = "Namespace"
CLUSTER_ID_NAMESPACE: Final = "kube-system"
CLUSTER_UID: Final = "cluster-uid"
NAMESPACE_UID: Final = "agent-namespace-uid"
BORN: Final = "creationTimestamp"
CONTAINER: Final = "agentk"
IMAGE_REPOSITORY: Final = "registry.example.test/agentk"
INDEX_DIGEST: Final = "sha256:" + "a" * 64
PLATFORM_DIGEST: Final = "sha256:" + "b" * 64
OTHER_DIGEST: Final = "sha256:" + "c" * 64
IMAGE: Final = f"{IMAGE_REPOSITORY}@{INDEX_DIGEST}"
PLATFORM_IMAGE_ID: Final = f"docker-pullable://{IMAGE_REPOSITORY}@{PLATFORM_DIGEST}"
CONNECTION_STALE: Final = "agent_connection_predates_pod"
CONTROLLER_DRIFT: Final = "agent_pod_controller_identity_mismatch"
IMAGE_OR_READY: Final = "agent_pod_image_or_readiness_mismatch"
POD_READBACK_INVALID: Final = "agent_pod_readback_invalid"
LEASE_MISMATCH: Final = "agent_lease_identity_mismatch"
LEASE_READBACK_INVALID: Final = "agent_lease_readback_invalid"
AGENT_ID: Final = 2
LEASE_NAME: Final = AGENT.agent_lease_name(AGENT_ID)
LEASE_UID: Final = "agent-lease-uid"
# A holder that connection._held_by accepts: the Pod name followed by "_<suffix>".
HOLDER: Final = f"{POD_NAME}_holder-suffix"
API_VERSION: Final = "apiVersion"
SPEC: Final = "spec"
HOLDER_FIELD: Final = "holderIdentity"
DELETED_AT: Final = "deletionTimestamp"
DELETION_TIME: Final = "2026-01-01T00:05:00Z"
OWNERS: Final = "ownerReferences"
OTHER_LEASE_API_VERSION: Final = "coordination.k8s.io/v1beta1"
OTHER_KIND: Final = "ConfigMap"
OTHER_CONTROLLER_KIND: Final = "StatefulSet"
OTHER_DEPLOYMENT_NAME: Final = "other-agent"
NOT_TEXT: Final = 7
CONTROLLER_KIND_MISMATCH: Final = "agent_pod_controller_mismatch"
# Marks a field the case deletes from the decoded response instead of replacing.
REMOVED: Final = object()
EXPECTED = AGENT.AgentPodExpectation(
    NAMESPACE,
    DEPLOYMENT_NAME,
    DEPLOYMENT_UID,
    CONTAINER,
    IMAGE,
    frozenset((INDEX_DIGEST, PLATFORM_DIGEST)),
)
READS: Final = (
    call.read(POD, POD_NAME, NAMESPACE),
    call.read(REPLICA_SET, REPLICA_SET_NAME, NAMESPACE),
    call.read(DEPLOYMENT, DEPLOYMENT_NAME, NAMESPACE),
)


def _controller(kind: str, name: str, uid: str) -> dict[str, object]:
    """Create the native owner reference for one controlling object."""
    return {KIND: kind, NAME: name, UID: uid, "controller": True}


# Each row decodes an independent copy of this immutable native response set.
BASELINE: Final = json.dumps(
    {
        NAMESPACE_KIND: {
            name: {KIND: NAMESPACE_KIND, METADATA: {NAME: name, UID: uid}}
            for name, uid in (
                (CLUSTER_ID_NAMESPACE, CLUSTER_UID),
                (NAMESPACE, NAMESPACE_UID),
            )
        },
        CONNECTION: {
            "connection_id": "42",
            "connected_at": "2026-01-01T00:01:00Z",
            "pod_name": POD_NAME,
            "pod_namespace": NAMESPACE,
            "agent_version": "v18.5.2",
            "agent_commit": "a" * 40,
        },
        POD: {
            KIND: POD,
            METADATA: {
                NAME: POD_NAME,
                NAMESPACE_FIELD: NAMESPACE,
                UID: POD_UID,
                BORN: "2026-01-01T00:00:00Z",
                "ownerReferences": [
                    _controller(REPLICA_SET, REPLICA_SET_NAME, REPLICA_SET_UID)
                ],
            },
            "spec": {"containers": [{NAME: CONTAINER, "image": IMAGE}]},
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {
                        NAME: CONTAINER,
                        "ready": True,
                        "imageID": PLATFORM_IMAGE_ID,
                    }
                ],
            },
        },
        REPLICA_SET: {
            KIND: REPLICA_SET,
            METADATA: {
                NAME: REPLICA_SET_NAME,
                NAMESPACE_FIELD: NAMESPACE,
                UID: REPLICA_SET_UID,
                "ownerReferences": [
                    _controller(DEPLOYMENT, DEPLOYMENT_NAME, DEPLOYMENT_UID)
                ],
            },
        },
        DEPLOYMENT: {
            KIND: DEPLOYMENT,
            METADATA: {
                NAME: DEPLOYMENT_NAME,
                NAMESPACE_FIELD: NAMESPACE,
                UID: DEPLOYMENT_UID,
            },
        },
        LEASE: {
            API_VERSION: AGENT.LEASE_API_VERSION,
            KIND: LEASE,
            METADATA: {NAME: LEASE_NAME, NAMESPACE_FIELD: NAMESPACE, UID: LEASE_UID},
            SPEC: {HOLDER_FIELD: HOLDER},
        },
    }
)


@pytest.fixture
def wire() -> dict[str, object]:
    """Decode a fresh copy for one invocation."""
    return json.loads(BASELINE)


@pytest.fixture
def service(wire: dict[str, object]) -> Mock:
    """Expose only scripted native read-back behind the real service interface."""
    result = create_autospec(AGENT.KubernetesService, instance=True)

    def read(kind: str, name: str, _namespace: str) -> object:
        resource = wire[kind]
        return resource[name] if kind == NAMESPACE_KIND else resource

    result.read.side_effect = read
    return result


def test_agent_cluster_scope_reads_both_native_namespace_uids(service: Mock) -> None:
    """Measure the same cluster and namespace identities as the CI proof job."""
    assert AGENT.read_agent_cluster_scope(service, NAMESPACE) == (
        CLUSTER_UID,
        NAMESPACE_UID,
    )
    assert service.mock_calls == [
        call.read(NAMESPACE_KIND, CLUSTER_ID_NAMESPACE, ""),
        call.read(NAMESPACE_KIND, NAMESPACE, ""),
    ]


def test_agent_connection_correlates_exact_pod_uid_and_platform_digest(
    wire: dict[str, object], service: Mock
) -> None:
    """Match the controller chain and accept a verified platform image digest."""
    connection = AGENT.AgentConnection(**wire[CONNECTION])

    result = AGENT.correlate_agent_pod(service, connection, EXPECTED)

    assert result == AGENT.AgentPodObservation(POD_UID, PLATFORM_DIGEST)
    assert tuple(service.mock_calls) == READS


@pytest.mark.parametrize(
    ("path", "value", "reason", "reads"),
    (
        (
            (POD, METADATA, BORN),
            "2026-01-01T00:02:00Z",
            CONNECTION_STALE,
            3,
        ),
        ((REPLICA_SET, METADATA, UID), REPLACEMENT_UID, CONTROLLER_DRIFT, 3),
        ((DEPLOYMENT, METADATA, UID), REPLACEMENT_UID, CONTROLLER_DRIFT, 3),
        (
            (POD, "spec", "containers", 0, "image"),
            f"{IMAGE_REPOSITORY}@{OTHER_DIGEST}",
            IMAGE_OR_READY,
            3,
        ),
        (
            (POD, "status", "containerStatuses", 0, "imageID"),
            f"containerd://{OTHER_DIGEST}",
            IMAGE_OR_READY,
            3,
        ),
        ((POD, "status", "containerStatuses", 0, "ready"), False, IMAGE_OR_READY, 3),
        (
            (CONNECTION, "pod_namespace"),
            OTHER_NAMESPACE,
            "agent_connection_namespace_mismatch",
            0,
        ),
        (
            (POD, METADATA, NAMESPACE_FIELD),
            OTHER_NAMESPACE,
            "agent_pod_readback_identity_mismatch",
            1,
        ),
    ),
    ids=(
        "old-connection-same-pod-name",
        "replaced-replica-set",
        "replaced-deployment",
        "unpinned-desired-image",
        "unverified-runtime-image",
        "container-not-ready",
        "cross-namespace-connection",
        "cross-namespace-pod-readback",
    ),
)
def test_agent_pod_rejects_one_mismatched_native_observation(
    wire: dict[str, object],
    service: Mock,
    path: tuple[str | int, ...],
    value: object,
    reason: str,
    reads: int,
) -> None:
    """Reject one changed invariant and never call a native mutation."""
    current = wire
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    connection = AGENT.AgentConnection(**wire[CONNECTION])

    with pytest.raises(AGENT.KubernetesError) as rejected:
        AGENT.correlate_agent_pod(service, connection, EXPECTED)

    assert str(rejected.value) == reason
    assert tuple(service.mock_calls) == READS[:reads]


def _edit(wire: dict[str, object], path: tuple[str | int, ...], value: object) -> None:
    """Replace or remove one field of the decoded native response copy.

    :param wire: Decoded copy of ``BASELINE`` for one case.
    :param path: Keys and list indexes that lead to the field.
    :param value: New field value, or ``REMOVED`` to delete the field.
    """
    current = wire
    for key in path[:-1]:
        current = current[key]
    if value is REMOVED:
        del current[path[-1]]
    else:
        current[path[-1]] = value


def test_agent_lease_absent_returns_none(
    wire: dict[str, object], service: Mock
) -> None:
    """Return None when agentk has not written its Lease yet.

    :param wire: Decoded native responses for this case.
    :param service: Fake Kubernetes service serving ``wire``.
    """
    wire[LEASE] = None

    assert AGENT.read_agent_lease(service, NAMESPACE, AGENT_ID) is None
    assert service.mock_calls == [call.read(LEASE, LEASE_NAME, NAMESPACE)]


@pytest.mark.parametrize(
    ("path", "value", "holder"),
    (
        pytest.param((LEASE, SPEC, HOLDER_FIELD), HOLDER, HOLDER, id="suffixed-holder"),
        pytest.param(
            (LEASE, SPEC, HOLDER_FIELD), POD_NAME, POD_NAME, id="pod-name-holder"
        ),
        pytest.param((LEASE, SPEC, HOLDER_FIELD), REMOVED, "", id="holder-missing"),
        pytest.param((LEASE, SPEC, HOLDER_FIELD), None, "", id="holder-null"),
        pytest.param((LEASE, SPEC), REMOVED, "", id="spec-missing"),
    ),
)
def test_agent_lease_reads_holder_from_spec_and_native_uid(
    wire: dict[str, object],
    service: Mock,
    path: tuple[str, ...],
    value: object,
    holder: str,
) -> None:
    """Return ``spec.holderIdentity`` and the Lease UID; an unheld Lease gives "".

    A missing, null or empty holder reads as "", which ``connection._held_by``
    matches to no Pod.

    :param wire: Decoded native responses for this case.
    :param service: Fake Kubernetes service serving ``wire``.
    :param path: Field of the Lease the case sets or removes.
    :param value: New value, or ``REMOVED``.
    :param holder: Holder the reader must return.
    """
    _edit(wire, path, value)

    assert AGENT.read_agent_lease(service, NAMESPACE, AGENT_ID) == (holder, LEASE_UID)
    assert service.mock_calls == [call.read(LEASE, LEASE_NAME, NAMESPACE)]


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    (
        pytest.param(
            (LEASE, API_VERSION),
            OTHER_LEASE_API_VERSION,
            LEASE_MISMATCH,
            id="other-api-version",
        ),
        pytest.param((LEASE, KIND), OTHER_KIND, LEASE_MISMATCH, id="other-kind"),
        pytest.param(
            (LEASE, METADATA, NAME),
            AGENT.agent_lease_name(AGENT_ID + 1),
            LEASE_MISMATCH,
            id="other-agent-lease",
        ),
        pytest.param(
            (LEASE, METADATA, NAMESPACE_FIELD),
            OTHER_NAMESPACE,
            LEASE_MISMATCH,
            id="other-namespace",
        ),
        pytest.param(
            (LEASE, METADATA, DELETED_AT),
            DELETION_TIME,
            LEASE_MISMATCH,
            id="being-deleted",
        ),
        pytest.param(
            (LEASE, METADATA, UID), REMOVED, LEASE_READBACK_INVALID, id="uid-missing"
        ),
        pytest.param(
            (LEASE, SPEC, HOLDER_FIELD),
            NOT_TEXT,
            LEASE_READBACK_INVALID,
            id="holder-not-text",
        ),
    ),
)
def test_agent_lease_rejects_one_changed_identity(
    wire: dict[str, object],
    service: Mock,
    path: tuple[str, ...],
    value: object,
    reason: str,
) -> None:
    """Refuse a read-back that is not this Agent's live Lease in its namespace.

    :param wire: Decoded native responses for this case.
    :param service: Fake Kubernetes service serving ``wire``.
    :param path: Field of the Lease the case sets or removes.
    :param value: New value, or ``REMOVED``.
    :param reason: Stable error the reader must raise.
    """
    _edit(wire, path, value)

    with pytest.raises(AGENT.KubernetesError) as rejected:
        AGENT.read_agent_lease(service, NAMESPACE, AGENT_ID)

    assert str(rejected.value) == reason
    assert service.mock_calls == [call.read(LEASE, LEASE_NAME, NAMESPACE)]


def test_agent_replica_set_reads_pod_controller_owned_by_recorded_deployment(
    service: Mock,
) -> None:
    """Return the ReplicaSet that controls the Pod, without reading the Deployment.

    :param service: Fake Kubernetes service serving the baseline responses.
    """
    result = AGENT.read_agent_replica_set(service, POD_NAME, EXPECTED)

    assert result == (REPLICA_SET_NAME, REPLICA_SET_UID)
    assert tuple(service.mock_calls) == READS[:2]


@pytest.mark.parametrize(
    ("path", "value", "reason", "reads"),
    (
        pytest.param(
            (REPLICA_SET, METADATA, OWNERS, 0, UID),
            REPLACEMENT_UID,
            CONTROLLER_DRIFT,
            2,
            id="other-deployment-uid",
        ),
        pytest.param(
            (REPLICA_SET, METADATA, OWNERS, 0, NAME),
            OTHER_DEPLOYMENT_NAME,
            CONTROLLER_DRIFT,
            2,
            id="other-deployment-name",
        ),
        pytest.param(
            (REPLICA_SET, METADATA, UID),
            REPLACEMENT_UID,
            CONTROLLER_DRIFT,
            2,
            id="replaced-replica-set",
        ),
        pytest.param(
            (REPLICA_SET, METADATA, UID),
            REMOVED,
            POD_READBACK_INVALID,
            2,
            id="replica-set-uid-missing",
        ),
        pytest.param(
            (POD, METADATA, OWNERS, 0, KIND),
            OTHER_CONTROLLER_KIND,
            CONTROLLER_KIND_MISMATCH,
            1,
            id="pod-not-controlled-by-replica-set",
        ),
    ),
)
def test_agent_replica_set_rejects_one_changed_controller(
    wire: dict[str, object],
    service: Mock,
    path: tuple[str | int, ...],
    value: object,
    reason: str,
    reads: int,
) -> None:
    """Refuse a ReplicaSet that another Deployment controls or that replaced the original.

    The Deployment is matched by the name and UID recorded in ``EXPECTED``, never by
    reading the Deployment object.

    :param wire: Decoded native responses for this case.
    :param service: Fake Kubernetes service serving ``wire``.
    :param path: Field of the Pod or ReplicaSet the case sets or removes.
    :param value: New value, or ``REMOVED``.
    :param reason: Stable error the reader must raise.
    :param reads: Native reads made before the reader stops.
    """
    _edit(wire, path, value)

    with pytest.raises(AGENT.KubernetesError) as rejected:
        AGENT.read_agent_replica_set(service, POD_NAME, EXPECTED)

    assert str(rejected.value) == reason
    assert tuple(service.mock_calls) == READS[:reads]
