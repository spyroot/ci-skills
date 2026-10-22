"""Model and serialize one verified OpenShift node-image artifact record.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json_object,
    json_integer,
    json_object,
    json_string,
)
from lib.python.core.kubernetes.artifact_storage_models import (
    StorageDestination,
    StorageHandle,
)
from lib.python.core.kubernetes.artifact_storage_runtime import normalized_digest
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.ocp_iso import OCPState
from lib.python.core.kubernetes.run_scope import (
    RunScope,
    validate_run_id,
)
from lib.python.core.kubernetes.tool_pod_models import (
    ArtifactHandle,
    PodHandle,
    PrepareToolPodAction,
    ToolPodMount,
    ToolPodSpec,
)

if TYPE_CHECKING:
    from lib.python.core.kubernetes.ocp_recovery import NodeRecoveryRecord

ARTIFACT_RECORD_VERSION: Final = "1.0"
_RECORD_FIELDS: Final = frozenset(
    {
        "schema_version",
        "run_id",
        "destination",
        "tool_spec",
        "tool_mounts",
        "artifact",
        "storage",
        "lifetime",
        "node_selector",
    }
)
_POD_FIELDS: Final = frozenset(
    {"namespace", "name", "uid", "container", "image", "restart_count"}
)
_ARTIFACT_FIELDS: Final = frozenset({"pod", "path", "sha256", "size"})
_MOUNT_FIELDS: Final = frozenset({"name", "volumeSource", "mountPath", "readOnly"})


class ArtifactLifetime(StrEnum):
    """Name the provider resource that bounds the final artifact lifetime.

    .. attribute :: POD
        :type: str

        Output exists only while its exact producer Pod exists.

    .. attribute :: CALLER_SELECTED_VOLUME
        :type: str

        Output persists on the caller-selected volume independently of the Pod.
    """

    POD = "pod"
    CALLER_SELECTED_VOLUME = "caller_selected_volume"


def _fields(
    document: Mapping[str, JSONValue], expected: frozenset[str], source: str
) -> None:
    """Require one exact internal record object shape.

    :param document: Decoded internal object.
    :param expected: Required field names for this object.
    :param source: Stable source name used in diagnostics.
    :raises KubernetesError: If fields are missing or unknown.
    """
    if set(document) != expected:
        raise KubernetesError(f"node_artifact_{source}_fields_invalid")


def _boolean(document: Mapping[str, JSONValue], key: str, source: str) -> bool:
    """Read one required boolean from an internal record.

    :param document: Decoded internal object.
    :param key: Required field name.
    :param source: Stable source name used in diagnostics.
    :returns: Exact boolean value.
    :raises KubernetesError: If the field is not a boolean.
    """
    value = document.get(key)
    if not isinstance(value, bool):
        raise KubernetesError(f"node_artifact_{source}_{key}_invalid")
    return value


def decode_pod_handle(document: JSONObject) -> PodHandle:
    """Decode the exact producer Pod identity from a run record.

    :param document: Persisted Pod identity.
    :returns: Typed immutable Pod handle.
    :raises KubernetesError: If any identity field is malformed.
    """  # noqa: DOC503 - caught validation is normalized to KubernetesError.
    expected = _POD_FIELDS | ({"node_name"} if "node_name" in document else set())
    _fields(document, frozenset(expected), "pod")
    try:
        restart_count = json_integer(document["restart_count"])
        if restart_count < 0:
            raise ValueError("negative restart count")
        return PodHandle(
            namespace=json_string(document["namespace"]),
            name=json_string(document["name"]),
            uid=json_string(document["uid"]),
            container=json_string(document["container"]),
            image=json_string(document["image"]),
            restart_count=restart_count,
            node_name=json_string(document.get("node_name", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise KubernetesError("node_artifact_pod_invalid") from exc


def _artifact(document: JSONObject) -> ArtifactHandle:
    """Decode one verified artifact identity from a run record.

    :param document: Persisted artifact object.
    :returns: Typed artifact handle bound to its producer Pod.
    :raises KubernetesError: If the identity, path, size, or digest is invalid.
    """
    _fields(document, _ARTIFACT_FIELDS, "artifact")
    try:
        size = json_integer(document["size"])
        if size < 0:
            raise ValueError("negative artifact size")
        path = json_string(document["path"])
        digest = normalized_digest(json_string(document["sha256"]))
        if not path.startswith("/"):
            raise ValueError("relative artifact path")
        return ArtifactHandle(
            pod=decode_pod_handle(json_object(document["pod"])),
            path=path,
            sha256=digest,
            size=size,
        )
    except (KeyError, TypeError, ValueError, KubernetesError) as exc:
        raise KubernetesError("node_artifact_identity_invalid") from exc


def _mount(document: JSONObject) -> ToolPodMount:
    """Decode one exact prepared-Pod mount from a run record.

    :param document: Persisted mount object.
    :returns: Typed native mount input.
    :raises KubernetesError: If the wrapper or native source is malformed.
    """
    _fields(document, _MOUNT_FIELDS, "mount")
    try:
        return ToolPodMount(
            name=json_string(document["name"]),
            volume_source=copy_json_object(json_object(document["volumeSource"])),
            mount_path=json_string(document["mountPath"]),
            read_only=_boolean(document, "readOnly", "mount"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise KubernetesError("node_artifact_mount_invalid") from exc


def _storage(document: JSONObject, destination: StorageDestination) -> StorageHandle:
    """Reconstruct and verify safe storage observations from a run record.

    :param document: Persisted safe storage observation.
    :param destination: Exact persisted destination wrapper.
    :returns: Typed storage handle whose rendered evidence matches the record.
    :raises KubernetesError: If an observation is malformed or internally differs.
    """
    capacity_value = document.get("capacity_bytes")
    try:
        capacity = None if capacity_value is None else json_integer(capacity_value)
        if capacity is not None and capacity < 0:
            raise ValueError("negative capacity")
        handle = StorageHandle(
            destination=destination,
            observed_mount_path=json_string(document["observed_mount_path"]),
            protocol=json_string(document["protocol"]),
            access_route=json_string(document["access_route"]),
            capacity_bytes=capacity,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise KubernetesError("node_artifact_storage_invalid") from exc
    if handle.as_dict() != document:
        raise KubernetesError("node_artifact_storage_readback_mismatch")
    return handle


def _node_selector(document: JSONObject) -> Mapping[str, str]:
    """Decode immutable native Pod placement labels from a run record.

    :param document: Persisted node-selector object.
    :returns: Immutable exact placement labels.
    :raises KubernetesError: If a selector key or value is not text.
    """
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in document.items()
    ):
        raise KubernetesError("node_artifact_node_selector_invalid")
    return MappingProxyType(dict(document))


@dataclass(frozen=True)
class NodeArtifactRecord:
    """Retain the exact verified ISO and inputs needed for later cluster reads.

    :param run_id: Build run identifier carried by live ownership labels.
    :param destination: Exact caller-selected storage wrapper.
    :param tool_spec: Exact CI12.0 tool specification used by the build Pod.
    :param tool_mounts: Exact ordered mounts used by the build Pod.
    :param artifact: Final verified artifact identity and producer Pod.
    :param storage: Safe observed destination identity.
    :param lifetime: Provider resource that bounds artifact availability.
    :param node_selector: Exact native Pod placement labels used by the build.

    .. attribute :: run_id
        :type: str

        Build run identifier carried by live ownership labels.

    .. attribute :: destination
        :type: StorageDestination

        Exact caller-selected storage wrapper.

    .. attribute :: tool_spec
        :type: ToolPodSpec

        Exact CI12.0 tool specification used by the build Pod.

    .. attribute :: tool_mounts
        :type: tuple[ToolPodMount, ...]

        Exact ordered mounts used by the build Pod.

    .. attribute :: artifact
        :type: ArtifactHandle

        Final verified artifact identity and producer Pod.

    .. attribute :: storage
        :type: StorageHandle

        Safe observed destination identity.

    .. attribute :: lifetime
        :type: ArtifactLifetime

        Provider resource that bounds artifact availability.

    .. attribute :: node_selector
        :type: Mapping[str, str]

        Immutable native Pod placement labels used by the build and later readers.
    """

    run_id: str
    destination: StorageDestination
    tool_spec: ToolPodSpec
    tool_mounts: tuple[ToolPodMount, ...]
    artifact: ArtifactHandle
    storage: StorageHandle
    lifetime: ArtifactLifetime
    node_selector: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )

    def __post_init__(self) -> None:
        """Reject internally inconsistent persisted evidence.

        :raises ValueError: If run, namespace, path, digest, size, or lifetime differs.
        :raises TypeError: If a native node selector key or value is not text.
        """
        validate_run_id(self.run_id)
        if self.artifact.pod.namespace != self.destination.namespace:
            raise ValueError("node_artifact_namespace_mismatch")
        if self.artifact.path != self.destination.artifact_path:
            raise ValueError("node_artifact_path_mismatch")
        normalized_digest(self.artifact.sha256)
        if self.artifact.size < 0:
            raise ValueError("node_artifact_size_invalid")
        if self.storage.destination != self.destination:
            raise ValueError("node_artifact_storage_destination_mismatch")
        expected = (
            ArtifactLifetime.POD
            if self.destination.is_temporary
            else ArtifactLifetime.CALLER_SELECTED_VOLUME
        )
        if self.lifetime is not expected:
            raise ValueError("node_artifact_lifetime_mismatch")
        if not isinstance(self.node_selector, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.node_selector.items()
        ):
            raise TypeError("node_artifact_node_selector_invalid")
        object.__setattr__(
            self,
            "node_selector",
            MappingProxyType(dict(self.node_selector)),
        )

    @classmethod
    def from_document(cls, document: JSONObject) -> NodeArtifactRecord:
        """Decode one immutable run record read from the Kubernetes API.

        :param document: Decoded ConfigMap payload.
        :returns: Typed record after cross-field validation.
        :raises KubernetesError: If the record version, fields, or evidence differ.
        """  # noqa: DOC503 - caught validation is normalized to KubernetesError.
        _fields(document, _RECORD_FIELDS, "record")
        try:
            if document["schema_version"] != ARTIFACT_RECORD_VERSION:
                raise ValueError("unsupported record version")
            destination = StorageDestination.from_document(
                json_object(document["destination"])
            )
            tool_spec = ToolPodSpec.from_document(
                copy_json_object(json_object(document["tool_spec"]))
            )
            mounts_value = document["tool_mounts"]
            if not isinstance(mounts_value, Sequence) or isinstance(
                mounts_value, (str, bytes, bytearray)
            ):
                raise TypeError("tool mounts must be an array")
            mounts = tuple(_mount(json_object(item)) for item in mounts_value)
            lifetime = ArtifactLifetime(json_string(document["lifetime"]))
            return cls(
                run_id=json_string(document["run_id"]),
                destination=destination,
                tool_spec=tool_spec,
                tool_mounts=mounts,
                artifact=_artifact(json_object(document["artifact"])),
                storage=_storage(json_object(document["storage"]), destination),
                lifetime=lifetime,
                node_selector=_node_selector(
                    copy_json_object(json_object(document["node_selector"]))
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise KubernetesError("node_artifact_record_invalid") from exc

    def as_dict(self) -> JSONObject:
        """Render the non-secret immutable run payload.

        :returns: Exact destination, tool, artifact, storage, and lifetime evidence.
        """
        return {
            "schema_version": ARTIFACT_RECORD_VERSION,
            "run_id": self.run_id,
            "destination": self.destination.as_dict(),
            "tool_spec": self.tool_spec.as_dict(),
            "tool_mounts": [mount.as_dict() for mount in self.tool_mounts],
            "artifact": self.artifact.as_dict(),
            "storage": self.storage.as_dict(),
            "lifetime": self.lifetime.value,
            "node_selector": dict(self.node_selector),
        }


@dataclass(frozen=True)
class RecordArtifactAction(SkillAction):
    """Select the exact prepared-Pod inputs to preserve with final evidence.

    :param preparation: Confirmed CI12.0 action used to create the build Pod.

    .. attribute :: preparation
        :type: PrepareToolPodAction

        Confirmed CI12.0 action used to create the build Pod.
    """

    preparation: PrepareToolPodAction


@dataclass
class OCPArtifactState(OCPState):
    """Extend native ISO state with one live run-artifact reference.

    .. attribute :: artifact_record
        :type: NodeArtifactRecord

        Parsed record after an exact ConfigMap read-back.

    .. attribute :: artifact_scope
        :type: RunScope

        Original build scope resolved from live labels.

    .. attribute :: artifact_record_uid
        :type: str

        Immutable ConfigMap UID observed at the provider boundary.

    .. attribute :: artifact_record_present
        :type: bool

        Whether this invocation resolved the live record.

    .. attribute :: artifact_record_deleted
        :type: bool

        Whether exact record deletion was independently verified.

    .. attribute :: artifact_before_removal_verified
        :type: bool

        Whether persistent bytes were read before build-resource removal.

    .. attribute :: artifact_after_removal_verified
        :type: bool

        Whether persistent bytes were read after build-resource removal.

    .. attribute :: temporary_backing_absent
        :type: bool

        Whether a Pod-lifetime artifact's producer Pod is absent.

    .. attribute :: artifact_readback_pod
        :type: PodHandle

        Temporary reader identity held constant across persistent read-backs.

    .. attribute :: recovery_record
        :type: NodeRecoveryRecord

        Minimal immutable identity persisted before native execution.

    .. attribute :: recovery_scope
        :type: RunScope

        Exact live scope containing the resolved recovery record.

    .. attribute :: recovery_record_uid
        :type: str

        Immutable ConfigMap UID read back from the provider.

    .. attribute :: recovery_record_present
        :type: bool

        Whether the invocation resolved a recovery record.

    .. attribute :: recovery_record_deleted
        :type: bool

        Whether exact recovery-record deletion was verified.

    .. attribute :: build_reused
        :type: bool

        Whether an identical completed run was selected for live verification.
    """

    artifact_record: NodeArtifactRecord = field(init=False)
    artifact_scope: RunScope = field(init=False)
    artifact_record_uid: str = field(init=False)
    artifact_record_present: bool = False
    artifact_record_deleted: bool = False
    artifact_before_removal_verified: bool = False
    artifact_after_removal_verified: bool = False
    temporary_backing_absent: bool = False
    artifact_readback_pod: PodHandle = field(init=False)
    recovery_record: NodeRecoveryRecord = field(init=False)
    recovery_scope: RunScope = field(init=False)
    recovery_record_uid: str = field(init=False)
    recovery_record_present: bool = False
    recovery_record_deleted: bool = False
    build_reused: bool = False


__all__ = [
    "ArtifactLifetime",
    "NodeArtifactRecord",
    "OCPArtifactState",
    "RecordArtifactAction",
    "decode_pod_handle",
]
