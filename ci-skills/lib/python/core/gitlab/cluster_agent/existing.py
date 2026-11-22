"""Prove the exact current installation before an Agent can be reused."""

from __future__ import annotations

import math
import time
from collections.abc import Sequence

from lib.python.core.cli_api import RequestDeadline
from lib.python.core.gitlab.cluster_agent.connection import (
    AgentConnectivity,
    read_agent_connectivity,
)
from lib.python.core.gitlab.cluster_agent.evidence import verified_agent_owner
from lib.python.core.gitlab.cluster_agent.installation import (
    ObservedChartResource,
    agent_chart_inventory,
    agent_chart_values,
    observe_chart_resources,
)
from lib.python.core.gitlab.cluster_agent.provider import (
    AgentProvider,
    AgentToken,
    KASMetadata,
)
from lib.python.core.gitlab.cluster_agent.recovery import (
    CREDENTIAL_ID,
    CREDENTIAL_NAME,
    CREDENTIAL_RESOURCE_NAME,
    CREDENTIAL_RESOURCE_UID,
    AgentRecoveryStore,
    RecoveryRecord,
)
from lib.python.core.gitlab.cluster_agent.registration import observed_resource
from lib.python.core.gitlab.cluster_agent.secret import AgentSecretStore
from lib.python.core.gitlab.cluster_agent.selection import (
    APP_VERSION,
    CHART_SHA256,
    ConnectSelection,
)
from lib.python.core.gitlab.gitlab_session import GitLabService
from lib.python.core.helm.native import HelmClient, HelmRelease, VerifiedChart
from lib.python.core.helm.storage import HelmStorageIdentity, read_helm_storage
from lib.python.core.json_types import (
    JSONObject,
    json_integer,
    json_object,
    json_objects,
    json_string,
)
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.oci import ImageManifestIdentity


def _chart_uids_match(
    document: JSONObject, native: Sequence[ObservedChartResource]
) -> bool:
    kinds = {item.identity.kind for item in native}
    stored = [
        item
        for item in json_objects(document.get("resources"))
        if item.get("kind") in kinds
    ]
    return (
        bool(native)
        and len(stored) == len(native)
        and all(
            stored.count(
                observed_resource(
                    item.identity.kind,
                    item.identity.name,
                    item.identity.namespace,
                    item.uid,
                    created=True,
                    api_version=item.identity.api_version,
                )
            )
            == 1
            for item in native
        )
    )


def verify_existing_agent(
    gitlab: GitLabService,
    kubernetes: KubernetesService,
    selection: ConnectSelection,
    record: RecoveryRecord,
    kas: KASMetadata,
    chart: VerifiedChart,
    image: ImageManifestIdentity,
    *,
    timeout: float,
) -> AgentConnectivity:
    """Verify an existing connected run with no provider or cluster writes.

    :param gitlab: Access-verified configuration project and credential.
    :param kubernetes: Service bound to the selected Kubernetes authority.
    :param selection: Already resolved consumer-specific names and exact scope.
    :param record: Current independently read native Agent recovery record.
    :param kas: Current advertised KAS metadata from the same GitLab authority.
    :param chart: Approved pinned archive used to check the original chart render.
    :param image: Manifest or index and runtime digests read from the pinned image.
    :param timeout: Remaining operation budget in seconds.
    :returns: Same recovery record with the currently verified active proof.
    :raises KubernetesError: Desired state, native ownership, or scope differs.
    :raises HelmError: A native Helm read could not be verified.
    :raises GitLabAPIError: A native GitLab read could not be verified.
    :raises APIError: The shared verification deadline elapsed.
    """  # noqa: DOC503 - Native reads and the deadline propagate their documented errors.
    if not math.isfinite(timeout) or timeout <= 0:
        raise KubernetesError("agent_existing_timeout")
    deadline = RequestDeadline(time.monotonic() + timeout, time.monotonic)
    document = record.document
    scope = selection.scope
    expected = {
        "origin": gitlab.session.origin.rstrip("/"),
        "project_id": selection.project.id,
        "project_path": selection.project.path,
        "agent_name": selection.agent_name,
        "run_id": scope.run_id,
        "namespace": scope.namespace,
        "release": selection.release,
        "ownership_record_uid": record.uid,
        "image_digest": image.digest,
        "app_version": APP_VERSION,
        "chart_sha256": CHART_SHA256,
    }
    if (
        document.get("state") not in ("CONNECTED", "VERIFIED")
        or any(document.get(key) != value for key, value in expected.items())
        or chart.sha256 != CHART_SHA256
        or image.digest not in image.runtime_digests
        or gitlab.target_id != selection.project.id
        or not record.uid
        or not record.resource_version
    ):
        raise KubernetesError("agent_existing_record_mismatch")
    try:
        if (
            not verified_agent_owner(document)
            or json_object(document.get("created")).get("namespace")
            is not scope.owns_namespace
        ):
            raise KubernetesError("agent_existing_record_mismatch")
        cluster_uid = json_string(document.get("cluster_uid"))
        namespace_uid = json_string(document.get("namespace_uid"))
        credential_uid = json_string(document.get(CREDENTIAL_RESOURCE_UID))
        credential_name = json_string(document.get(CREDENTIAL_RESOURCE_NAME))
        storage = HelmStorageIdentity(
            json_string(document.get("release_storage_name")),
            json_string(document.get("release_storage_uid")),
        )
        revision = json_integer(document.get("release_revision"))
        token = AgentToken(
            json_integer(document.get(CREDENTIAL_ID)),
            json_string(document.get(CREDENTIAL_NAME)),
            json_integer(document.get("agent_id")),
        )
    except (TypeError, ValueError):
        raise KubernetesError("agent_existing_record_mismatch") from None

    helm = HelmClient(kubernetes)
    release = HelmRelease(selection.release, scope.namespace, revision, "deployed")
    if (
        helm.status(release.name, release.namespace, timeout=deadline.remaining())
        != release
    ):
        raise KubernetesError("agent_existing_release_mismatch")
    if deadline.call(read_helm_storage, kubernetes, release) != storage:
        raise KubernetesError("agent_existing_storage_mismatch")
    values = agent_chart_values(selection, kas, credential_name, image.digest)
    if not helm.values_match(release, values, timeout=deadline.remaining()):
        raise KubernetesError("agent_existing_values_mismatch")
    rendered = helm.render(
        release.name, release.namespace, chart, values, timeout=deadline.remaining()
    )
    inventory = agent_chart_inventory(rendered, selection)
    live = deadline.call(observe_chart_resources, kubernetes, selection, inventory)
    if not _chart_uids_match(document, live):
        raise KubernetesError("agent_existing_inventory_mismatch")

    provider = AgentProvider(gitlab)
    if deadline.call(
        provider.read_token, token.agent_id, token.id
    ) != token or deadline.call(provider.list_tokens, token.agent_id) != (token,):
        raise KubernetesError("agent_existing_token_mismatch")
    credential = AgentSecretStore(kubernetes, selection)
    if (
        not credential_uid
        or credential.name != credential_name
        or deadline.call(credential.read) != credential_uid
    ):
        raise KubernetesError("agent_existing_credential_mismatch")
    connections = read_agent_connectivity(
        gitlab, kubernetes, selection, record, image, timeout=deadline.remaining()
    )
    store = AgentRecoveryStore(
        kubernetes, selection, gitlab.session.origin, cluster_uid, namespace_uid
    )
    if deadline.call(store.read) != record:
        raise KubernetesError("agent_existing_journal_changed")
    return AgentConnectivity(record, connections)
