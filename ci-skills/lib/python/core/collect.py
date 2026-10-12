"""Concurrent read-only collectors with typed records and in-process filters.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import re
from argparse import Namespace
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from collections.abc import Set as AbstractSet
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Final, Optional, TypeAlias, Union
from urllib.parse import quote, urlsplit

from lib.python.access.access import gitlab_env, glab_argv, kubectl_argv, kubernetes_env
from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.kubernetes.cilium import (
    AGENT_DAEMONSET,
    HEALTH_COMMAND,
    agent_selector,
    health_detail,
    health_findings,
    is_ready,
    matches_selector,
    valid_health,
)
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.report import report
from lib.python.core.runtime import error_class, run_command, run_command_tail, sanitize
from lib.python.core.target import GitLabOperationTarget, Target, kubernetes_label
from lib.python.sys.status import PASS, UNKNOWN

# A cluster-wide list read is legitimately slow and several run concurrently.
# Measured on one target cluster: `kubectl get events -A -o json` was 7.7 MB and
# about 20 seconds on its own. The default per-command bound reported that
# healthy cluster as a timeout, and the access gate read the timeout as a
# denial, so list reads carry their own larger bound.
LIST_TIMEOUT_SECONDS = 120

# The window an event read covers when the caller names none.
DEFAULT_WINDOW = timedelta(hours=1)


class CommandReadError(ValueError):
    """A native read failed before a valid JSON response was available.

    :param reason: Stable classification of the native failure or invalid JSON.
    """


def _read_json(
    command: Sequence[str],
    env: Optional[Mapping[str, Optional[str]]] = None,
    timeout: int = LIST_TIMEOUT_SECONDS,
) -> JSONValue:
    """Read one native command response through the shared JSON decoder.

    :param command: Executable and arguments passed without a shell.
    :param env: Child environment overrides; absent overrides inherit the current
        environment, and a null mapping value removes that variable.
    :param timeout: Maximum command runtime in seconds.
    :returns: Validated JSON value, including an empty object or JSON null exactly
        when the command returned that value. Callers validate their resource shape.
    :raises CommandReadError: If the command fails or stdout is malformed,
        nonfinite, or too deeply nested to decode safely.
    :raises OSError: If process creation fails for a reason other than a missing
        executable, such as insufficient execute permission.
    """  # noqa: DOC503 - Native process creation can propagate OSError.
    result = run_command(command, env=env, timeout=timeout)
    if result.returncode:
        raise CommandReadError(error_class(result))
    try:
        return response_decoder().decode(result.stdout)
    except (ValueError, RecursionError) as exc:
        raise CommandReadError("invalid_json") from exc


def _items(value: JSONValue) -> Sequence[JSONObject]:
    """Validate a Kubernetes List envelope before consuming its items.

    :param value: Decoded API response expected to contain an ``items`` list.
    :returns: Item objects with named metadata and object-shaped spec/status.
    :raises TypeError: The envelope or any item's required shape is malformed.
    """
    items = value.get("items") if isinstance(value, Mapping) else None
    if not isinstance(items, list):
        raise TypeError("invalid_list_response")
    records: MutableSequence[JSONObject] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise TypeError("invalid_list_response")
        metadata = item.get("metadata")
        if (
            not isinstance(metadata, Mapping)
            or not isinstance(metadata.get("name"), str)
            or not metadata["name"]
        ):
            raise TypeError("invalid_list_response")
        if any(
            key in item and not isinstance(item[key], Mapping)
            for key in ("spec", "status")
        ):
            raise TypeError("invalid_item_response")
        records.append(item)
    return records


def _meta(item: JSONObject) -> JSONObject:
    """Read a Kubernetes item's metadata or an empty mapping.

    :param item: Decoded resource object.
    :returns: Item metadata when present, otherwise an empty mapping.
    """
    metadata = item.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _search(value: JSONObject, needle: Optional[str]) -> bool:
    """Apply an optional case-insensitive search over a report record.

    :param value: Record to serialize for text search.
    :param needle: Search text, or ``None``/empty text to accept every record.
    :returns: Whether the serialized record contains the supplied text.
    :raises TypeError: If supplied record values cannot be serialized as JSON.
    :raises ValueError: If a caller supplies a circular container.
    """  # noqa: DOC502 - json.dumps rejects cycles and unsupported values.
    return (
        not needle
        or needle.casefold() in json.dumps(value, ensure_ascii=False).casefold()
    )


def _batch(
    target: Target, resources: Mapping[str, tuple[str, bool]]
) -> tuple[Mapping[str, Sequence[JSONObject]], MutableSequence[Mapping[str, str]]]:
    """Read several Kubernetes resource lists concurrently on one target.

    :param target: Bound Kubernetes target used for every native command.
    :param resources: Report key mapped to resource name and all-namespaces flag.
    :returns: Validated item lists keyed by report name and per-resource error
        records; a failed list contributes an empty list.
    :raises TargetError: Bound kubeconfig changed while building queries.
    :raises ValueError: If the target has no Kubernetes authority.
    :raises OSError: If a native resource process cannot be started.
    """  # noqa: DOC502 - Target/query helpers and worker reads propagate failures.
    data: MutableMapping[str, Sequence[JSONObject]] = {}
    errors: MutableSequence[Mapping[str, str]] = []
    if not resources:
        return data, errors

    with ThreadPoolExecutor(max_workers=min(10, len(resources))) as pool:
        futures = {
            pool.submit(
                _read_json,
                kubectl_argv(
                    target,
                    "get",
                    resource,
                    *(["-A"] if namespaced else []),
                    "-o",
                    "json",
                ),
                env=kubernetes_env(target),
            ): key
            for key, (resource, namespaced) in resources.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                data[key] = _items(future.result())
            except (CommandReadError, TypeError) as exc:
                data[key] = []
                errors.append({"source": key, "reason": str(exc)})

    return data, errors


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


def collect_storage(target: Target, args: Namespace) -> MutableMapping[str, JSONValue]:
    """Read storage resources and assemble their filtered relationship report.

    :param target: Bound Kubernetes target with context and credential sources.
    :param args: Namespace, node, storage-class, phase, and text filters.
    :returns: Storage report joining claims, volumes, pods, controllers, and
        attachments; failed or malformed resource lists produce a PARTIAL report.
    :raises TargetError: If pinned kubeconfig sources changed or cannot be read.
    :raises ValueError: If the target has no Kubernetes authority.
    :raises OSError: If a native resource process cannot be started.
    """  # noqa: DOC502 - Target/query helpers and worker reads propagate failures.
    data, errors = _batch(target, STORAGE_RESOURCES)
    return StorageInventory(data, args, errors).report(target)


# A relative window is what an operator actually asks for -- "the last five
# minutes" -- and computing an RFC3339 pair by hand is where the mistake goes.
RELATIVE_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
RELATIVE_PATTERN = re.compile(r"\A(\d+)([smhd])\Z")


def parse_window(value: str) -> timedelta:
    """Parse a positive relative event duration.

    :param value: Count plus one lower-case unit ``s``, ``m``, ``h``, or ``d``.
    :returns: Corresponding positive ``timedelta``.
    :raises ValueError: The expression is empty, malformed, or nonpositive.
    :raises OverflowError: If the requested duration exceeds timedelta limits.
    """  # noqa: DOC503 - Duration and datetime arithmetic can overflow.
    # Not lowercased: `1M` most likely means one month, which this does not
    # support, so it must be refused rather than read as one minute.
    match = RELATIVE_PATTERN.match(value.strip())
    if not match:
        raise ValueError(
            "--last must be a count and unit, one of s, m, h, d (for example 5m)"
        )
    amount = int(match.group(1))
    if amount <= 0:
        raise ValueError("--last must be a positive duration")
    return timedelta(**{RELATIVE_UNITS[match.group(2)]: amount})


def _timestamp(value: str) -> datetime:
    """Normalize one timezone-aware ISO timestamp to UTC.

    :param value: ISO timestamp, optionally using ``Z`` for UTC.
    :returns: Timezone-aware UTC datetime.
    :raises ValueError: The timestamp is invalid or lacks a timezone.
    """
    candidate = value.replace("Z", "+00:00")
    stamp = datetime.fromisoformat(candidate)
    if stamp.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return stamp.astimezone(UTC)


def _event_data(
    target: Target,
) -> tuple[
    Mapping[str, Sequence[JSONObject]],
    MutableSequence[Mapping[str, str]],
    Optional[str],
]:
    """Read Events with a core/v1 fallback only when the preferred API is absent.

    :param target: Bound Kubernetes target for both event list queries.
    :returns: Event lists keyed by API source, classified read errors, and the
        fallback reason when the preferred resource was unavailable.
    :raises TargetError: If pinned kubeconfig sources changed or are unreadable.
    :raises OSError: If a native process cannot be started.
    """  # noqa: DOC502 - Target/query helpers and worker reads propagate failures.
    preferred_key = "events_v1"
    try:
        preferred = _read_json(
            kubectl_argv(target, "get", "events.events.k8s.io", "-A", "-o", "json"),
            env=kubernetes_env(target),
        )
        return {preferred_key: _items(preferred)}, [], None
    except (CommandReadError, TypeError) as exc:
        preferred_reason = str(exc)
    if preferred_reason != "resource_unavailable":
        return (
            {preferred_key: []},
            [{"source": preferred_key, "reason": preferred_reason}],
            None,
        )
    core_key = "core_events"
    try:
        core = _read_json(
            kubectl_argv(target, "get", "events", "-A", "-o", "json"),
            env=kubernetes_env(target),
        )
        return (
            {core_key: _items(core)},
            [{"source": preferred_key, "reason": "compatibility_fallback"}],
            preferred_reason,
        )
    except (CommandReadError, TypeError) as exc:
        return (
            {preferred_key: [], core_key: []},
            [
                {"source": preferred_key, "reason": preferred_reason},
                {"source": core_key, "reason": str(exc)},
            ],
            preferred_reason,
        )


def _event_parts(
    event: JSONObject,
) -> tuple[JSONObject, JSONObject, JSONObject]:
    """Validate the nested metadata, object reference, and series of an Event.

    :param event: Decoded Kubernetes Event object.
    :returns: Metadata, involved object, and series mappings.
    :raises TypeError: The object name, timestamps, message fields, or count
        fields have an invalid shape.
    """
    meta = _meta(event)
    obj = event["regarding"] if "regarding" in event else event.get("involvedObject")
    series = event.get("series", {})
    if series is None:
        series = {}
    if (
        not isinstance(obj, dict)
        or not isinstance(series, dict)
        or not isinstance(obj.get("name"), str)
        or not obj["name"]
    ):
        raise TypeError("invalid_event_record")
    # ObjectReference.kind is optional in the Kubernetes Event API. A name
    # still identifies the record for the filters and report below.
    for container, fields in (
        (meta, ("namespace", "creationTimestamp")),
        (obj, ("kind", "namespace", "name", "uid")),
        (series, ("lastObservedTime",)),
        (
            event,
            (
                "eventTime",
                "firstTimestamp",
                "lastTimestamp",
                "deprecatedLastTimestamp",
                "reason",
                "type",
                "note",
                "message",
            ),
        ),
    ):
        if any(
            container.get(field) is not None and not isinstance(container[field], str)
            for field in fields
        ):
            raise TypeError("invalid_event_record")
    for container, field in (
        (series, "count"),
        (event, "deprecatedCount"),
        (event, "count"),
    ):
        value = container.get(field)
        if value is not None and (type(value) is not int or value < 0):
            raise TypeError("invalid_event_record")
    return meta, obj, series


class EventInventory:
    """Filter one event inventory using a validated time window and object filters."""

    def __init__(self, args: Namespace):
        """Resolve the requested event window before any provider query.

        :param args: Relative or absolute time window and event selection filters.
        :raises ValueError: If time options conflict, timestamps are invalid, or
            the start occurs after the end.
        :raises OverflowError: If duration arithmetic exceeds datetime limits.
        """  # noqa: DOC503 - Duration and datetime arithmetic can overflow.
        self.args = args
        last = getattr(args, "last", None)
        if last is not None and (args.from_time or args.to_time):
            raise ValueError("--last cannot be combined with --from or --to")
        self.end = _timestamp(args.to_time) if args.to_time else datetime.now(UTC)
        if last is not None:
            self.start = self.end - parse_window(last)
        elif args.from_time:
            self.start = _timestamp(args.from_time)
        else:
            self.start = self.end - DEFAULT_WINDOW
        if self.start > self.end:
            raise ValueError("--from must not be after --to")

    def record(self, event: JSONObject, source: str) -> JSONObject:
        """Validate one Event and apply window, object, and reason filters.

        :param event: Native Event from the preferred or compatibility API.
        :param source: Resource-list key retained as report provenance.
        :returns: Matching record, or an empty object when the event is filtered.
        :raises TypeError: If event fields needed for identity or reporting are malformed.
        :raises ValueError: If observed timestamps are absent or invalid.
        """  # noqa: DOC503 - KubernetesObject field readers raise TypeError.
        meta, obj, series = _event_parts(event)
        event_view, meta_view = KubernetesObject(event), KubernetesObject(meta)
        object_view, series_view = KubernetesObject(obj), KubernetesObject(series)
        first_source = (
            event_view.text("eventTime")
            or event_view.text("firstTimestamp")
            or meta_view.text("creationTimestamp")
        )
        last_source = (
            series_view.text("lastObservedTime")
            or event_view.text("lastTimestamp")
            or event_view.text("deprecatedLastTimestamp")
            or first_source
        )
        if not last_source:
            raise ValueError("invalid_event_timestamp")
        try:
            event_time = _timestamp(last_source)
            first_time = _timestamp(first_source) if first_source else event_time
        except ValueError as exc:
            raise ValueError("invalid_event_timestamp") from exc
        if self.start <= event_time <= self.end:
            matching_time = event_time
        elif self.start <= first_time <= self.end:
            matching_time = first_time
        else:
            return {}
        namespace = meta_view.text("namespace") or object_view.text("namespace")
        kind = object_view.text("kind")
        name = object_view.text("name")
        reason = event_view.text("reason")
        if (
            self.args.namespace != "all"
            and namespace != self.args.namespace
            or self.args.kind
            and (kind or "").casefold() != self.args.kind.casefold()
            or self.args.object
            and name != self.args.object
            or self.args.reason
            and self.args.reason.casefold() not in (reason or "").casefold()
        ):
            return {}
        return {
            "timestamp": matching_time.isoformat(),
            "namespace": namespace,
            "first_timestamp": first_time.isoformat(),
            "last_timestamp": event_time.isoformat(),
            "kind": kind,
            "name": name,
            "object_uid": object_view.text("uid"),
            "reason": reason,
            "type": event_view.text("type"),
            "message": sanitize(
                event_view.text("note") or event_view.text("message") or "", 1000
            ),
            "count": series.get("count")
            or event.get("deprecatedCount")
            or event.get("count")
            or 1,
            "source": source,
        }

    def rows(
        self,
        data: Mapping[str, Sequence[JSONObject]],
        errors: MutableSequence[Mapping[str, str]],
    ) -> Sequence[JSONObject]:
        """Select and deduplicate records while retaining every malformed event error.

        :param data: Event lists keyed by their API source.
        :param errors: Read errors extended with invalid record or timestamp failures.
        :returns: Matching unique rows sorted by timestamp, namespace, and name.
        """
        rows: MutableSequence[JSONObject] = []
        seen: set[tuple[str, Optional[str], Optional[str], str]] = set()
        for source in ("core_events", "events_v1"):
            for event in data.get(source, []):
                try:
                    row = self.record(event, source)
                except (TypeError, ValueError) as exc:
                    errors.append({"source": source, "reason": str(exc)})
                    continue
                if not row:
                    continue
                view = KubernetesObject(row)
                key = (
                    view.required_text("timestamp"),
                    view.text("object_uid"),
                    view.text("reason"),
                    view.text("message") or "",
                )
                if key not in seen and _search(row, self.args.search):
                    rows.append(row)
                    seen.add(key)
        return sorted(
            rows,
            key=lambda item: (
                str(item["timestamp"]),
                str(item["namespace"] or ""),
                str(item["name"] or ""),
            ),
        )

    def report(
        self,
        target: Target,
        data: Mapping[str, Sequence[JSONObject]],
        errors: MutableSequence[Mapping[str, str]],
        fallback_reason: Optional[str] = None,
    ) -> MutableMapping[str, JSONValue]:
        """Build the event report and disclose any compatibility API fallback.

        :param target: Kubernetes authority named in the report.
        :param data: Event lists from the preferred or fallback API.
        :param errors: Read failures extended while validating event records.
        :param fallback_reason: Native failure that justified using core/v1, if any.
        :returns: JSON report with selected rows, window, filters, and source failures.
        :raises ValueError: If the target has no Kubernetes authority.
        """  # noqa: DOC502 - kubernetes_label requires a selected authority.
        rows = self.rows(data, errors)
        result = report(
            "event_trace",
            kubernetes_label(target),
            {
                "from": self.start.isoformat(),
                "to": self.end.isoformat(),
                "namespace": self.args.namespace,
                "kind": self.args.kind,
                "object": self.args.object,
                "reason": self.args.reason,
                "search": self.args.search,
            },
            rows,
            errors,
        )
        if fallback_reason:
            result["compatibility_fallback"] = {
                "preferred_api": "events.k8s.io/v1",
                "reason": fallback_reason,
            }
        return result


def collect_events(target: Target, args: Namespace) -> MutableMapping[str, JSONValue]:
    """Read cluster Events for a validated time window and object selection.

    :param target: Bound Kubernetes authority used for Event list queries.
    :param args: Relative/absolute time window and namespace, object, reason, text filters.
    :returns: Event report with unique sorted rows and independent source errors.
    :raises ValueError: If time options conflict, a timestamp is invalid, or the
        target has no Kubernetes authority.
    :raises OverflowError: If the requested duration exceeds datetime limits.
    :raises TargetError: If pinned Kubernetes credentials changed before a read.
    :raises OSError: If a native Event query cannot be started.
    """  # noqa: DOC502 - Target/query helpers and worker reads propagate failures.
    inventory = EventInventory(args)
    data, errors, fallback_reason = _event_data(target)
    return inventory.report(target, data, errors, fallback_reason)


class CiliumInventory:
    """Select Cilium resources and collect bounded health evidence per agent."""

    def __init__(
        self,
        target: Target,
        args: Namespace,
        data: Mapping[str, Sequence[JSONObject]],
        errors: MutableSequence[Mapping[str, str]],
    ):
        """Keep one resource snapshot and the caller's report filters.

        :param target: Bound Kubernetes authority used for health commands.
        :param args: Namespace selection, node restriction, and report text filter.
        :param data: Validated resource lists from the shared concurrent reader.
        :param errors: Collection failures extended with selection and health errors.
        """
        self.target = target
        self.args = args
        self.errors = errors
        self.data: MutableMapping[str, Sequence[JSONObject]] = {}
        for source, records in data.items():
            try:
                for record in records:
                    resource = KubernetesObject(record)
                    if source != "ciliumnodes":
                        resource.metadata.required_text("namespace")
                self.data[source] = records
            except TypeError:
                self.data[source] = []
                errors.append({"source": source, "reason": "invalid_item_response"})
        self.daemonsets = [
            item
            for item in self.data.get("daemonsets", [])
            if _meta(item).get("name") == AGENT_DAEMONSET
        ]
        self.namespace = self.select_namespace()

    def select_namespace(self) -> Optional[str]:
        """Resolve the caller's namespace or the unique observed Cilium namespace.

        :returns: Selected namespace, or None with a recorded selection error when
            automatic discovery cannot identify exactly one namespace.
        """
        if self.args.namespace != "auto":
            return self.args.namespace
        namespaces = sorted(
            {
                name
                for item in self.daemonsets
                if isinstance(name := _meta(item).get("namespace"), str) and name
            }
        )
        if len(namespaces) == 1:
            return namespaces[0]
        self.errors.append(
            {"source": "daemonsets", "reason": "cilium_namespace_not_unique"}
        )
        return None

    def agent_pods(self) -> Sequence[JSONObject]:
        """Select agents using the observed DaemonSet selector and node filter.

        :returns: Matching Pod objects in API order. Missing DaemonSet or selector
            evidence adds a classified error to this inventory.
        """
        selected = [
            item
            for item in self.daemonsets
            if _meta(item).get("namespace") == self.namespace
        ]
        if len(selected) != 1:
            self.errors.append(
                {"source": "daemonsets", "reason": "cilium_daemonset_missing"}
            )
        selector = (
            agent_selector(self.daemonsets, self.namespace)
            if self.namespace is not None
            else None
        ) or {}
        if selected and not selector:
            self.errors.append(
                {"source": "daemonsets", "reason": "cilium_selector_missing"}
            )
        pods: MutableSequence[JSONObject] = []
        for item in self.data.get("pods", []):
            pod = KubernetesObject(item)
            try:
                if pod.metadata.text("namespace") != self.namespace:
                    continue
                labels = pod.metadata.object("labels").document
                node = pod.object("spec").text("nodeName")
                pod.object("status").text("phase")
                pod.metadata.text("uid")
                if matches_selector(labels, selector) and (
                    not self.args.node or node == self.args.node
                ):
                    pods.append(item)
            except TypeError:
                self.errors.append(
                    {"source": "pods", "reason": "invalid_item_response"}
                )
        return pods

    def read_health(self, pod: JSONObject) -> JSONObject:
        """Read and classify one selected agent's health without changing the Pod.

        :param pod: Validated agent Pod from this inventory's namespace selection.
        :returns: Agent identity, readiness, native exit status, and validated health
            details; unready agents or failed reads report UNKNOWN with a reason.
        :raises TypeError: If called directly with malformed Pod fields.
        :raises TargetError: If bound kubeconfig sources changed or are unreadable.
        :raises OSError: If the native process cannot be started.
        """  # noqa: DOC502 - Field readers and native health queries can fail.
        resource = KubernetesObject(pod)
        ready = is_ready(pod)
        row: MutableMapping[str, JSONValue] = {
            "namespace": self.namespace,
            "name": resource.name,
            "pod_uid": resource.metadata.text("uid"),
            "node": resource.object("spec").text("nodeName"),
            "phase": resource.object("status").text("phase"),
            "ready": ready,
            "command": list(HEALTH_COMMAND),
            "status": UNKNOWN,
            "exit_status": None,
            "health": None,
        }
        if not ready:
            return row
        result = run_command(
            kubectl_argv(
                self.target,
                "-n",
                resource.namespace,
                "exec",
                resource.name,
                "--",
                *HEALTH_COMMAND,
            ),
            timeout=30,
            env=kubernetes_env(self.target),
        )
        row["exit_status"] = result.returncode
        if result.returncode:
            row["reason"] = error_class(result)
            return row
        try:
            decoded = response_decoder().decode(result.stdout)
        except (ValueError, RecursionError):
            row["reason"] = "invalid_health_json"
            return row
        if not valid_health(decoded):
            row["reason"] = "invalid_health_response"
            return row
        row.update(
            health=decoded,
            health_detail=health_detail(decoded),
            findings=health_findings(decoded),
            status=PASS,
        )
        return row

    def rows(self) -> Sequence[JSONObject]:
        """Read matching agents concurrently and retain health errors before filtering.

        :returns: Sorted agent rows, or one UNKNOWN row when no agent matches.
        :raises TargetError: If bound kubeconfig sources changed or are unreadable.
        :raises OSError: If a native health process cannot be started.
        """  # noqa: DOC502 - Field readers and native health queries can fail.
        pods = self.agent_pods()
        if not pods:
            self.errors.append(
                {"source": "pods", "reason": "cilium_agent_pods_missing"}
            )
            return [
                {
                    "namespace": self.namespace,
                    "name": "cilium-agent",
                    "node": self.args.node,
                    "pod_uid": None,
                    "status": UNKNOWN,
                    "reason": "no_matching_agent_pods",
                    "command": list(HEALTH_COMMAND),
                    "exit_status": None,
                }
            ]
        rows: MutableSequence[JSONObject] = []
        with ThreadPoolExecutor(max_workers=min(8, len(pods))) as pool:
            for row in pool.map(self.read_health, pods):
                if row["status"] == UNKNOWN:
                    self.errors.append(
                        {
                            "source": str(row["name"]),
                            "reason": str(row.get("reason") or "agent_not_ready"),
                        }
                    )
                elif row["findings"]:
                    self.errors.append(
                        {"source": str(row["name"]), "reason": "component_unhealthy"}
                    )
                if _search(row, self.args.search):
                    rows.append(row)
        return sorted(
            rows, key=lambda row: (str(row["node"] or ""), str(row["name"] or ""))
        )

    def report(self) -> MutableMapping[str, JSONValue]:
        """Add related Cilium resource observations to the shared report envelope.

        :returns: Cilium agent rows, component findings, related inventories, and
            all resource-selection or health-read errors collected for this target.
        :raises TargetError: If bound kubeconfig sources changed or are unreadable.
        :raises OSError: If a native health process cannot be started.
        """  # noqa: DOC502 - Field readers and native health queries can fail.
        result = report(
            "cilium_status",
            kubernetes_label(self.target),
            {
                "namespace": self.namespace,
                "node": self.args.node,
                "search": self.args.search,
            },
            self.rows(),
            self.errors,
        )
        result["daemonsets"] = [
            {
                "namespace": item.metadata.text("namespace"),
                "name": item.name,
                **item.replica_counts("DaemonSet"),
            }
            for document in self.daemonsets
            if (item := KubernetesObject(document)).metadata.text("namespace")
            == self.namespace
        ]
        result["operators"] = [
            {
                "namespace": item.metadata.text("namespace"),
                "name": item.name,
                "ready": item.object("status").document.get("readyReplicas", 0),
            }
            for document in self.data.get("operators", [])
            if (item := KubernetesObject(document)).metadata.text("namespace")
            == self.namespace
            and "cilium" in item.name
        ]
        result["ciliumnodes"] = [
            {"name": _meta(item).get("name"), "status": item.get("status", {})}
            for item in self.data.get("ciliumnodes", [])
        ]
        return result


def collect_cilium(target: Target, args: Namespace) -> JSONObject:
    """Read Cilium agent, operator, and CiliumNode status on one target.

    :param target: Bound Kubernetes target for resource lists and agent health.
    :param args: Namespace selector, optional node, and text-search filter.
    :returns: Cilium report with agent records, component findings, related
        inventories, and errors when selection or health reads fail.
    :raises TargetError: If bound kubeconfig sources changed or are unreadable.
    :raises ValueError: If the target has no Kubernetes authority.
    :raises OSError: If a native resource or health process cannot be started.
    """  # noqa: DOC502 - Target/query helpers and worker reads propagate failures.
    data, errors = _batch(
        target,
        {
            "daemonsets": ("daemonsets", True),
            "pods": ("pods", True),
            "operators": ("deployments", True),
            "ciliumnodes": ("ciliumnodes.cilium.io", False),
        },
    )
    return CiliumInventory(target, args, data, errors).report()


def gitlab_job_reference(job_url: str, host: str) -> tuple[str, str]:
    """Extract a project path and numeric job ID from one selected-host URL.

    :param job_url: HTTPS GitLab job URL ending in ``/-/jobs/<id>``.
    :param host: Exact selected GitLab network authority.
    :returns: Relative project path and decimal job ID as text.
    :raises ValueError: Scheme, host, path, query, fragment, or job ID is invalid.
    """
    parsed = urlsplit(job_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != host
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("job URL must use the exact selected GitLab FQDN")
    prefix, marker, job_id = parsed.path.rpartition("/-/jobs/")
    if not marker or not job_id.isdecimal() or not prefix.strip("/"):
        raise ValueError("job URL must contain a project path and numeric job ID")
    return prefix.strip("/"), job_id


def collect_gitlab_job(
    target: Union[Target, GitLabOperationTarget],
    args: Namespace,
    credential: Optional[Mapping[str, Optional[str]]] = None,
) -> JSONObject:
    """Read one GitLab job and its referenced pipeline and runner.

    :param target: GitLab target selected by the caller.
    :param args: Required job URL and optional text-search filter.
    :param credential: Bound child environment; when absent, a full target
        resolves its own GitLab credentials.
    :returns: Job report with selected fields from the exact job and any valid
        pipeline/runner reads, plus per-reference errors.
    :raises ValueError: The job URL is absent/invalid or a GitLab-only target
        lacks an injected credential environment, or the target has no GitLab authority.
    :raises OSError: If a native job, metadata, or trace process cannot be started.
    """  # noqa: DOC503 - Read failures become records; native I/O can escape.
    if not getattr(args, "job_url", None):
        raise ValueError("--job-url is required; see --describe for the contract")
    if target.gitlab is None:
        raise ValueError("gitlab_target_required")
    project, job_id = gitlab_job_reference(args.job_url, target.gitlab.host)
    if credential is None:
        if isinstance(target, GitLabOperationTarget):
            raise ValueError("bound_gitlab_credential_required")
        credential = gitlab_env(target)
    encoded = quote(project, safe="")
    base = f"projects/{encoded}"
    try:
        job = _read_json(glab_argv(target, f"{base}/jobs/{job_id}"), env=credential)
        if not isinstance(job, Mapping) or str(job.get("id")) != job_id:
            raise CommandReadError("invalid_response")
    except CommandReadError as exc:
        return report(
            "gitlab_job",
            target.gitlab.url,
            {"job_url": args.job_url, "search": args.search},
            [],
            [{"source": "job", "reason": str(exc)}],
        )
    pipeline_ref, runner_ref = job.get("pipeline"), job.get("runner")
    pipeline_id = pipeline_ref.get("id") if isinstance(pipeline_ref, dict) else None
    runner_id = runner_ref.get("id") if isinstance(runner_ref, dict) else None
    endpoints = {
        "pipeline": f"{base}/pipelines/{pipeline_id}" if pipeline_id else None,
        "runner": f"runners/{runner_id}" if runner_id else None,
    }
    metadata: MutableMapping[str, JSONValue] = {}
    errors: MutableSequence[Mapping[str, str]] = []
    if not pipeline_id:
        errors.append({"source": "pipeline", "reason": "pipeline_reference_missing"})
    if not runner_id:
        errors.append({"source": "runner", "reason": "runner_reference_missing"})
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_read_json, glab_argv(target, endpoint), env=credential): name
            for name, endpoint in endpoints.items()
            if endpoint
        }
        for future in as_completed(futures):
            try:
                value = future.result()
            except CommandReadError as exc:
                errors.append({"source": futures[future], "reason": str(exc)})
                continue
            if not isinstance(value, Mapping) or str(value.get("id")) != str(
                pipeline_id if futures[future] == "pipeline" else runner_id
            ):
                errors.append({"source": futures[future], "reason": "invalid_response"})
            else:
                fields = (
                    ("id", "status", "sha", "ref", "created_at", "updated_at")
                    if futures[future] == "pipeline"
                    else (
                        "id",
                        "description",
                        "runner_type",
                        "status",
                        "tag_list",
                        "active",
                        "paused",
                        "online",
                        "contacted_at",
                    )
                )
                metadata[futures[future]] = {
                    field: value[field] for field in fields if field in value
                }
    trace_result = run_command_tail(
        glab_argv(target, f"{base}/jobs/{job_id}/trace"), timeout=30, env=credential
    )
    if trace_result.returncode:
        errors.append({"source": "trace", "reason": error_class(trace_result)})
        trace = ""
    else:
        trace = sanitize("\n".join(trace_result.stdout.splitlines()[-200:]), 64000)
    row = {
        "job_id": job.get("id"),
        "name": job.get("name"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "failure_reason": job.get("failure_reason"),
        "pipeline": metadata.get("pipeline"),
        "runner": metadata.get("runner"),
        "trace_tail": trace,
    }
    rows = [row] if _search(row, args.search) else []
    return report(
        "gitlab_job",
        target.gitlab.url,
        {"job_url": args.job_url, "search": args.search},
        rows,
        errors,
    )
