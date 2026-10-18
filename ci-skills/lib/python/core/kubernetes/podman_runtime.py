"""Shared Podman workload placement, resources, storage, and read-back.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Final

from kubernetes.utils.quantity import parse_quantity

from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.resources import matches

PRIVILEGED_CONSTRAINT: Final = "privileged"
DEFAULT_CPU_REQUEST: Final = "250m"
DEFAULT_CPU_LIMIT: Final = "1"
DEFAULT_MEMORY_REQUEST: Final = "256Mi"
DEFAULT_MEMORY_LIMIT: Final = "1Gi"
DEFAULT_STORAGE_SIZE: Final = "8Gi"
DEFAULT_SERVICE_ACCOUNT: Final = "default"
PODMAN_STORAGE_VOLUME: Final = "containers-storage"
PODMAN_RUNROOT_VOLUME: Final = "containers-runroot"
PODMAN_STORAGE_MOUNT: Final = "/var/lib/containers"
PODMAN_RUNROOT_MOUNT: Final = "/run/containers"


def _quantity(value: str, field_name: str) -> Decimal:
    """Parse a nonnegative Kubernetes resource amount.

    :param value: Caller-selected quantity including its optional unit suffix.
    :param field_name: Stable field used in validation failures.
    :returns: Finite nonnegative amount in the provider's base unit.
    :raises ValueError: The quantity has invalid syntax or is negative.
    """
    try:
        amount = parse_quantity(value.strip())
    except (ValueError, InvalidOperation) as exc:
        raise ValueError(f"{field_name}_invalid") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{field_name}_invalid")
    return amount


@dataclass(frozen=True)
class PodmanResources:
    """Bound requests, limits, and Podman storage size for one workload."""

    cpu_request: str = DEFAULT_CPU_REQUEST
    cpu_limit: str = DEFAULT_CPU_LIMIT
    memory_request: str = DEFAULT_MEMORY_REQUEST
    memory_limit: str = DEFAULT_MEMORY_LIMIT
    storage_size: str = DEFAULT_STORAGE_SIZE

    def __post_init__(self) -> None:
        """Validate quantities and request/limit ordering before planning.

        :raises ValueError: A quantity is malformed, negative, or exceeds its limit.
        """
        for resource in ("cpu", "memory"):
            request_name, limit_name = f"{resource}_request", f"{resource}_limit"
            for name in (request_name, limit_name):
                object.__setattr__(self, name, getattr(self, name).strip())
            request = _quantity(getattr(self, request_name), request_name)
            limit = _quantity(getattr(self, limit_name), limit_name)
            if request > limit:
                raise ValueError(f"{resource}_request_exceeds_limit")
        object.__setattr__(self, "storage_size", self.storage_size.strip())
        _quantity(self.storage_size, "storage_size")

    def document(self) -> JSONObject:
        """Render exact bounded resource inputs for plans and read-back.

        :returns: CPU, memory, and storage quantities selected by the caller.
        """
        return {
            "cpu_request": self.cpu_request,
            "cpu_limit": self.cpu_limit,
            "memory_request": self.memory_request,
            "memory_limit": self.memory_limit,
            "storage_size": self.storage_size,
        }


@dataclass(frozen=True)
class PodmanRuntimeSettings:
    """Select one exact node, account, resource set, and Podman storage layout."""

    node_name: str
    service_account: str = DEFAULT_SERVICE_ACCOUNT
    resources: PodmanResources = field(default_factory=PodmanResources)

    def __post_init__(self) -> None:
        """Normalize required names before manifest rendering.

        :raises ValueError: The selected node or ServiceAccount is blank.
        """
        for attribute in ("node_name", "service_account"):
            value = getattr(self, attribute)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"podman_{attribute}_required")
            object.__setattr__(self, attribute, value.strip())

    def plan(self) -> JSONObject:
        """Render all Podman workload inputs bound by confirmation.

        :returns: Exact placement, account, privilege, storage, and resources.
        """
        return {
            "node": self.node_name,
            "service_account": self.service_account,
            "privileged": True,
            "automount_service_account_token": False,
            "resources": self.resources.document(),
            "storage": {
                "graph_root": PODMAN_STORAGE_MOUNT,
                "run_root": PODMAN_RUNROOT_MOUNT,
            },
        }

    def pod_fields(self) -> JSONObject:
        """Render shared Pod placement and account fields.

        :returns: Native PodSpec fields common to probe and long-lived runner.
        """
        return {
            "nodeName": self.node_name,
            "serviceAccountName": self.service_account,
            "automountServiceAccountToken": False,
        }

    def container_fields(self) -> JSONObject:
        """Render shared privileged security and resource fields.

        :returns: Native container fields common to probe and runner.
        """
        return {
            "securityContext": {
                "privileged": True,
                "runAsUser": 0,
                "runAsNonRoot": False,
            },
            "resources": {
                "requests": {
                    "cpu": self.resources.cpu_request,
                    "memory": self.resources.memory_request,
                },
                "limits": {
                    "cpu": self.resources.cpu_limit,
                    "memory": self.resources.memory_limit,
                },
            },
        }

    def volume_mounts(self) -> Sequence[JSONObject]:
        """Render Podman graph-root and run-root mounts.

        :returns: Native volume mounts shared by probe and runner.
        """
        return (
            {"name": PODMAN_STORAGE_VOLUME, "mountPath": PODMAN_STORAGE_MOUNT},
            {"name": PODMAN_RUNROOT_VOLUME, "mountPath": PODMAN_RUNROOT_MOUNT},
        )

    def volumes(self) -> Sequence[JSONObject]:
        """Render bounded Podman storage and ephemeral run-root volumes.

        :returns: Native ``emptyDir`` volumes shared by probe and runner.
        """
        return (
            {
                "name": PODMAN_STORAGE_VOLUME,
                "emptyDir": {"sizeLimit": self.resources.storage_size},
            },
            {"name": PODMAN_RUNROOT_VOLUME, "emptyDir": {}},
        )


def _objects(value: JSONValue, field_name: str) -> Sequence[Mapping[str, JSONValue]]:
    """Require a sequence containing only JSON objects.

    :param value: Native field selected from one Pod read-back.
    :param field_name: Stable field named by malformed evidence.
    :returns: The same sequence narrowed to JSON object mappings.
    :raises KubernetesError: The provider field is absent or malformed.
    """
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise KubernetesError(f"podman_{field_name}_invalid")
    return value


def _named(
    items: Sequence[Mapping[str, JSONValue]], name: str, field_name: str
) -> Mapping[str, JSONValue]:
    """Select one unique named native object.

    :param items: Validated native objects.
    :param name: Exact requested object name.
    :param field_name: Stable field named by missing or duplicate evidence.
    :returns: One matching object.
    :raises KubernetesError: The provider read-back is absent or ambiguous.
    """
    selected = tuple(item for item in items if item.get("name") == name)
    if len(selected) != 1:
        raise KubernetesError(f"podman_{field_name}_not_unique")
    return selected[0]


def podman_runtime_readback(
    pod: Mapping[str, JSONValue],
    settings: PodmanRuntimeSettings,
    container_name: str,
    *,
    init_container_name: str | None = None,
) -> JSONObject:
    """Verify actual Pod placement, privilege, resources, and storage.

    :param pod: Native Pod read-back for the workload.
    :param settings: Exact runtime settings bound by the confirmed plan.
    :param container_name: Container expected to run Podman.
    :param init_container_name: Optional setup container sharing Podman storage.
    :returns: Safe observed runtime fields after exact comparison.
    :raises KubernetesError: Any placement or runtime field differs.
    """
    spec = pod.get("spec")
    if not isinstance(spec, Mapping):
        raise KubernetesError("podman_pod_spec_invalid")
    containers = _objects(spec.get("containers"), "containers")
    container = _named(containers, container_name, "container")
    init_container = None
    if init_container_name is not None:
        init_containers = _objects(spec.get("initContainers"), "init_containers")
        init_container = _named(
            init_containers,
            init_container_name,
            "init_container",
        )
    volumes = _objects(spec.get("volumes"), "volumes")
    storage = _named(volumes, PODMAN_STORAGE_VOLUME, "storage_volume")
    runroot = _named(volumes, PODMAN_RUNROOT_VOLUME, "runroot_volume")
    mounts = _objects(container.get("volumeMounts"), "volume_mounts")
    storage_mount = _named(mounts, PODMAN_STORAGE_VOLUME, "storage_mount")
    runroot_mount = _named(mounts, PODMAN_RUNROOT_VOLUME, "runroot_mount")
    security = container.get("securityContext")
    resources = container.get("resources")
    expected_container = settings.container_fields()
    init_matches = True
    if init_container is not None:
        init_mounts = _objects(
            init_container.get("volumeMounts"),
            "init_volume_mounts",
        )
        init_matches = (
            matches(
                expected_container["securityContext"],
                init_container.get("securityContext"),
            )
            and matches(
                expected_container["resources"],
                init_container.get("resources"),
            )
            and _named(
                init_mounts,
                PODMAN_STORAGE_VOLUME,
                "init_storage_mount",
            ).get("mountPath")
            == PODMAN_STORAGE_MOUNT
            and _named(
                init_mounts,
                PODMAN_RUNROOT_VOLUME,
                "init_runroot_mount",
            ).get("mountPath")
            == PODMAN_RUNROOT_MOUNT
        )
    if (
        spec.get("nodeName") != settings.node_name
        or spec.get("serviceAccountName") != settings.service_account
        or spec.get("automountServiceAccountToken") is not False
        or not matches(expected_container["securityContext"], security)
        or not matches(expected_container["resources"], resources)
        or not matches(
            {"sizeLimit": settings.resources.storage_size},
            storage.get("emptyDir"),
        )
        or not matches({}, runroot.get("emptyDir"))
        or storage_mount.get("mountPath") != PODMAN_STORAGE_MOUNT
        or runroot_mount.get("mountPath") != PODMAN_RUNROOT_MOUNT
        or not init_matches
    ):
        raise KubernetesError("podman_runtime_readback_mismatch")
    return settings.plan()
