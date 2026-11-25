"""Correlate a selected GitLab Agent connection to its owned Kubernetes Pod."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Optional

from lib.python.core.gitlab.cluster_agent.ci_access_proof import CLUSTER_ID_NAMESPACE
from lib.python.core.gitlab.cluster_agent.provider import AgentConnection
from lib.python.core.kubernetes.kinds import (
    COORDINATION_API_VERSION,
    DEPLOYMENT_KIND,
    LEASE_KIND,
    REPLICA_SET_KIND,
)
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.kubernetes.objects import KubernetesObject


@dataclass(frozen=True)
class AgentPodExpectation:
    """Bind Kubernetes read-back to the installation recorded by the run.

    .. attribute :: namespace
        :type: str

        Selected native namespace.

    .. attribute :: deployment_name
        :type: str

        Deployment name from the verified Helm installation.

    .. attribute :: deployment_uid
        :type: str

        Original Deployment identity, never inferred from its name.

    .. attribute :: container
        :type: str

        Agent container name from the verified chart render.

    .. attribute :: image
        :type: str

        Exact digest-pinned desired container image.

    .. attribute :: runtime_digests
        :type: frozenset[str]

        Manifest/index digests independently verified from the selected OCI image.
    """

    namespace: str
    deployment_name: str
    deployment_uid: str
    container: str
    image: str
    runtime_digests: frozenset[str]


@dataclass(frozen=True)
class AgentPodObservation:
    """Retain only the safe identifiers needed for connection evidence.

    .. attribute :: uid
        :type: str

        Exact Pod identity read independently of GitLab.

    .. attribute :: image_digest
        :type: str

        Running container digest matched to independently verified OCI bytes.

    .. attribute :: replica_set_name
        :type: str

        ReplicaSet that controls the Pod and belongs to the recorded Deployment.

    .. attribute :: replica_set_uid
        :type: str

        Native UID of that ReplicaSet.
    """

    uid: str
    image_digest: str
    replica_set_name: str
    replica_set_uid: str


def _read(
    service: KubernetesService, kind: str, name: str, namespace: str
) -> KubernetesObject:
    raw = service.read(kind, name, namespace)
    if raw is None:
        raise KubernetesError("agent_pod_readback_absent")
    obj = KubernetesObject(raw)
    if (
        obj.text("kind") != kind
        or obj.metadata.text("name") != name
        or obj.metadata.text("namespace") != namespace
        or obj.metadata.text("deletionTimestamp") is not None
    ):
        raise KubernetesError("agent_pod_readback_identity_mismatch")
    return obj


# agentk writes its leader-election Lease in its own namespace and names it after the
# Agent ID; agentk v18.5.2 wrote "agent-1-lock" for Agent ID 1 on the test cluster.
AGENT_LEASE_NAME: Final = "agent-{agent_id}-lock"


@dataclass(frozen=True)
class AgentLease:
    """Retain the identity of the agentk leader-election Lease.

    .. attribute :: holder
        :type: str

        ``spec.holderIdentity``, or an empty string when no holder is set.

    .. attribute :: uid
        :type: str

        Native Lease UID.
    """

    holder: str
    uid: str


def agent_lease_name(agent_id: int) -> str:
    """Name the leader-election Lease that agentk writes for one Agent.

    :param agent_id: Native GitLab Agent ID from the ownership record.
    :returns: Lease name in the Agent's namespace.
    """
    return AGENT_LEASE_NAME.format(agent_id=agent_id)


def read_agent_lease(
    service: KubernetesService, namespace: str, agent_id: int
) -> Optional[AgentLease]:
    """Read the agentk Lease of one Agent in its namespace.

    :param service: Existing credential-bound Kubernetes transport.
    :param namespace: Namespace of the Agent installation.
    :param agent_id: Native GitLab Agent ID from the ownership record.
    :returns: The Lease holder and UID, or ``None`` when the Lease is absent.
    :raises KubernetesError: The object read back is not that Lease or is being deleted.
    """
    name = agent_lease_name(agent_id)
    raw = service.read(LEASE_KIND, name, namespace)
    if raw is None:
        return None
    try:
        obj = KubernetesObject(raw)
        if (
            obj.text("apiVersion") != COORDINATION_API_VERSION
            or obj.text("kind") != LEASE_KIND
            or obj.metadata.text("name") != name
            or obj.metadata.text("namespace") != namespace
            or obj.metadata.text("deletionTimestamp") is not None
        ):
            raise KubernetesError("agent_lease_identity_mismatch")
        holder = obj.object("spec").text("holderIdentity") or ""
        return AgentLease(holder, obj.metadata.required_text("uid"))
    except TypeError:
        raise KubernetesError("agent_lease_readback_invalid") from None


def _controller(obj: KubernetesObject, kind: str) -> KubernetesObject:
    owners = [
        owner
        for owner in obj.metadata.objects("ownerReferences")
        if owner.document.get("controller") is True
    ]
    if len(owners) != 1 or owners[0].text("kind") != kind:
        raise KubernetesError("agent_pod_controller_mismatch")
    return owners[0]


def _container(obj: KubernetesObject, field: str, wanted: str) -> KubernetesObject:
    matches = [item for item in obj.objects(field) if item.text("name") == wanted]
    if len(matches) != 1:
        raise KubernetesError("agent_pod_container_unverified")
    return matches[0]


def _image_digest(value: str) -> str:
    reference = value.split("://", 1)[-1]
    return reference.rsplit("@", 1)[-1]


def read_agent_cluster_scope(
    service: KubernetesService, namespace: str
) -> tuple[str, str]:
    """Read the same cluster and namespace identities used by the consumer job.

    :param service: Existing credential-bound Kubernetes transport.
    :param namespace: Exact run namespace selected through verified ownership.
    :returns: UIDs of kube-system and the selected namespace, in that order.
    :raises KubernetesError: Either namespace is absent, replaced by a malformed
        response, or being deleted.
    """  # noqa: DOC502 - Shared namespace verification propagates failures.
    return read_agent_cluster_uid(service), _namespace_uid(service, namespace)


def read_agent_cluster_uid(service: KubernetesService) -> str:
    """Read the current cluster's native identity independently of a workload.

    :param service: Selected credential-bound Kubernetes transport.
    :returns: UID of the native kube-system namespace.
    :raises KubernetesError: The cluster identity cannot be verified.
    """  # noqa: DOC502 - Shared namespace verification propagates failures.
    return _namespace_uid(service, CLUSTER_ID_NAMESPACE)


def _namespace_uid(service: KubernetesService, name: str) -> str:
    raw = service.read("Namespace", name, "")
    if raw is None:
        raise KubernetesError("agent_namespace_absent")
    obj = KubernetesObject(raw)
    try:
        if (
            obj.text("kind") != "Namespace"
            or obj.metadata.text("name") != name
            or obj.metadata.text("deletionTimestamp") is not None
        ):
            raise KubernetesError("agent_namespace_identity_mismatch")
        return obj.metadata.required_text("uid")
    except TypeError:
        raise KubernetesError("agent_namespace_readback_invalid") from None


def correlate_agent_pod(
    service: KubernetesService,
    connection: AgentConnection,
    expected: AgentPodExpectation,
) -> AgentPodObservation:
    """Verify native connection metadata against a live, owned Agent container.

    :param service: Existing credential-bound Kubernetes transport.
    :param connection: Active native GitLab Agent connection.
    :param expected: Recorded Deployment identity and known pinned image.
    :returns: Pod UID and observed immutable runtime image digest.
    :raises KubernetesError: Provider data is absent, malformed, stale, or mismatched.
    """
    if connection.pod_namespace != expected.namespace:
        raise KubernetesError("agent_connection_namespace_mismatch")
    try:
        pod = _read(service, "Pod", connection.pod_name, expected.namespace)
        pod_owner = _controller(pod, REPLICA_SET_KIND)
        replica = _read(
            service,
            REPLICA_SET_KIND,
            pod_owner.required_text("name"),
            expected.namespace,
        )
        deployment_owner = _controller(replica, DEPLOYMENT_KIND)
        deployment = _read(
            service, DEPLOYMENT_KIND, expected.deployment_name, expected.namespace
        )
        if (
            replica.metadata.required_text("uid") != pod_owner.required_text("uid")
            or deployment_owner.required_text("name") != expected.deployment_name
            or deployment_owner.required_text("uid") != expected.deployment_uid
            or deployment.metadata.required_text("uid") != expected.deployment_uid
        ):
            raise KubernetesError("agent_pod_controller_identity_mismatch")

        born = datetime.fromisoformat(pod.metadata.required_text("creationTimestamp"))
        connected = datetime.fromisoformat(connection.connected_at)
        if born.tzinfo is None or connected.tzinfo is None or connected < born:
            raise KubernetesError("agent_connection_predates_pod")

        desired = _container(pod.object("spec"), "containers", expected.container)
        status = pod.object("status")
        running = _container(status, "containerStatuses", expected.container)
        digest = _image_digest(running.required_text("imageID"))
        if (
            desired.required_text("image") != expected.image
            or status.text("phase") != "Running"
            or running.document.get("ready") is not True
            or digest not in expected.runtime_digests
        ):
            raise KubernetesError("agent_pod_image_or_readiness_mismatch")
        return AgentPodObservation(
            pod.metadata.required_text("uid"),
            digest,
            replica.metadata.required_text("name"),
            replica.metadata.required_text("uid"),
        )
    except (TypeError, ValueError):
        raise KubernetesError("agent_pod_readback_invalid") from None
