"""Join Kubernetes storage resources into a filtered report.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, TypeAlias

from lib.python.core.collection import _search
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.kubernetes.collect import _batch
from lib.python.core.kubernetes.kubernetes import KubernetesService
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.report import report
from lib.python.core.target import Target, kubernetes_label

STORAGE_RESOURCES: Final[Mapping[str, tuple[str, bool]]] = MappingProxyType(
    {
        "nodes": ("nodes", False),
        "pods": ("pods", True),
        "pvcs": ("persistentvolumeclaims", True),
        "pvs": ("persistentvolumes", False),
        "storageclasses": ("storageclasses", False),
        "csidrivers": ("csidrivers", False),
        "csinodes": ("csinodes", False),
        "attachments": ("volumeattachments", False),
        "deployments": ("deployments", True),
        "statefulsets": ("statefulsets", True),
        "daemonsets": ("daemonsets", True),
        "replicasets": ("replicasets", True),
    }
)


STORAGE_CONTROLLER_KINDS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "deployments": "Deployment",
        "statefulsets": "StatefulSet",
        "daemonsets": "DaemonSet",
    }
)


StorageRecords: TypeAlias = Mapping[str, Sequence[JSONObject]]


ClaimKey: TypeAlias = tuple[str, str]


ControllerKey: TypeAlias = tuple[str, str, str]


@dataclass(frozen=True)
class StorageIndexes:
    """Joined storage evidence collected from one resource inventory.

    :param volumes: PersistentVolumes indexed by resource name.
    :param attachments: Attachment observations indexed by volume name.
    :param controllers: Workload objects indexed by kind, namespace, and name.
    :param pods: Pod observations indexed by namespace and claim name.
    :param claim_controllers: Controller identities associated with each claim.

    .. attribute :: volumes
        :type: Mapping[str, KubernetesObject]

        PersistentVolumes indexed by resource name.

    .. attribute :: attachments
        :type: Mapping[str, Sequence[JSONObject]]

        Attachment observations indexed by volume name.

    .. attribute :: controllers
        :type: Mapping[ControllerKey, KubernetesObject]

        Workload objects indexed by kind, namespace, and name.

    .. attribute :: pods
        :type: Mapping[ClaimKey, Sequence[JSONObject]]

        Pod observations indexed by namespace and claim name.

    .. attribute :: claim_controllers
        :type: Mapping[ClaimKey, AbstractSet[ControllerKey]]

        Controller identities associated with each claim.
    """

    volumes: Mapping[str, KubernetesObject]
    attachments: Mapping[str, Sequence[JSONObject]]
    controllers: Mapping[ControllerKey, KubernetesObject]
    pods: Mapping[ClaimKey, Sequence[JSONObject]]
    claim_controllers: Mapping[ClaimKey, AbstractSet[ControllerKey]]


class StorageInventory:
    """Validate and join Kubernetes storage observations into a filtered report."""

    def __init__(
        self,
        data: StorageRecords,
        args: Namespace,
        errors: MutableSequence[Mapping[str, str]],
    ):
        """Validate resource fields before constructing relationship indexes.

        :param data: Resource lists already checked for valid metadata envelopes.
        :param args: Filters used when selecting claim and unclaimed-volume rows.
        :param errors: Existing collection errors, extended for malformed resources.
        """
        self.args = args
        self.errors = errors
        self.data: MutableMapping[str, Sequence[KubernetesObject]] = {}
        for source, records in data.items():
            try:
                resources = [KubernetesObject(record) for record in records]
                for resource in resources:
                    self.validate(source, resource)
                self.data[source] = resources
            except TypeError:
                self.data[source] = []
                errors.append({"source": source, "reason": "invalid_item_response"})

    @staticmethod
    def validate(source: str, item: KubernetesObject) -> None:
        """Check fields that participate in storage relationships and filters.

        :param source: Batch resource key identifying the native resource kind.
        :param item: Resource to validate before any joins or filtering.
        :raises TypeError: If a referenced field has an invalid type or identity.
        """  # noqa: DOC502 - KubernetesObject field readers raise TypeError.
        _ = item.name
        if source in {"pods", "pvcs", "replicasets", *STORAGE_CONTROLLER_KINDS}:
            _ = item.namespace
        spec, status = item.object("spec"), item.object("status")
        for key in ("nodeName", "volumeName", "storageClassName"):
            spec.text(key)
        status.text("phase")
        spec.object("source").text("persistentVolumeName")
        spec.object("csi").text("driver")
        for owner in item.metadata.objects("ownerReferences"):
            owner.required_text("kind")
            owner.required_text("name")
        for volume in spec.objects("volumes"):
            volume.object("persistentVolumeClaim").text("claimName")
        for condition in status.objects("conditions"):
            condition.required_text("type")
            condition.text("status")

    def attachments(self) -> Mapping[str, Sequence[JSONObject]]:
        """Index native attachment evidence by referenced volume.

        :returns: Volume names mapped to attachment name, node, and attached state.
        """
        result: MutableMapping[str, MutableSequence[JSONObject]] = {}
        for item in self.data.get("attachments", []):
            spec = item.object("spec")
            volume = spec.object("source").text("persistentVolumeName")
            if volume:
                result.setdefault(volume, []).append(
                    {
                        "name": item.name,
                        "node": spec.text("nodeName"),
                        "attached": item.object("status").document.get("attached"),
                    }
                )
        return result

    def replica_owners(self) -> Mapping[ClaimKey, Sequence[str]]:
        """Resolve ReplicaSet ownership to Deployment names.

        :returns: Namespace and ReplicaSet name mapped to owning Deployments.
        """
        return {
            (item.namespace, item.name): [
                ref.required_text("name")
                for ref in item.metadata.objects("ownerReferences")
                if ref.text("kind") == "Deployment"
            ]
            for item in self.data.get("replicasets", [])
        }

    def pod_links(
        self,
    ) -> tuple[
        Mapping[ClaimKey, Sequence[JSONObject]],
        Mapping[ClaimKey, AbstractSet[ControllerKey]],
    ]:
        """Join pod volume references to workload ownership.

        :returns: Claim-to-pod observations and claim-to-controller identities.
        """
        replica_owners = self.replica_owners()
        pods: MutableMapping[ClaimKey, MutableSequence[JSONObject]] = {}
        controllers: MutableMapping[ClaimKey, set[ControllerKey]] = {}
        for pod in self.data.get("pods", []):
            namespace = pod.namespace
            owners = pod.metadata.objects("ownerReferences")
            names = [ref.required_text("name") for ref in owners]
            references = {
                (ref.required_text("kind"), namespace, ref.required_text("name"))
                for ref in owners
                if ref.text("kind") in STORAGE_CONTROLLER_KINDS.values()
            }
            for ref in owners:
                if ref.text("kind") == "ReplicaSet":
                    deployments = replica_owners.get(
                        (namespace, ref.required_text("name")), []
                    )
                    names.extend(deployments)
                    references.update(
                        ("Deployment", namespace, name) for name in deployments
                    )
            spec = pod.object("spec")
            for volume in spec.objects("volumes"):
                claim = volume.object("persistentVolumeClaim").text("claimName")
                if claim:
                    key = (namespace, claim)
                    pods.setdefault(key, []).append(
                        {
                            "pod": pod.name,
                            "pod_uid": pod.metadata.document.get("uid"),
                            "node": spec.text("nodeName"),
                            "owners": names,
                            "phase": pod.object("status").text("phase"),
                        }
                    )
                    controllers.setdefault(key, set()).update(references)
        return pods, controllers

    def indexes(self) -> StorageIndexes:
        """Build the relationship indexes shared by claim and volume rows.

        :returns: Resource and observation indexes from this validated inventory.
        """
        pods, claim_controllers = self.pod_links()
        return StorageIndexes(
            volumes={item.name: item for item in self.data.get("pvs", [])},
            attachments=self.attachments(),
            controllers={
                (kind, item.namespace, item.name): item
                for source, kind in STORAGE_CONTROLLER_KINDS.items()
                for item in self.data.get(source, [])
            },
            pods=pods,
            claim_controllers=claim_controllers,
        )

    def selected(self, row: JSONObject) -> bool:
        """Apply the declared filters to one fully assembled storage row.

        :param row: Claim or volume row with its correlated node observations.
        :returns: Whether namespace, node, class, phase, and text filters match.
        """
        nodes = row["nodes"]
        return (
            (self.args.namespace == "all" or row["namespace"] == self.args.namespace)
            and (
                not self.args.node
                or isinstance(nodes, Sequence)
                and self.args.node in nodes
            )
            and (
                not self.args.storage_class
                or row["storage_class"] == self.args.storage_class
            )
            and (
                self.args.phase == "all"
                or self.args.phase in (row["phase"], row["pv_phase"])
            )
            and _search(row, self.args.search)
        )

    @staticmethod
    def nodes(observations: Sequence[JSONObject]) -> Sequence[str]:
        """Collect distinct assigned node names from correlated observations.

        :param observations: Validated pod or attachment summaries.
        :returns: Sorted nonempty node names; unassigned nodes are omitted.
        """
        return sorted(
            {
                name
                for item in observations
                if isinstance(name := item.get("node"), str) and name
            }
        )

    @staticmethod
    def controller(key: ControllerKey, item: KubernetesObject) -> JSONObject:
        """Summarize a workload using the counters for its native kind.

        :param key: Controller kind, namespace, and name relationship key.
        :param item: Validated controller resource.
        :returns: Kind, name, desired count, and ready count from the API.
        :raises TypeError: If the workload's spec or status is not an object.
        """  # noqa: DOC502 - The shared workload reader validates field shapes.
        kind, _namespace, name = key
        return {"kind": kind, "name": name, **item.replica_counts(kind)}

    def claim_row(self, item: KubernetesObject, indexes: StorageIndexes) -> JSONObject:
        """Build one claim row with every observed storage relationship.

        :param item: Validated PersistentVolumeClaim.
        :param indexes: Resource and relationship indexes for this inventory.
        :returns: Claim identity, volume state, and pod/controller/attachment evidence.
        """
        spec, status = item.object("spec"), item.object("status")
        volume = spec.text("volumeName")
        pv = indexes.volumes.get(volume) if volume is not None else None
        pv_spec = pv.object("spec") if pv is not None else KubernetesObject({})
        key = (item.namespace, item.name)
        pods = indexes.pods.get(key, [])
        return {
            "resource_kind": "PersistentVolumeClaim",
            "namespace": item.namespace,
            "name": item.name,
            "phase": status.text("phase") or "Unknown",
            "pv_phase": pv.object("status").text("phase") if pv is not None else None,
            "storage_class": spec.text("storageClassName")
            or pv_spec.text("storageClassName"),
            "volume": volume,
            "capacity": status.document.get("capacity"),
            "access_modes": spec.document.get("accessModes", []),
            "pods": pods,
            "nodes": self.nodes(pods),
            "attachments": indexes.attachments.get(volume, [])
            if volume is not None
            else [],
            "csi_driver": pv_spec.object("csi").text("driver"),
            "controllers": [
                self.controller(controller, indexes.controllers[controller])
                for controller in sorted(indexes.claim_controllers.get(key, set()))
                if controller in indexes.controllers
            ],
        }

    def volume_row(self, item: KubernetesObject, indexes: StorageIndexes) -> JSONObject:
        """Build one unclaimed-volume row from native state and attachments.

        :param item: Validated PersistentVolume without an observed claim.
        :param indexes: Relationship indexes containing attachment observations.
        :returns: Cluster-scoped volume row with capacity, phase, class, and nodes.
        """
        spec = item.object("spec")
        phase = item.object("status").text("phase") or "Unknown"
        attachments = indexes.attachments.get(item.name, [])
        return {
            "resource_kind": "PersistentVolume",
            "namespace": None,
            "name": item.name,
            "phase": phase,
            "pv_phase": phase,
            "storage_class": spec.text("storageClassName"),
            "volume": item.name,
            "capacity": spec.document.get("capacity"),
            "access_modes": spec.document.get("accessModes", []),
            "pods": [],
            "nodes": self.nodes(attachments),
            "attachments": attachments,
            "csi_driver": spec.object("csi").text("driver"),
            "controllers": [],
        }

    def rows(self) -> Sequence[JSONObject]:
        """Assemble filtered claim rows and genuinely unclaimed volume rows.

        :returns: Matching rows sorted by namespace and resource name. A claim
            excluded by filters still reserves its observed PersistentVolume.
        """
        indexes = self.indexes()
        claimed: set[str] = set()
        rows: MutableSequence[JSONObject] = []
        for item in self.data.get("pvcs", []):
            volume = item.object("spec").text("volumeName")
            if volume:
                claimed.add(volume)
            row = self.claim_row(item, indexes)
            if self.selected(row):
                rows.append(row)
        if self.args.namespace == "all":
            for item in self.data.get("pvs", []):
                if item.name not in claimed:
                    row = self.volume_row(item, indexes)
                    if self.selected(row):
                        rows.append(row)
        return sorted(
            rows, key=lambda row: (str(row["namespace"] or ""), str(row["name"]))
        )

    def report(self, target: Target) -> MutableMapping[str, JSONValue]:
        """Attach storage inventory observations to the shared diagnostic envelope.

        :param target: Bound Kubernetes authority named in the report.
        :returns: Storage report with relationship rows, source errors, node
            conditions, storage classes, CSI drivers, and resource counts.
        """
        result = report(
            "storage_report",
            kubernetes_label(target),
            {
                key: getattr(self.args, key)
                for key in ("namespace", "node", "storage_class", "phase", "search")
            },
            self.rows(),
            self.errors,
        )
        result["inventory"] = {
            key: len(self.data.get(key, [])) for key in STORAGE_RESOURCES
        }
        result["nodes"] = [
            {
                "name": node.name,
                "conditions": {
                    condition.required_text("type"): condition.text("status")
                    for condition in node.object("status").objects("conditions")
                },
            }
            for node in self.data.get("nodes", [])
        ]
        result["storage_classes"] = [
            {"name": item.name, "provisioner": item.document.get("provisioner")}
            for item in self.data.get("storageclasses", [])
        ]
        result["csi_drivers"] = [item.name for item in self.data.get("csidrivers", [])]
        result["controller_inventory"] = {
            kind: len(self.data.get(kind, [])) for kind in STORAGE_CONTROLLER_KINDS
        }
        return result


def collect_storage(
    target: Target, args: Namespace, *, state: DeliveryState | None = None
) -> MutableMapping[str, JSONValue]:
    """Read storage resources and assemble their filtered relationship report.

    :param target: Bound Kubernetes target with context and credential sources.
    :param args: Namespace, node, storage-class, phase, and text filters.
    :param state: Shared declaration and previously observed Kubernetes access.
    :returns: Storage report joining claims, volumes, pods, controllers, and
        attachments; failed or malformed resource lists produce a PARTIAL report.
    :raises TargetError: If pinned kubeconfig sources changed or cannot be read.
    :raises ValueError: If the target has no Kubernetes authority.
    :raises OSError: If a native resource process cannot be started.
    """  # noqa: DOC502 - Target/query helpers and worker reads propagate failures.
    KubernetesService(target, state=state).require_access()
    data, errors = _batch(target, STORAGE_RESOURCES)
    return StorageInventory(data, args, errors).report(target)
