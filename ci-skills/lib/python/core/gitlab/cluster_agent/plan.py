"""Prepare the native before-state needed to confirm a fresh Agent install."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final

from lib.python.core.cli_api import RequestDeadline
from lib.python.core.gitlab.cluster_agent.absence import (
    LogicalAgentAbsence,
    verify_agent_logical_absence,
)
from lib.python.core.gitlab.cluster_agent.authority import AgentAuthorities
from lib.python.core.gitlab.cluster_agent.connection import AgentConnectivity
from lib.python.core.gitlab.cluster_agent.existing import verify_existing_agent
from lib.python.core.gitlab.cluster_agent.installation import (
    agent_chart_inventory,
    agent_chart_values,
    verify_chart_absent,
)
from lib.python.core.gitlab.cluster_agent.recovery import RecoveryRecord
from lib.python.core.gitlab.cluster_agent.secret import agent_secret_name
from lib.python.core.gitlab.cluster_agent.selection import (
    CHART_SHA256,
    CHART_VERSION,
    IMAGE_TAG,
    ConnectSelection,
)
from lib.python.core.helm.native import HelmClient, VerifiedChart
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.gitlab_agent import read_agent_cluster_scope
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.oci import ImageManifestIdentity
from lib.python.core.report import plan_digest

_CONNECT: Final = "connect"


@dataclass(frozen=True, slots=True)
class FreshConnectPlan:
    """Bind the exact fresh namespace observation and confirmation digest.

    .. attribute :: namespace_uid
        :type: str | None

        Native UID of the borrowed namespace, or None for a confirmed absence.

    .. attribute :: digest
        :type: str

        Fingerprint of the fresh installation intent and independent observations.
    """

    namespace_uid: str | None
    digest: str

    def require_confirmation(self, supplied: str) -> None:
        """Reject a stale digest before a caller can start the installation.

        :param supplied: Digest obtained from the previous read-only invocation.
        :raises ValueError: The new observation differs from the supplied digest.
        """
        if supplied != self.digest:
            raise ValueError("confirm_plan_mismatch_rerun_live_plan")


def _namespace_uid(
    authorities: AgentAuthorities,
    selection: ConnectSelection,
    deadline: RequestDeadline,
) -> str | None:
    kubernetes = authorities.kubernetes
    namespace = selection.scope.namespace
    if selection.scope.owns_namespace:
        if (
            kubernetes.read("Namespace", namespace, "", timeout=deadline.remaining())
            is not None
        ):
            raise KubernetesError("agent_plan_namespace_collision")
        return None
    cluster_uid, namespace_uid = deadline.call(
        read_agent_cluster_scope, kubernetes, namespace
    )
    if cluster_uid != authorities.cluster_uid:
        raise KubernetesError("agent_plan_cluster_changed")
    return namespace_uid


def prepare_fresh_connect(
    authorities: AgentAuthorities,
    selection: ConnectSelection,
    chart: VerifiedChart,
    image: ImageManifestIdentity,
    *,
    deadline: RequestDeadline,
) -> FreshConnectPlan:
    """Bind read-only provider observations to a single fresh-install digest.

    :param authorities: Native project, KAS, cluster and candidate read-back.
    :param selection: Exact native project and approved consumer run names.
    :param chart: Verified archive of the approved chart.
    :param image: Runtime-verified digest and platform manifests.
    :param deadline: Shared absolute deadline for read-only provider operations.
    :returns: Immutable result to compare with a fresh observation before apply.
    :raises KubernetesError: Identity mismatch, active resources or a namespace collision.
    """  # noqa: DOC503 - Shared reads and chart validation retain native errors.
    if authorities.project != selection.project:
        raise KubernetesError("agent_plan_project_mismatch")
    if chart.sha256 != CHART_SHA256:
        raise KubernetesError("agent_plan_chart_pin_mismatch")

    kubernetes = authorities.kubernetes
    scope = selection.scope
    namespace_uid = _namespace_uid(authorities, selection, deadline)
    absence = verify_agent_logical_absence(
        authorities.gitlab, kubernetes, selection, timeout=deadline.remaining()
    )
    if absence.cluster_uid != authorities.cluster_uid:
        raise KubernetesError("agent_plan_cluster_changed")
    values = agent_chart_values(
        selection, authorities.kas, agent_secret_name(selection), image.digest
    )
    manifest = HelmClient(kubernetes).render(
        selection.release, scope.namespace, chart, values, timeout=deadline.remaining()
    )
    inventory = agent_chart_inventory(manifest, selection)
    deadline.call(verify_chart_absent, kubernetes, inventory)
    if _namespace_uid(authorities, selection, deadline) != namespace_uid:
        raise KubernetesError("agent_plan_namespace_changed")

    document: JSONObject = {
        **_binding(authorities, selection),
        "operation": _CONNECT,
        "namespace_uid": namespace_uid,
        "chart_version": CHART_VERSION,
        "chart_sha256": chart.sha256,
        "image": IMAGE_TAG,
        "image_digest": image.digest,
        "runtime_digests": sorted(image.runtime_digests),
        "values": values,
        "resources": [
            {
                "api_version": item.api_version,
                "kind": item.kind,
                "namespace": item.namespace,
                "name": item.name,
            }
            for item in inventory
        ],
    }
    return FreshConnectPlan(namespace_uid, plan_digest(document))


def _binding(authorities: AgentAuthorities, selection: ConnectSelection) -> JSONObject:
    session = authorities.gitlab.session
    configuration = authorities.kubernetes.configuration
    scope = selection.scope
    kas = authorities.kas
    return {
        "origin": session.origin,
        "project_id": selection.project.id,
        "project_path": selection.project.path,
        "agent_name": selection.agent_name,
        "run_id": scope.run_id,
        "namespace": scope.namespace,
        "owns_namespace": scope.owns_namespace,
        "release": selection.release,
        "cluster_uid": authorities.cluster_uid,
        "context": configuration.context,
        "server": configuration.server,
        "gitlab_version": kas.gitlab_version,
        "kas_version": kas.version,
        "kas_address": kas.address,
        "kas_proxy_url": kas.proxy_url,
        "candidate_revision": session.skill["revision"]["value"],
        "functional_digest": session.skill["functional_digest"],
    }


def _observed_digest(document: JSONObject, confirmation: str | None) -> str:
    digest = plan_digest(document)
    if confirmation is not None and digest != confirmation:
        raise ValueError("confirm_plan_mismatch_rerun_live_plan")
    return digest


def observe_repeat_connect(
    authorities: AgentAuthorities,
    selection: ConnectSelection,
    record: RecoveryRecord,
    chart: VerifiedChart,
    image: ImageManifestIdentity,
    *,
    deadline: RequestDeadline,
    confirmation: str | None = None,
) -> tuple[str, AgentConnectivity]:
    """Confirm the same existing Agent installation without any writes.

    :param authorities: Selected GitLab and Kubernetes with candidate provenance.
    :param selection: Original native project and recorded Agent run selector.
    :param record: Exact original journal from native lookup.
    :param chart: Approved chart archive for desired-state verification.
    :param image: Verified image root digest and admissible runtime digests.
    :param deadline: Shared absolute deadline for the native proof.
    :param confirmation: Digest from an earlier read-only plan when applying.
    :returns: Confirmation digest and current independent connectivity proof.
    """  # noqa: DOC503 - Verification and supplied confirmation can fail.
    result = verify_existing_agent(
        authorities.gitlab,
        authorities.kubernetes,
        selection,
        record,
        authorities.kas,
        chart,
        image,
        timeout=deadline.remaining(),
    )
    document: JSONObject = {
        **_binding(authorities, selection),
        "operation": _CONNECT,
        "ownership": result.record.document,
        "journal_uid": result.record.uid,
        "journal_version": result.record.resource_version,
        "connections": [
            asdict(item)
            for item in sorted(
                result.connections, key=lambda item: item.connection.connection_id
            )
        ],
    }
    return _observed_digest(document, confirmation), result


def observe_repeat_delete(
    authorities: AgentAuthorities,
    selection: ConnectSelection,
    *,
    deadline: RequestDeadline,
    confirmation: str | None = None,
) -> tuple[str, LogicalAgentAbsence]:
    """Prove logical absence for a delete whose recovery record is absent.

    :param authorities: Selected GitLab and Kubernetes with candidate provenance.
    :param selection: Exact Agent name, run ID, and logical release selector.
    :param deadline: Shared absolute deadline for the native reads.
    :param confirmation: Digest from an earlier read-only plan when applying.
    :returns: Confirmation digest and independent verified logical absence.
    :raises KubernetesError: The logical absence proof selects another cluster.
    """  # noqa: DOC503 - Verification and supplied confirmation can fail.
    result = verify_agent_logical_absence(
        authorities.gitlab,
        authorities.kubernetes,
        selection,
        timeout=deadline.remaining(),
    )
    if result.cluster_uid != authorities.cluster_uid:
        raise KubernetesError("agent_plan_cluster_changed")
    document: JSONObject = {
        **_binding(authorities, selection),
        "operation": "delete",
        "logical_absence": True,
    }
    return _observed_digest(document, confirmation), result
