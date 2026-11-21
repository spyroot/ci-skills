"""Correlate active native KAS connections before an Agent becomes connected."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from lib.python.core.cli_api import RequestDeadline
from lib.python.core.gitlab.cluster_agent.provider import (
    AgentConnection,
    AgentProvider,
    AgentRegistration,
)
from lib.python.core.gitlab.cluster_agent.recovery import (
    AgentRecoveryStore,
    RecoveryRecord,
)
from lib.python.core.gitlab.cluster_agent.registration import (
    observed_resource,
    persist_observation,
)
from lib.python.core.gitlab.cluster_agent.selection import (
    APP_VERSION,
    IMAGE_TAG,
    ConnectSelection,
)
from lib.python.core.gitlab.cluster_agent.terms import AGENT_POD_LIMIT, PRESENT
from lib.python.core.gitlab.gitlab_session import GitLabService
from lib.python.core.json_types import (
    JSONObject,
    json_integer,
    json_object,
    json_objects,
    json_string,
)
from lib.python.core.kubernetes.gitlab_agent import (
    AgentPodExpectation,
    AgentPodObservation,
    agent_lease_name,
    correlate_agent_pod,
    read_agent_cluster_scope,
    read_agent_lease,
)
from lib.python.core.kubernetes.kinds import (
    APPS_API_VERSION,
    COORDINATION_API_VERSION,
    LEASE_KIND,
    REPLICA_SET_KIND,
)
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.oci import ImageManifestIdentity

_CHART_CONTAINER: Final = "gitlab-agent"
_POLL_SECONDS: Final = 5.0


@dataclass(frozen=True, slots=True)
class ConnectedAgentPod:
    """Bind one provider connection to the independently observed running Pod.

    .. attribute :: connection
        :type: AgentConnection

        Exact active GitLab connection metadata.

    .. attribute :: pod
        :type: AgentPodObservation

        Kubernetes Pod UID and observed immutable runtime digest.
    """

    connection: AgentConnection
    pod: AgentPodObservation


@dataclass(frozen=True, slots=True)
class AgentConnectivity:
    """Retain the connected recovery record and its native proof.

    .. attribute :: record
        :type: RecoveryRecord

        Latest recovery record after conditional update and independent read-back.

    .. attribute :: connections
        :type: tuple[ConnectedAgentPod, ...]

        Complete correlated set of active connections.
    """

    record: RecoveryRecord
    connections: tuple[ConnectedAgentPod, ...]


def _text(document: JSONObject, field: str) -> str:
    value = json_string(document.get(field))
    if not value:
        raise TypeError("agent_connection_required_field_missing")
    return value


def _scope(record: RecoveryRecord) -> tuple[str, str]:
    return (
        _text(record.document, "cluster_uid"),
        _text(record.document, "namespace_uid"),
    )


def _installation(
    gitlab: GitLabService,
    selection: ConnectSelection,
    record: RecoveryRecord,
    image: ImageManifestIdentity,
) -> tuple[AgentRegistration, AgentPodExpectation, dict[str, str]]:
    document = record.document
    namespace = selection.scope.namespace
    expected = {
        "origin": gitlab.session.origin.rstrip("/"),
        "project_id": selection.project.id,
        "project_path": selection.project.path,
        "agent_name": selection.agent_name,
        "run_id": selection.scope.run_id,
        "namespace": namespace,
        "release": selection.release,
        "ownership_record_uid": record.uid,
        "app_version": APP_VERSION,
        "image_digest": image.digest,
    }
    if (
        not record.uid
        or gitlab.target_id != selection.project.id
        or any(document.get(key) != value for key, value in expected.items())
        or image.digest not in image.runtime_digests
        or document.get("state") not in ("PENDING", "CONNECTED", "VERIFIED")
    ):
        raise KubernetesError("agent_connection_record_mismatch")
    try:
        agent_id = document.get("agent_id")
        resources = json_objects(document.get("resources"))
        deployments = [
            entry for entry in resources if entry.get("kind") == "Deployment"
        ]
        if (
            type(agent_id) is not int
            or agent_id < 1
            or json_object(document.get("created")).get("release") is not True
            or len(deployments) != 1
        ):
            raise TypeError
        deployment = deployments[0]
        if (
            deployment.get("api_version") != "apps/v1"
            or deployment.get("namespace") != namespace
            or deployment.get("created") is not True
            or deployment.get("state") != PRESENT
        ):
            raise TypeError
        pods: dict[str, str] = {}
        for entry in resources:
            if entry.get("kind") != "Pod":
                continue
            name = _text(entry, "name")
            if (
                document.get("state") == "PENDING"
                or entry.get("namespace") != namespace
                or entry.get("created") is not True
                or entry.get("state") != PRESENT
                or name in pods
            ):
                raise TypeError
            pods[name] = _text(entry, "uid")
        correlation = AgentPodExpectation(
            namespace=namespace,
            deployment_name=_text(deployment, "name"),
            deployment_uid=_text(deployment, "uid"),
            container=_CHART_CONTAINER,
            image=f"{IMAGE_TAG}@{image.digest}",
            runtime_digests=image.runtime_digests,
        )
        return (
            AgentRegistration(agent_id, selection.agent_name, selection.project.id),
            correlation,
            pods,
        )
    except TypeError:
        raise KubernetesError("agent_connection_record_mismatch") from None


def _held_by(holder: str, pods: dict[str, str]) -> bool:
    # A leader-election identity is the Pod name, optionally followed by "_<suffix>".
    return any(
        holder == name
        or (holder.startswith(name + "_") and len(holder) > len(name) + 1)
        for name in pods
    )


def _runtime_rows(
    kubernetes: KubernetesService,
    namespace: str,
    agent_id: int,
    connections: tuple[ConnectedAgentPod, ...],
    deadline: RequestDeadline,
) -> tuple[JSONObject, ...]:
    """Build ownership rows for the objects the connected Agent workload created.

    The ReplicaSets come from the Pod correlation that proved each connection. The
    Lease wait uses at most half of the remaining deadline, so persistence and any
    compensation keep the other half. When no connected Pod holds the Lease by then,
    no Lease row is returned; delete then treats the record as one without a Lease
    row.

    :param kubernetes: Bound Kubernetes service of the recorded installation.
    :param namespace: Namespace of the Agent installation.
    :param agent_id: Native GitLab Agent ID.
    :param connections: Verified connections with their correlated Pods.
    :param deadline: Shared proof deadline.
    :returns: One row per controlling ReplicaSet, then the Lease row when a
        connected Pod holds it; each row is marked created by this run.
    :raises KubernetesError: The Lease read back is not that Lease.
    """  # noqa: DOC503 - The shared deadline propagates its expiry error.
    pods = {entry.connection.pod_name: entry.pod.uid for entry in connections}
    replica_sets = sorted(
        {
            (entry.pod.replica_set_name, entry.pod.replica_set_uid)
            for entry in connections
        }
    )
    rows: list[JSONObject] = [
        observed_resource(
            REPLICA_SET_KIND,
            name,
            namespace,
            uid,
            created=True,
            api_version=APPS_API_VERSION,
        )
        for name, uid in replica_sets
    ]
    stop = time.monotonic() + deadline.remaining() / 2
    while True:
        lease = deadline.call(read_agent_lease, kubernetes, namespace, agent_id)
        if lease is not None and _held_by(lease.holder, pods):
            rows.append(
                observed_resource(
                    LEASE_KIND,
                    agent_lease_name(agent_id),
                    namespace,
                    lease.uid,
                    created=True,
                    api_version=COORDINATION_API_VERSION,
                )
            )
            return tuple(rows)
        left = stop - time.monotonic()
        if left <= 0:
            return tuple(rows)
        time.sleep(min(_POLL_SECONDS, left))


def _active_connections(
    provider: AgentProvider,
    selection: ConnectSelection,
    agent: AgentRegistration,
    deadline: RequestDeadline,
    *,
    wait: bool,
) -> tuple[AgentConnection, ...]:
    while True:
        active = deadline.call(provider.connections, selection.project, agent)
        if active:
            return active
        if not wait:
            raise KubernetesError("agent_connection_absent")
        time.sleep(min(_POLL_SECONDS, deadline.remaining()))


def read_agent_connectivity(
    gitlab: GitLabService,
    kubernetes: KubernetesService,
    selection: ConnectSelection,
    record: RecoveryRecord,
    image: ImageManifestIdentity,
    *,
    timeout: float,
    wait: bool = False,
) -> tuple[ConnectedAgentPod, ...]:
    """Read the complete active set and verify every running image and Pod UID.

    :param gitlab: Access-verified GitLab service for the selected project.
    :param kubernetes: Kubernetes service bound to the selected cluster.
    :param selection: Exact project, Agent and installation scope.
    :param record: Latest native recovery snapshot with the installed identities.
    :param image: OCI root and runtime digests derived from the official manifest.
    :param timeout: Remaining seconds for the connection proof.
    :param wait: Pace read-only retries only when GitLab returns an empty active set.
    :returns: Complete native connection inventory correlated to owned Pods.
    :raises KubernetesError: Scope, stored identity, connection or Pod proof fails.
    :raises GitLabAPIError: Native GitLab read fails or is incomplete.
    :raises APIError: The shared operation deadline expires.
    """  # noqa: DOC503 - Provider and shared deadline failures propagate.
    if not math.isfinite(timeout) or timeout <= 0:
        raise KubernetesError("agent_connection_timeout")
    deadline = RequestDeadline(time.monotonic() + timeout, time.monotonic)
    agent, expected, original_pods = _installation(gitlab, selection, record, image)
    try:
        cluster_scope = _scope(record)
    except TypeError:
        raise KubernetesError("agent_connection_record_mismatch") from None
    if (
        deadline.call(read_agent_cluster_scope, kubernetes, selection.scope.namespace)
        != cluster_scope
    ):
        raise KubernetesError("agent_connection_scope_mismatch")
    provider = AgentProvider(gitlab)
    if (
        deadline.call(provider.project) != selection.project
        or deadline.call(provider.read_agent, agent.id) != agent
    ):
        raise KubernetesError("agent_connection_registration_mismatch")
    active = _active_connections(provider, selection, agent, deadline, wait=wait)
    if len(active) > AGENT_POD_LIMIT or len(
        {item.connection_id for item in active}
    ) != len(active):
        raise KubernetesError("agent_connection_metadata_mismatch")
    now = datetime.now(UTC)
    observed: list[ConnectedAgentPod] = []
    pods: dict[str, str] = {}
    for connection in active:
        try:
            connected_at = datetime.fromisoformat(connection.connected_at)
        except (TypeError, ValueError):
            raise KubernetesError("agent_connection_metadata_mismatch") from None
        if (
            connected_at.utcoffset() is None
            or connected_at > now
            or connection.agent_version.removeprefix("v")
            != APP_VERSION.removeprefix("v")
        ):
            raise KubernetesError("agent_connection_metadata_mismatch")
        pod = deadline.call(correlate_agent_pod, kubernetes, connection, expected)
        if pods.setdefault(connection.pod_name, pod.uid) != pod.uid:
            raise KubernetesError("agent_connection_pod_identity_mismatch")
        observed.append(ConnectedAgentPod(connection, pod))
    if record.document.get("state") != "PENDING" and original_pods != pods:
        raise KubernetesError("agent_connection_pod_identity_mismatch")
    return tuple(observed)


def confirm_fresh_agent_connection(
    gitlab: GitLabService,
    kubernetes: KubernetesService,
    selection: ConnectSelection,
    record: RecoveryRecord,
    image: ImageManifestIdentity,
    *,
    timeout: float,
) -> AgentConnectivity:
    """Record verified first connection ownership without changing an existing run.

    :param gitlab: Selected GitLab project and native connection authority.
    :param kubernetes: Bound Kubernetes service of the recorded installation.
    :param selection: Exact approved project, Agent, and run scope.
    :param record: Current PENDING recovery record after the first install.
    :param image: Verified OCI root and runtime digest inventory.
    :param timeout: Remaining operation seconds.
    :returns: Independently confirmed CONNECTED record and native proof.
    :raises KubernetesError: Native proof, current state, or ownership update fails.
    :raises GitLabAPIError: Reading the selected native registration or KAS fails.
    :raises APIError: The shared proof deadline expires.
    """  # noqa: DOC503 - Native proof and conditional persistence propagate failures.
    if record.document.get("state") != "PENDING":
        raise KubernetesError("agent_connection_record_mismatch")
    deadline = RequestDeadline(time.monotonic() + timeout, time.monotonic)
    connections = read_agent_connectivity(
        gitlab,
        kubernetes,
        selection,
        record,
        image,
        timeout=deadline.remaining(),
        wait=True,
    )
    pods = {entry.connection.pod_name: entry.pod.uid for entry in connections}
    runtime = _runtime_rows(
        kubernetes,
        selection.scope.namespace,
        json_integer(record.document.get("agent_id")),
        connections,
        deadline,
    )
    cluster_uid, namespace_uid = _scope(record)
    store = AgentRecoveryStore(
        kubernetes, selection, gitlab.session.origin, cluster_uid, namespace_uid
    )
    saved = deadline.call(
        persist_observation,
        store,
        record,
        {"state": "CONNECTED"},
        "release",
        *(
            observed_resource("Pod", name, selection.scope.namespace, uid, created=True)
            for name, uid in sorted(pods.items())
        ),
        *runtime,
    )
    return AgentConnectivity(saved, connections)
