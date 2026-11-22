"""Render the approved Agent chart and observe only its exact resources."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from lib.python.core.gitlab.cluster_agent.provider import KASMetadata
from lib.python.core.gitlab.cluster_agent.selection import APP_VERSION, ConnectSelection
from lib.python.core.helm.resources import RenderedResource, rendered_resources
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.kubernetes.objects import KubernetesObject

# Reserve space for the chart's -v2 suffix and the controller-generated Pod suffixes.
_WORKLOAD_NAME_LIMIT: Final = 43
_WORKLOAD_ROLE: Final = "agent"
_DIGEST: Final = re.compile(r"sha256:[a-f0-9]{64}")
_AGENT_TYPES: Final = MappingProxyType(
    {
        ("apps/v1", "Deployment"): True,
        ("v1", "ServiceAccount"): True,
        ("v1", "Service"): True,
        ("rbac.authorization.k8s.io/v1", "ClusterRoleBinding"): False,
    }
)
_HELM_MANAGER: Final = "app.kubernetes.io/managed-by"
_HELM_RELEASE_NAME: Final = "meta.helm.sh/release-name"
_HELM_RELEASE_NAMESPACE: Final = "meta.helm.sh/release-namespace"


def agent_chart_values(
    selection: ConnectSelection, kas: KASMetadata, credential_name: str, digest: str
) -> JSONObject:
    """Select nonsecret Helm values for a confirmed Agent installation.

    :param selection: Native project and consumer-specific run names.
    :param kas: Address discovered through verified GitLab metadata.
    :param credential_name: Exact name of the separately created token Secret.
    :param digest: Runtime-verified immutable OCI index or manifest digest.
    :returns: Values using Secret-name indirection and the verified chart image.
    :raises ValueError: Digest or selected credential resource name is missing.
    """
    if not credential_name or _DIGEST.fullmatch(digest) is None:
        raise ValueError("agent_chart_inputs_invalid")
    workload = selection.scope.bounded_name(
        _WORKLOAD_ROLE, limit=_WORKLOAD_NAME_LIMIT, prefix=selection.agent_name
    )
    return {
        "fullnameOverride": workload,
        "image": {"tag": f"{APP_VERSION}@{digest}"},
        "config": {
            "kasAddress": kas.address,
            "secretName": credential_name,
            "operational_container_scanning": {"enabled": False},
        },
    }


def agent_chart_inventory(
    manifest: str, selection: ConnectSelection
) -> tuple[RenderedResource, ...]:
    """Read the pinned Agent render without using names as ownership evidence.

    :param manifest: Native Helm YAML render from the verified chart.
    :param selection: Confirmed consumer run and exact selected namespace.
    :returns: Exact object identities needed for absence and installation read-back.
    :raises KubernetesError: Any expected chart object kind is missing or repeated.
    """
    resources = rendered_resources(
        manifest, namespace=selection.scope.namespace, accepted=_AGENT_TYPES
    )
    if len(resources) != len(_AGENT_TYPES) or len(
        {item.kind for item in resources}
    ) != len(_AGENT_TYPES):
        raise KubernetesError("agent_chart_inventory_mismatch")
    return resources


def _read_rendered(
    service: KubernetesService, resource: RenderedResource
) -> JSONObject | None:
    group, separator, version = resource.api_version.partition("/")
    if separator:
        kind = f"{resource.kind.lower()}.{version}.{group}"
    else:
        kind = resource.kind
    return service.read(kind, resource.name, resource.namespace)


def verify_chart_absent(
    service: KubernetesService, resources: Sequence[RenderedResource]
) -> None:
    """Refuse installation over any object already present at a rendered identity.

    :param service: Existing credential-bound Kubernetes transport.
    :param resources: Nonempty parsed identity inventory from the exact render.
    :raises KubernetesError: An object already occupies an intended identity.
    """
    if not resources:
        raise KubernetesError("agent_chart_inventory_missing")
    for resource in resources:
        if _read_rendered(service, resource) is not None:
            raise KubernetesError("agent_chart_resource_collision")


@dataclass(frozen=True)
class ObservedChartResource:
    """Pair a Helm-rendered identity with an independent native UID.

    .. attribute :: identity
        :type: RenderedResource

        Exact resource selected by the chart render.

    .. attribute :: uid
        :type: str

        UID from the actual Helm-managed object.
    """

    identity: RenderedResource
    uid: str


def observe_chart_resources(
    service: KubernetesService,
    selection: ConnectSelection,
    resources: Sequence[RenderedResource],
) -> tuple[ObservedChartResource, ...]:
    """Bind newly installed resources to the exact native Helm ownership markers.

    :param service: Existing credential-bound Kubernetes transport.
    :param selection: Exact release and namespace selected before installation.
    :param resources: Nonempty identity inventory independently verified absent.
    :returns: Immutable resource identities for subsequent connection and cleanup.
    :raises KubernetesError: An expected object, UID, or Helm owner is unverified.
    """
    if not resources:
        raise KubernetesError("agent_chart_inventory_missing")
    result = []
    for identity in resources:
        document = _read_rendered(service, identity)
        if document is None:
            raise KubernetesError("agent_chart_resource_absent")
        item = KubernetesObject(document)
        try:
            metadata = item.metadata
            annotations = metadata.object("annotations")
            if (
                item.text("kind") != identity.kind
                or item.text("apiVersion") != identity.api_version
                or metadata.text("name") != identity.name
                or (metadata.text("namespace") or "") != identity.namespace
                or metadata.text("deletionTimestamp") is not None
                or metadata.object("labels").text(_HELM_MANAGER) != "Helm"
                or annotations.text(_HELM_RELEASE_NAME) != selection.release
                or annotations.text(_HELM_RELEASE_NAMESPACE)
                != selection.scope.namespace
            ):
                raise KubernetesError("agent_chart_resource_owner_mismatch")
            result.append(
                ObservedChartResource(identity, metadata.required_text("uid"))
            )
        except TypeError:
            raise KubernetesError("agent_chart_resource_invalid") from None
    return tuple(result)
