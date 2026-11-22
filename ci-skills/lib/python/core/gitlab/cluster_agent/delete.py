"""Delete only the original native identities recorded for a connected Agent."""

from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass
from typing import Final

from lib.python.core.cli_api import RequestDeadline
from lib.python.core.gitlab.cluster_agent.absence import (
    LogicalAgentAbsence,
    verify_agent_logical_absence,
)
from lib.python.core.gitlab.cluster_agent.evidence import verified_agent_owner
from lib.python.core.gitlab.cluster_agent.provider import (
    AgentProvider,
    AgentRegistration,
    AgentToken,
)
from lib.python.core.gitlab.cluster_agent.recovery import (
    CREDENTIAL_ID,
    CREDENTIAL_NAME,
    CREDENTIAL_RESOURCE_NAME,
    AgentRecoveryStore,
    RecoveryRecord,
    agent_recovery_name,
)
from lib.python.core.gitlab.cluster_agent.secret import agent_secret_name
from lib.python.core.gitlab.cluster_agent.selection import (
    ConnectSelection,
    release_name,
)
from lib.python.core.gitlab.cluster_agent.terms import (
    ABSENT,
    AGENT_POD_LIMIT,
    AGENT_RUNTIME_KINDS,
    DELETE,
    PRESENT,
)
from lib.python.core.gitlab.gitlab_session import GitLabService
from lib.python.core.helm.native import HelmClient, HelmRelease
from lib.python.core.helm.storage import (
    HelmStorageIdentity,
    helm_storage_name,
    read_helm_storage,
)
from lib.python.core.json_types import (
    JSONObject,
    json_integer,
    json_object,
    json_objects,
    json_string,
)
from lib.python.core.kubernetes.agent_namespace import verify_agent_namespace_cleanup
from lib.python.core.kubernetes.gitlab_agent import (
    agent_lease_name,
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
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.kubernetes.run_scope import run_owned
from lib.python.core.report import plan_digest
from lib.python.core.runtime import execution_trace

_RECORD_MISMATCH: Final = "agent_delete_record_mismatch"
_RESOURCE_MISMATCH: Final = "agent_delete_resource_mismatch"
_ABSENCE_UNVERIFIED: Final = "agent_delete_native_absence_unverified"
_DELETE_ORDER: Final = (
    "Deployment",
    REPLICA_SET_KIND,
    "Pod",
    LEASE_KIND,
    "Service",
    "ServiceAccount",
    "ClusterRoleBinding",
    "Secret",
)
_REQUIRED_COUNTS: Final = {
    "Namespace": 1,
    "ConfigMap": 1,
    "Secret": 2,
    "Deployment": 1,
    "Service": 1,
    "ServiceAccount": 1,
    "ClusterRoleBinding": 1,
}
_KIND_VERSIONS: Final = {
    **dict.fromkeys((*_REQUIRED_COUNTS, "Pod"), "v1"),
    "Deployment": APPS_API_VERSION,
    "ClusterRoleBinding": "rbac.authorization.k8s.io/v1",
    REPLICA_SET_KIND: APPS_API_VERSION,
    LEASE_KIND: COORDINATION_API_VERSION,
}


@dataclass(frozen=True, slots=True)
class AgentCleanup:
    """Retain verified former IDs and the independent logical absence proof.

    .. attribute :: ownership
        :type: RecoveryRecord

        Original ownership used to authorize cleanup.

    .. attribute :: resources
        :type: tuple[JSONObject, ...]

        Original identity rows with verified ABSENT or preserved PRESENT state.

    .. attribute :: absence
        :type: LogicalAgentAbsence

        Independent current empty Agent and release selector.

    .. attribute :: agent_present
        :type: bool

        The native Agent registration existed when the delete read state before writing.

    .. attribute :: token_present
        :type: bool

        The native Agent token existed at that same read.

    .. attribute :: present
        :type: frozenset[str]

        UIDs of the owned resources that existed at that same read.

    .. attribute :: release_present
        :type: bool

        The Helm release existed at that same read.
    """

    ownership: RecoveryRecord
    resources: tuple[JSONObject, ...]
    absence: LogicalAgentAbsence
    agent_present: bool
    token_present: bool
    present: frozenset[str]
    release_present: bool


@dataclass(frozen=True, slots=True)
class _Resource:
    row: JSONObject
    kind: str
    name: str
    namespace: str
    uid: str
    version: str
    created: bool


def _original_resources(
    document: JSONObject, namespace: str, *, installation_only: bool
) -> tuple[_Resource, ...]:
    collected = []
    for row in json_objects(document.get("resources")):
        kind = json_string(row.get("kind"))
        name = json_string(row.get("name"))
        scope = json_string(row.get("namespace"))
        uid = json_string(row.get("uid"))
        version = json_string(row.get("api_version"))
        created = row.get("created")
        if (
            not name
            or not uid
            or _KIND_VERSIONS.get(kind) != version
            or scope
            != ("" if kind in ("Namespace", "ClusterRoleBinding") else namespace)
            or type(created) is not bool
            or (kind != "Namespace" and not created)
        ):
            raise KubernetesError(_RECORD_MISMATCH)
        collected.append(_Resource(row, kind, name, scope, uid, version, created))
    counts = Counter(item.kind for item in collected)
    valid_pods = (
        counts["Pod"] == 0
        if installation_only
        else 1 <= counts["Pod"] <= AGENT_POD_LIMIT
    )
    valid_runtime = (
        counts[REPLICA_SET_KIND] == counts[LEASE_KIND] == 0
        if installation_only
        else counts[REPLICA_SET_KIND] <= AGENT_POD_LIMIT and counts[LEASE_KIND] <= 1
    )
    if (
        any(counts[kind] != count for kind, count in _REQUIRED_COUNTS.items())
        or not valid_pods
        or not valid_runtime
        or len({item.uid for item in collected}) != len(collected)
    ):
        raise KubernetesError(_RECORD_MISMATCH)
    return tuple(collected)


def _native_present(
    kubernetes: KubernetesService,
    resource: _Resource,
    run_id: str,
    marker_names: frozenset[str],
) -> bool:
    with execution_trace(None):
        raw = kubernetes.read(resource.kind, resource.name, resource.namespace)
    if raw is None:
        return False
    try:
        obj = KubernetesObject(raw)
        if (
            obj.text("apiVersion") != resource.version
            or obj.text("kind") != resource.kind
            or obj.metadata.text("name") != resource.name
            or (obj.metadata.text("namespace") or "") != resource.namespace
            or obj.metadata.text("uid") != resource.uid
            or obj.metadata.text("deletionTimestamp") is not None
            or (
                resource.created
                and resource.name in marker_names
                and not run_owned(raw, run_id)
            )
        ):
            raise KubernetesError(_RESOURCE_MISMATCH)
    except TypeError:
        raise KubernetesError(_RESOURCE_MISMATCH) from None
    return True


def _provider_state(
    provider: AgentProvider,
    selection: ConnectSelection,
    agent: AgentRegistration,
    token: AgentToken,
    deadline: RequestDeadline,
    *,
    resume: bool,
) -> tuple[bool, bool]:
    if deadline.call(provider.project) != selection.project:
        raise KubernetesError("agent_delete_project_mismatch")
    native_agent = deadline.call(provider.read_agent, agent.id)
    selected = deadline.call(provider.find_agent, agent.name)
    if native_agent != selected or (
        native_agent != agent and (native_agent is not None or not resume)
    ):
        raise KubernetesError("agent_delete_registration_mismatch")
    native_token = deadline.call(provider.read_token, agent.id, token.id)
    if native_token != token and (native_token is not None or not resume):
        raise KubernetesError("agent_delete_token_mismatch")
    if native_agent is None:
        if native_token is not None:
            raise KubernetesError("agent_delete_token_mismatch")
    elif deadline.call(provider.list_tokens, agent.id) != (
        (token,) if native_token is not None else ()
    ):
        raise KubernetesError("agent_delete_token_mismatch")
    return native_agent is not None, native_token is not None


def _delete_resource(
    kubernetes: KubernetesService, deadline: RequestDeadline, resource: _Resource
) -> None:
    with execution_trace(None):
        deadline.call(
            kubernetes.delete,
            resource.kind,
            resource.name,
            resource.namespace,
            expected_uid=resource.uid,
        )


def _unrecorded_lease(
    kubernetes: KubernetesService,
    deadline: RequestDeadline,
    scope: tuple[str, str, str],
    agent: AgentRegistration,
    resources: tuple[_Resource, ...],
) -> frozenset[str]:
    """Delete the agentk Lease of a record that has no Lease row.

    A record has no Lease row when it predates Lease recording, when it is a PENDING
    record under compensation, or when no connected Pod held the Lease during
    connect. Only in a run-owned namespace, after its cluster and namespace UIDs read
    back unchanged, the Lease named after the recorded Agent ID is deleted by its UID,
    and that UID is returned so the namespace inventory accepts events about it.

    :param kubernetes: Bound Kubernetes service of the recorded installation.
    :param deadline: Shared delete deadline.
    :param scope: Run-owned namespace, recorded cluster UID and namespace UID.
    :param agent: Recorded Agent registration.
    :param resources: Recorded resource rows.
    :returns: The deleted Lease UID, or an empty set when the record has a Lease row
        or no such Lease exists.
    :raises KubernetesError: The namespace changed, or the Lease identity or its
        deletion cannot be verified.
    """  # noqa: DOC503 - Shared deadline and delete propagate their errors.
    if any(item.kind == LEASE_KIND for item in resources):
        return frozenset()
    namespace, cluster_uid, namespace_uid = scope
    if deadline.call(read_agent_cluster_scope, kubernetes, namespace) != (
        cluster_uid,
        namespace_uid,
    ):
        raise KubernetesError("agent_delete_scope_mismatch")
    lease = deadline.call(read_agent_lease, kubernetes, namespace, agent.id)
    if lease is None:
        return frozenset()
    _delete_resource(
        kubernetes,
        deadline,
        _Resource(
            {},
            LEASE_KIND,
            agent_lease_name(agent.id),
            namespace,
            lease.uid,
            COORDINATION_API_VERSION,
            True,
        ),
    )
    return frozenset((lease.uid,))


@dataclass(frozen=True, slots=True)
class _DeleteBefore:
    store: AgentRecoveryStore
    provider: AgentProvider
    agent: AgentRegistration
    token: AgentToken
    agent_present: bool
    token_present: bool
    release_present: bool
    resources: tuple[_Resource, ...]
    present: frozenset[str]
    cluster_uid: str
    namespace_uid: str
    marker_names: frozenset[str]
    resume: bool


def _delete_deadline(timeout: float) -> RequestDeadline:
    if not math.isfinite(timeout) or timeout <= 0:
        raise KubernetesError("agent_delete_timeout")
    return RequestDeadline(time.monotonic() + timeout, time.monotonic)


def _delete_digest(
    gitlab: GitLabService, before: _DeleteBefore, record: RecoveryRecord
) -> str:
    skill = gitlab.session.skill
    return plan_digest(
        {
            "operation": DELETE,
            "ownership": record.document,
            "journal_uid": record.uid,
            "journal_version": record.resource_version,
            "present_uids": sorted(before.present),
            "agent_present": before.agent_present,
            "token_present": before.token_present,
            "release_present": before.release_present,
            "candidate_revision": skill["revision"]["value"],
            "functional_digest": skill["functional_digest"],
        }
    )


def plan_tracked_delete(
    gitlab: GitLabService,
    kubernetes: KubernetesService,
    selection: ConnectSelection,
    record: RecoveryRecord,
    *,
    timeout: float,
) -> str:
    """Read exact tracked identities and return a confirmation digest without writes.

    :param gitlab: Credential-bound project and installed candidate.
    :param kubernetes: Verified Kubernetes context for the same journal.
    :param selection: Original run, Agent, and namespace resolved independently.
    :param record: Exact recovery journal returned by native lookup.
    :param timeout: Bounded remaining time for the preflight reads.
    :returns: Digest of the observed deletion intent and native state.
    """  # noqa: DOC503 - Native reads and deadline checks propagate failures.
    before = _read_delete(
        gitlab,
        kubernetes,
        selection,
        record,
        deadline=_delete_deadline(timeout),
        installation_only=False,
    )
    return _delete_digest(gitlab, before, record)


def delete_tracked_agent(
    gitlab: GitLabService,
    kubernetes: KubernetesService,
    selection: ConnectSelection,
    record: RecoveryRecord,
    *,
    timeout: float,
    confirmation: str | None = None,
) -> AgentCleanup:
    """Remove an independently selected, confirmed Agent by original IDs and UIDs.

    :param gitlab: Credential-bound exact configuration project.
    :param kubernetes: Selected credential-bound cluster service.
    :param selection: Confirmed Agent, project, run, namespace, and release.
    :param record: Confirmed direct read of the fully tracked native recovery record.
    :param timeout: Remaining operation budget in seconds.
    :param confirmation: Optional digest required by the installed command before writes.
    :returns: Original ownership, reconciled resource states, and logical absence.
    :raises KubernetesError: Ownership, restoration state, or cleanup is unverified.
    :raises GitLabAPIError: Native GitLab identity or deletion cannot be verified.
    :raises HelmError: Native release history or storage identity is invalid.
    :raises APIError: The operation deadline expires.
    """  # noqa: DOC502,DOC503 - Shared deletion propagates native and deadline failures.
    return _delete_tracked(
        gitlab,
        kubernetes,
        selection,
        record,
        timeout=timeout,
        installation_only=False,
        confirmation=confirmation,
    )


def compensate_installed_agent(
    gitlab: GitLabService,
    kubernetes: KubernetesService,
    selection: ConnectSelection,
    record: RecoveryRecord,
    *,
    timeout: float,
) -> AgentCleanup:
    """Compensate a fully journaled install that did not establish KAS proof.

    :param gitlab: Credential-bound exact configuration project.
    :param kubernetes: Selected credential-bound cluster service.
    :param selection: Agent, project, run, namespace, and release under recovery.
    :param record: Exact PENDING install journal or its DELETING continuation.
    :param timeout: Bounded remaining time for compensation.
    :returns: Former owned identities and independent verified absence.
    :raises KubernetesError: Original native installation or cleanup is unverified.
    """  # noqa: DOC502,DOC503 - Shared deletion propagates native failures.
    return _delete_tracked(
        gitlab, kubernetes, selection, record, timeout=timeout, installation_only=True
    )


def _read_delete(
    gitlab: GitLabService,
    kubernetes: KubernetesService,
    selection: ConnectSelection,
    record: RecoveryRecord,
    *,
    deadline: RequestDeadline,
    installation_only: bool,
) -> _DeleteBefore:
    document = record.document
    scope = selection.scope
    state = document.get("state")
    resume = state == "DELETING"
    allowed = (
        ("PENDING", "DELETING")
        if installation_only
        else ("CONNECTED", "VERIFIED", "DELETING")
    )
    if state not in allowed:
        raise KubernetesError(_RECORD_MISMATCH)
    expected = {
        "origin": gitlab.session.origin.rstrip("/"),
        "project_id": selection.project.id,
        "project_path": selection.project.path,
        "agent_name": selection.agent_name,
        "run_id": scope.run_id,
        "namespace": scope.namespace,
        "release": release_name(scope, selection.agent_name),
        "ownership_record_uid": record.uid,
        "ownership_record_name": agent_recovery_name(selection),
        CREDENTIAL_RESOURCE_NAME: agent_secret_name(selection),
    }
    if (
        any(document.get(key) != value for key, value in expected.items())
        or selection.release != expected["release"]
        or gitlab.target_id != selection.project.id
        or not record.uid
        or not record.resource_version
    ):
        raise KubernetesError(_RECORD_MISMATCH)
    try:
        if (
            not verified_agent_owner(document)
            or json_object(document.get("created")).get("namespace")
            is not scope.owns_namespace
        ):
            raise KubernetesError(_RECORD_MISMATCH)
        cluster_uid = json_string(document.get("cluster_uid"))
        namespace_uid = json_string(document.get("namespace_uid"))
        release = HelmRelease(
            selection.release,
            scope.namespace,
            json_integer(document.get("release_revision")),
            "deployed",
        )
        storage = HelmStorageIdentity(
            json_string(document.get("release_storage_name")),
            json_string(document.get("release_storage_uid")),
        )
        agent = AgentRegistration(
            json_integer(document.get("agent_id")),
            selection.agent_name,
            selection.project.id,
        )
        token = AgentToken(
            json_integer(document.get(CREDENTIAL_ID)),
            json_string(document.get(CREDENTIAL_NAME)),
            agent.id,
        )
        resources = _original_resources(
            document, scope.namespace, installation_only=installation_only
        )
    except (TypeError, ValueError):
        raise KubernetesError(_RECORD_MISMATCH) from None
    if storage.name != helm_storage_name(release):
        raise KubernetesError(_RECORD_MISMATCH)
    if deadline.call(read_agent_cluster_scope, kubernetes, scope.namespace) != (
        cluster_uid,
        namespace_uid,
    ):
        raise KubernetesError("agent_delete_scope_mismatch")
    store = AgentRecoveryStore(
        kubernetes, selection, gitlab.session.origin, cluster_uid, namespace_uid
    )
    if deadline.call(store.read) != record:
        raise KubernetesError("agent_delete_journal_changed")

    provider = AgentProvider(gitlab)
    agent_present, token_present = _provider_state(
        provider, selection, agent, token, deadline, resume=resume
    )
    helm = HelmClient(kubernetes)
    current_release = helm.status(
        release.name, release.namespace, timeout=deadline.remaining()
    )
    current_storage = deadline.call(read_helm_storage, kubernetes, release)
    if (current_release != release or current_storage != storage) and not (
        resume and current_release is None and current_storage is None
    ):
        raise KubernetesError("agent_delete_release_mismatch")

    marker_names = frozenset(
        (scope.namespace, store.name, agent_secret_name(selection))
    )
    present = {
        item.uid
        for item in resources
        if deadline.call(_native_present, kubernetes, item, scope.run_id, marker_names)
    }
    required = {
        item.uid
        for item in resources
        if item.kind not in AGENT_RUNTIME_KINDS
        and (not resume or not item.created or item.kind in ("ConfigMap", "Namespace"))
    }
    if not required <= present:
        raise KubernetesError(_RESOURCE_MISMATCH)
    return _DeleteBefore(
        store,
        provider,
        agent,
        token,
        agent_present,
        token_present,
        current_release is not None,
        resources,
        frozenset(present),
        cluster_uid,
        namespace_uid,
        marker_names,
        resume,
    )


def _delete_tracked(
    gitlab: GitLabService,
    kubernetes: KubernetesService,
    selection: ConnectSelection,
    record: RecoveryRecord,
    *,
    timeout: float,
    installation_only: bool,
    confirmation: str | None = None,
) -> AgentCleanup:
    deadline = _delete_deadline(timeout)
    before = _read_delete(
        gitlab,
        kubernetes,
        selection,
        record,
        deadline=deadline,
        installation_only=installation_only,
    )
    if (
        confirmation is not None
        and _delete_digest(gitlab, before, record) != confirmation
    ):
        raise KubernetesError("confirm_plan_mismatch_rerun_live_plan")

    scope = selection.scope
    store, provider = before.store, before.provider
    agent, token = before.agent, before.token
    resources = before.resources
    present = before.present
    cluster_uid, namespace_uid = before.cluster_uid, before.namespace_uid
    marker_names = before.marker_names
    if before.resume:
        if deadline.call(store.read) != record:
            raise KubernetesError("agent_delete_journal_changed")
    else:
        deadline.call(store.replace, record, {**record.document, "state": "DELETING"})

    if before.token_present:
        deadline.call(provider.delete_token, agent.id, token)
    if before.agent_present:
        deadline.call(provider.delete_agent, agent)
    for kind in _DELETE_ORDER:
        for item in resources:
            if item.kind == kind and item.uid in present:
                _delete_resource(kubernetes, deadline, item)

    if scope.owns_namespace:
        verify_agent_namespace_cleanup(
            kubernetes,
            scope.namespace,
            namespace_uid,
            scope.run_id,
            frozenset(item.uid for item in resources)
            | _unrecorded_lease(
                kubernetes,
                deadline,
                (scope.namespace, cluster_uid, namespace_uid),
                agent,
                resources,
            ),
            timeout=deadline.remaining(),
        )
        namespace = next(item for item in resources if item.kind == "Namespace")
        _delete_resource(kubernetes, deadline, namespace)
    else:
        journal = next(item for item in resources if item.kind == "ConfigMap")
        _delete_resource(kubernetes, deadline, journal)

    if (
        deadline.call(provider.read_agent, agent.id) is not None
        or deadline.call(provider.read_token, agent.id, token.id) is not None
    ):
        raise KubernetesError(_ABSENCE_UNVERIFIED)
    final = []
    for item in resources:
        alive = deadline.call(
            _native_present, kubernetes, item, scope.run_id, marker_names
        )
        if alive is item.created:
            raise KubernetesError(_ABSENCE_UNVERIFIED)
        final.append({**item.row, "state": PRESENT if alive else ABSENT})
    absence = verify_agent_logical_absence(
        gitlab, kubernetes, selection, timeout=deadline.remaining()
    )
    if absence.cluster_uid != cluster_uid:
        raise KubernetesError("agent_delete_scope_mismatch")
    return AgentCleanup(
        record,
        tuple(final),
        absence,
        before.agent_present,
        before.token_present,
        present,
        before.release_present,
    )
