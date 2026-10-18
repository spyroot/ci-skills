"""Typed models for reusable Kubernetes tool Pod workflows.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, MutableMapping, MutableSequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Final, Optional

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json_object,
    json_objects,
    json_string,
)
from lib.python.core.kubernetes.kubernetes import DEFAULT_COMMAND_TIMEOUT
from lib.python.core.kubernetes.run_scope import (
    RunScopeState,
    validate_namespace,
    validate_run_id,
)
from lib.python.core.schema import SpecificationError, validate_specification
from lib.python.core.tooling import Dependency

TOOL_POD_SCHEMA: Final = "tool-pod.schema.json"


def dependency_key(index: int) -> str:
    """Return the stable internal identity of one declared dependency.

    :param index: Zero-based position in the validated input sequence.
    :returns: Decimal index safe as an internal mapping key.
    :raises ValueError: If the index is negative or is a boolean.
    """
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("tool_dependency_index_invalid")
    return str(index)


def _fingerprint(document: JSONObject) -> tuple[str, str]:
    """Return full and label-safe SHA-256 identities for one canonical object.

    :param document: JSON object whose complete values bind the resource identity.
    :returns: Full prefixed hexadecimal digest and lowercase base32 label value.
    """
    encoded = json.dumps(
        document,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    label = base64.b32encode(bytes.fromhex(digest)).decode("ascii").rstrip("=").lower()
    return f"sha256:{digest}", label


@dataclass(frozen=True)
class ToolPodSpec:
    """Validated image and reusable dependency declarations.

    :param image: Caller-selected execution image.
    :param dependencies: Dependencies in declaration order.
    :param document: Immutable operation-owned copy of the validated input.
    :param fingerprint: Full SHA-256 of the canonical input.
    :param fingerprint_label: Base32 fingerprint suitable for a label value.

    .. attribute :: image
        :type: str

        Caller-selected execution image.

    .. attribute :: dependencies
        :type: tuple[Dependency, ...]

        Dependencies in declaration order.

    .. attribute :: document
        :type: JSONObject

        Immutable operation-owned copy of the validated input.

    .. attribute :: fingerprint
        :type: str

        Full SHA-256 of the canonical input.

    .. attribute :: fingerprint_label
        :type: str

        Base32 fingerprint suitable for a Kubernetes label value.
    """

    image: str
    dependencies: tuple[Dependency, ...]
    document: JSONObject = field(repr=False)
    fingerprint: str
    fingerprint_label: str

    @classmethod
    def load(cls, source: Path) -> ToolPodSpec:
        """Read one finite JSON input and validate it once.

        :param source: Caller-selected wrapper path.
        :returns: Typed specification built from the exact parsed bytes.
        :raises OSError: If the selected input or installed schema cannot be read.
        :raises SpecificationError: If JSON or schema validation fails.
        """  # noqa: DOC503 - file and schema readers propagate OSError.
        try:
            value = response_decoder().decode(source.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SpecificationError("invalid_specification:/:json") from exc
        if not isinstance(value, Mapping):
            raise SpecificationError("invalid_specification:/:type")
        return cls.from_document(value)

    @classmethod
    def from_document(cls, document: JSONObject) -> ToolPodSpec:
        """Create typed inputs from the exact plan-owned document.

        :param document: Parsed wrapper retained by the planning operation.
        :returns: Validated immutable specification.
        :raises SpecificationError: If the document violates the installed schema.
        """
        validate_specification(document, TOOL_POD_SCHEMA)
        try:
            image = json_string(document["image"])
            dependencies = tuple(
                Dependency.from_document(item)
                for item in json_objects(document["dependencies"])
            )
        except (KeyError, TypeError) as exc:
            raise SpecificationError("invalid_specification:/:type") from exc
        owned = copy_json_object(document)
        fingerprint, label = _fingerprint(owned)
        return cls(
            image=image,
            dependencies=dependencies,
            document=MappingProxyType(owned),
            fingerprint=fingerprint,
            fingerprint_label=label,
        )

    def as_dict(self) -> JSONObject:
        """Return an operation-owned JSON copy for planning and reports.

        :returns: Exact validated input without derived fields.
        """
        return copy_json_object(self.document)


@dataclass(frozen=True)
class ToolPodMount:
    """Pass one caller-selected native Kubernetes volume source to the Pod.

    :param name: Native volume and mount name.
    :param volume_source: Native fields placed beneath the volume ``name``.
    :param mount_path: Absolute container path selected by the consuming workflow.
    :param read_only: Native container mount access flag.

    .. attribute :: name
        :type: str

        Native volume and mount name.

    .. attribute :: volume_source
        :type: JSONObject

        Operation-owned native fields placed beneath the volume name.

    .. attribute :: mount_path
        :type: str

        Absolute container path selected by the consuming workflow.

    .. attribute :: read_only
        :type: bool

        Native container mount access flag.
    """

    name: str
    volume_source: JSONObject = field(repr=False)
    mount_path: str
    read_only: bool = False

    def __post_init__(self) -> None:
        """Take an operation-owned copy without interpreting native fields.

        :raises TypeError: If the native source contains non-JSON values.
        """  # noqa: DOC502 - JSON copying validates nested values.
        object.__setattr__(
            self,
            "volume_source",
            MappingProxyType(copy_json_object(self.volume_source)),
        )

    def as_dict(self) -> JSONObject:
        """Render exact caller inputs for plan and fingerprint binding.

        :returns: Name, unchanged native source, path, and access flag.
        """
        return {
            "name": self.name,
            "volumeSource": copy_json_object(self.volume_source),
            "mountPath": self.mount_path,
            "readOnly": self.read_only,
        }


@dataclass(frozen=True)
class PrepareToolPodAction(SkillAction):
    """Inputs for one confirmed tool Pod preparation.

    :param spec: Exact specification bound by the confirmed plan.
    :param run_id: Optional caller correlation identifier or prior live run.
    :param namespace: Optional existing namespace selected by the caller.
    :param timeout: Positive bound for each Kubernetes operation.
    :param retain: Keep a successfully verified Pod until explicit removal.
    :param mounts: Native storage mounts selected by an internal consumer.

    .. attribute :: spec
        :type: ToolPodSpec

        Exact specification bound by the confirmed plan.

    .. attribute :: run_id
        :type: Optional[str]

        Optional caller correlation identifier or prior live run.

    .. attribute :: namespace
        :type: Optional[str]

        Optional existing namespace selected by the caller.

    .. attribute :: timeout
        :type: int

        Positive bound for each Kubernetes operation.

    .. attribute :: retain
        :type: bool

        Whether to preserve a successfully verified Pod.

    .. attribute :: mounts
        :type: tuple[ToolPodMount, ...]

        Native storage mounts selected by an internal consumer.
    """

    spec: ToolPodSpec
    run_id: Optional[str] = None
    namespace: Optional[str] = None
    timeout: int = DEFAULT_COMMAND_TIMEOUT
    retain: bool = True
    mounts: tuple[ToolPodMount, ...] = ()

    def __post_init__(self) -> None:
        """Reject invalid bounds and correlation identities before provider access.

        :raises ValueError: If timeout, run ID, or namespace is invalid.
        """
        if self.timeout <= 0:
            raise ValueError("tool_pod_timeout_invalid")
        if self.run_id:
            validate_run_id(self.run_id)
        if self.namespace:
            validate_namespace(self.namespace)

    @property
    def image(self) -> str:
        """Expose the selected image to the resource renderer.

        :returns: Caller-selected execution image.
        """
        return self.spec.image

    @property
    def fingerprint(self) -> str:
        """Bind the tool specification and native mounts to this Pod shape.

        :returns: Full canonical action fingerprint.
        """
        return _fingerprint(self._fingerprint_document())[0]

    @property
    def fingerprint_label(self) -> str:
        """Expose a Kubernetes-label-safe form of the action fingerprint.

        :returns: Lowercase base32 SHA-256 without padding.
        """
        return _fingerprint(self._fingerprint_document())[1]

    def _fingerprint_document(self) -> JSONObject:
        """Build canonical resource-shape inputs without provider defaults.

        :returns: Exact tool specification and ordered native mounts.
        """
        return {
            "spec": self.spec.as_dict(),
            "mounts": [mount.as_dict() for mount in self.mounts],
        }


@dataclass(frozen=True)
class RemoveToolPodAction(SkillAction):
    """Select a retained tool Pod scope for verified removal.

    :param run_id: Exact operation identifier returned by preparation.

    .. attribute :: run_id
        :type: str

        Exact operation identifier returned by preparation.
    """

    run_id: str

    def __post_init__(self) -> None:
        """Validate the lookup identifier without inferring a namespace.

        :raises ValueError: If the identifier is not DNS-label-safe.
        """  # noqa: DOC502 - validate_run_id raises the declared ValueError.
        validate_run_id(self.run_id)


@dataclass(frozen=True)
class ToolDependencyAction(SkillAction):
    """Bind one dependency index to the confirmed preparation action.

    :param prepare: Confirmed preparation action shared by dependency callbacks.
    :param index: Zero-based dependency position in the validated input.

    .. attribute :: prepare
        :type: PrepareToolPodAction

        Confirmed preparation action shared by dependency callbacks.

    .. attribute :: index
        :type: int

        Zero-based dependency position in the validated input.
    """

    prepare: PrepareToolPodAction
    index: int

    def __post_init__(self) -> None:
        """Require an existing dependency position in the confirmed input.

        :raises ValueError: If the index does not select exactly one dependency.
        """
        dependency_key(self.index)
        if self.index >= len(self.prepare.spec.dependencies):
            raise ValueError("tool_dependency_index_invalid")

    @property
    def key(self) -> str:
        """Return the stable state key for this declared position.

        :returns: Decimal dependency index.
        """
        return dependency_key(self.index)

    @property
    def dependency(self) -> Dependency:
        """Return the exact declared dependency at this callback position.

        :returns: Dependency selected by its stable input index.
        """
        return self.prepare.spec.dependencies[self.index]


@dataclass(frozen=True)
class PodHandle:
    """Exact live Pod/container identity shared by downstream callbacks.

    :param namespace: Observed Pod namespace.
    :param name: Observed Pod name.
    :param uid: Immutable observed Pod UID.
    :param container: Exact selected container name.
    :param image: Selected execution image.
    :param restart_count: Observed container restart count.

    .. attribute :: namespace
        :type: str

        Observed Pod namespace.

    .. attribute :: name
        :type: str

        Observed Pod name.

    .. attribute :: uid
        :type: str

        Immutable observed Pod UID.

    .. attribute :: container
        :type: str

        Exact selected container name.

    .. attribute :: image
        :type: str

        Selected execution image.

    .. attribute :: restart_count
        :type: int

        Observed container restart count.
    """

    namespace: str
    name: str
    uid: str
    container: str
    image: str
    restart_count: int

    def as_dict(self) -> JSONObject:
        """Render safe provider identity and restart evidence.

        :returns: JSON object suitable for reports and later callbacks.
        """
        return {
            "namespace": self.namespace,
            "name": self.name,
            "uid": self.uid,
            "container": self.container,
            "image": self.image,
            "restart_count": self.restart_count,
        }


@dataclass(frozen=True)
class ArtifactHandle:
    """Verified staging artifact held inside the exact prepared Pod.

    :param pod: Exact Pod identity containing the artifact.
    :param path: Absolute staging path inside the selected container.
    :param sha256: Expected digest proven by successful checksum verification.
    :param size: Observed artifact byte count.

    .. attribute :: pod
        :type: PodHandle

        Exact Pod identity containing the artifact.

    .. attribute :: path
        :type: str

        Absolute staging path inside the selected container.

    .. attribute :: sha256
        :type: str

        Expected digest proven by successful checksum verification.

    .. attribute :: size
        :type: int

        Observed artifact byte count.
    """

    pod: PodHandle
    path: str
    sha256: str
    size: int

    def as_dict(self) -> JSONObject:
        """Render the artifact identity without local paths or credentials.

        :returns: Exact Pod, path, checksum, and byte size.
        """
        return {
            "pod": self.pod.as_dict(),
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
        }


@dataclass
class ToolPodState(RunScopeState):
    """Typed state for ordered preparation and later consumer callbacks.

    .. attribute :: pod
        :type: PodHandle

        Exact ready Pod identity assigned before consumers execute.

    .. attribute :: execution_environment
        :type: JSONObject

        Read-only runtime, OS, package-manager, and PATH observations.

    .. attribute :: tool_observations
        :type: MutableMapping[str, JSONObject]

        Tool observations keyed by stable dependency index.

    .. attribute :: artifacts
        :type: MutableMapping[str, ArtifactHandle]

        Verified staging handles keyed by stable dependency index.

    .. attribute :: search_paths
        :type: MutableSequence[str]

        Declared installation directories prepended during probes.

    .. attribute :: readback
        :type: MutableMapping[str, JSONValue]

        Safe provider and tool read-back selected for the final report.
    """

    pod: PodHandle = field(init=False)
    execution_environment: JSONObject = field(default_factory=dict)
    tool_observations: MutableMapping[str, JSONObject] = field(default_factory=dict)
    artifacts: MutableMapping[str, ArtifactHandle] = field(default_factory=dict)
    search_paths: MutableSequence[str] = field(default_factory=list)
    readback: MutableMapping[str, JSONValue] = field(default_factory=dict)

    @property
    def result_records(self) -> MutableSequence[Mapping[str, object]]:
        """Expose the executor's canonical ordered evidence collection.

        :returns: Shared lifecycle records in execution order.
        """
        return self.records
