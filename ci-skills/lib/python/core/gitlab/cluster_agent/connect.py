"""Compose the fresh Agent registration and first Helm installation."""

from __future__ import annotations

import math
import time

from lib.python.core.cli_api import APIError, RequestDeadline
from lib.python.core.gitlab.cluster_agent.connection import (
    AgentConnectivity,
    confirm_fresh_agent_connection,
)
from lib.python.core.gitlab.cluster_agent.delete import compensate_installed_agent
from lib.python.core.gitlab.cluster_agent.installation import (
    agent_chart_inventory,
    agent_chart_values,
    observe_chart_resources,
    verify_chart_absent,
)
from lib.python.core.gitlab.cluster_agent.provider import KASMetadata
from lib.python.core.gitlab.cluster_agent.recovery import (
    CREDENTIAL_RESOURCE_UID,
    AgentRecoveryStore,
    RecoveryRecord,
)
from lib.python.core.gitlab.cluster_agent.registration import (
    observed_resource,
    persist_observation,
    register_fresh_agent,
)
from lib.python.core.gitlab.cluster_agent.secret import AgentSecretStore
from lib.python.core.gitlab.cluster_agent.selection import (
    CHART_SHA256,
    ConnectSelection,
)
from lib.python.core.gitlab.gitlab_session import GitLabService
from lib.python.core.helm.native import (
    HelmClient,
    HelmError,
    HelmRelease,
    VerifiedChart,
)
from lib.python.core.helm.storage import read_helm_storage
from lib.python.core.json_types import JSONObject, json_string
from lib.python.core.kubernetes.gitlab_agent import read_agent_cluster_scope
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.oci import ImageManifestIdentity


def install_fresh_agent(
    gitlab: GitLabService,
    kubernetes: KubernetesService,
    selection: ConnectSelection,
    pending: JSONObject,
    kas: KASMetadata,
    chart: VerifiedChart,
    image_digest: str,
    *,
    timeout: float,
) -> RecoveryRecord:
    """Register once, install the approved chart, and record its native UIDs.

    The caller has confirmed the fresh plan and verified the selected namespace,
    KAS metadata, archive and image provenance. The result remains PENDING until
    a current GitLab connection can be correlated to the owned running Pod.

    :param gitlab: Access-verified GitLab service for the selected project.
    :param kubernetes: Kubernetes service bound to the confirmed target.
    :param selection: Approved consumer project, scope, Agent and release names.
    :param pending: Matched fresh recovery intent containing the selected image pin.
    :param kas: Read-back KAS metadata from the selected GitLab authority.
    :param chart: The actual chart archive and approved SHA-256 pin.
    :param image_digest: Immutable OCI digest previously resolved from the registry.
    :param timeout: Remaining operation time budget in seconds.
    :returns: Latest PENDING recovery record with verified release and object UIDs.
    :raises KubernetesError: Inputs, ownership, preflight or read-back are invalid.
    :raises HelmError: Native retrieval, render or install is not confirmed.
    :raises GitLabAPIError: Registration or credential issuance is not confirmed.
    """  # noqa: DOC503 - Native Helm and GitLab stages propagate their own errors.
    if chart.sha256 != CHART_SHA256:
        raise KubernetesError("agent_install_chart_pin_mismatch")
    if pending.get("image_digest") != image_digest:
        raise KubernetesError("agent_install_image_pin_mismatch")
    if not math.isfinite(timeout) or timeout <= 0:
        raise KubernetesError("agent_install_timeout")
    deadline = time.monotonic() + timeout

    def _remaining() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise KubernetesError("agent_install_timeout")
        return remaining

    try:
        cluster_uid = json_string(pending.get("cluster_uid"))
        namespace_uid = json_string(pending.get("namespace_uid"))
        if not cluster_uid or not namespace_uid:
            raise TypeError
    except TypeError:
        raise KubernetesError("agent_install_input_invalid") from None

    namespace = selection.scope.namespace
    helm = HelmClient(kubernetes)
    credential = AgentSecretStore(kubernetes, selection)
    values = agent_chart_values(selection, kas, credential.name, image_digest)
    first_release = HelmRelease(selection.release, namespace, 1, "deployed")

    def _storage_absent() -> None:
        if read_helm_storage(kubernetes, first_release) is not None:
            raise KubernetesError("agent_install_storage_already_present")

    if helm.status(selection.release, namespace, timeout=_remaining()) is not None:
        raise KubernetesError("agent_install_release_already_present")
    _storage_absent()
    rendered = helm.render(
        selection.release, namespace, chart, values, timeout=_remaining()
    )
    inventory = agent_chart_inventory(rendered, selection)
    verify_chart_absent(kubernetes, inventory)
    _remaining()
    record = register_fresh_agent(
        gitlab, kubernetes, selection, pending, deadline=RequestDeadline(deadline)
    )
    credential_uid = record.document.get(CREDENTIAL_RESOURCE_UID)
    if not isinstance(credential_uid, str) or not credential_uid:
        raise KubernetesError("agent_install_credential_mismatch")
    store = AgentRecoveryStore(
        kubernetes, selection, gitlab.session.origin, cluster_uid, namespace_uid
    )

    def _recheck(manifest: str) -> None:
        if read_agent_cluster_scope(kubernetes, namespace) != (
            cluster_uid,
            namespace_uid,
        ):
            raise KubernetesError("agent_install_scope_mismatch")
        if credential.read() != credential_uid:
            raise KubernetesError("agent_install_credential_mismatch")
        if agent_chart_inventory(manifest, selection) != inventory:
            raise KubernetesError("agent_install_render_mismatch")
        _storage_absent()
        verify_chart_absent(kubernetes, inventory)

    observed = helm.install_fresh(
        selection.release,
        namespace,
        chart,
        values,
        verify_absent=_recheck,
        timeout=_remaining(),
    )
    if observed != first_release:
        raise KubernetesError("agent_install_release_unverified")
    storage = read_helm_storage(kubernetes, observed)
    if storage is None:
        raise KubernetesError("agent_install_storage_unverified")
    record = persist_observation(
        store,
        record,
        {
            "release_revision": observed.revision,
            "release_storage_name": storage.name,
            "release_storage_uid": storage.uid,
        },
        "release",
        observed_resource("Secret", storage.name, namespace, storage.uid, created=True),
    )
    installed = observe_chart_resources(kubernetes, selection, inventory)
    return persist_observation(
        store,
        record,
        {},
        "release",
        *(
            observed_resource(
                item.identity.kind,
                item.identity.name,
                item.identity.namespace,
                item.uid,
                created=True,
                api_version=item.identity.api_version,
            )
            for item in installed
        ),
    )


def connect_fresh_agent(
    gitlab: GitLabService,
    kubernetes: KubernetesService,
    selection: ConnectSelection,
    pending: JSONObject,
    kas: KASMetadata,
    chart: VerifiedChart,
    image: ImageManifestIdentity,
    *,
    timeout: float,
) -> AgentConnectivity:
    """Carry one confirmed install through the native Agent connection proof.

    :param gitlab: Access-verified GitLab service for the selected project.
    :param kubernetes: Bound Kubernetes service for the selected cluster.
    :param selection: Exact consumer project, namespace and Agent identities.
    :param pending: Confirmed fresh recovery intent.
    :param kas: Verified advertised KAS metadata for the selected authority.
    :param chart: Official archive and approved chart digest.
    :param image: Verified registry manifest and eligible platform digests.
    :param timeout: Shared installation and connection time budget, in seconds.
    :returns: Durable CONNECTED record and the correlated native proof.
    :raises KubernetesError: Invalid time budget, ownership or connection evidence.
    :raises HelmError: The native Helm install could not be verified.
    :raises GitLabAPIError: Native registration, credential or KAS confirmation failed.
    :raises APIError: The shared timeout elapsed before success was verified.
    """  # noqa: DOC503 - Native operations and shared deadline propagate failures.
    if not math.isfinite(timeout) or timeout <= 0:
        raise KubernetesError("agent_connection_timeout")
    deadline = RequestDeadline(time.monotonic() + timeout, time.monotonic)
    installed = install_fresh_agent(
        gitlab,
        kubernetes,
        selection,
        pending,
        kas,
        chart,
        image.digest,
        timeout=deadline.remaining(),
    )
    try:
        return confirm_fresh_agent_connection(
            gitlab,
            kubernetes,
            selection,
            installed,
            image,
            timeout=deadline.remaining(),
        )
    except (APIError, KubernetesError, OSError, TypeError, ValueError):
        try:
            compensate_installed_agent(
                gitlab, kubernetes, selection, installed, timeout=deadline.remaining()
            )
        except (APIError, HelmError, KubernetesError, OSError, TypeError, ValueError):
            raise KubernetesError("agent_install_compensation_unverified") from None
        raise
