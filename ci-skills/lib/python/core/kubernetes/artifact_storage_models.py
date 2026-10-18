"""Typed destination and state models for in-cluster artifact delivery.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, MutableSequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final, Literal, Optional

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import JSONObject, JSONValue, copy_json_object
from lib.python.core.kubernetes.run_scope import validate_namespace
from lib.python.core.kubernetes.tool_pod_models import (
    ArtifactHandle,
    ToolPodMount,
    ToolPodState,
)
from lib.python.core.schema import SpecificationError

DESTINATION_VOLUME_NAME: Final = "artifact-destination"
DESTINATION_FIELDS: Final = frozenset(
    {"namespace", "volumeSource", "mountPath", "artifactPath", "buildOutput"}
)
BuildOutput = Literal["destination", "scratch"]


def _required_string(document: Mapping[str, object], key: str) -> str:
    """Read one required nonempty string from the destination wrapper.

    :param document: Parsed caller wrapper.
    :param key: Required field name.
    :returns: Stripped nonempty field value.
    :raises SpecificationError: If the field is absent, empty, or not text.
    """
    value = document.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise SpecificationError(f"invalid_destination:/{key}:string")
    return value


def _absolute_path(value: str, key: str) -> str:
    """Require one absolute container path without changing caller text.

    :param value: Caller-supplied path.
    :param key: Field name used in a stable diagnostic.
    :returns: Exact caller path after structural validation.
    :raises SpecificationError: If the path is relative, contains traversal, or
        contains a control character unsafe for a native process argument.
    """
    if any(character in value for character in ("\x00", "\n", "\r")):
        raise SpecificationError(f"invalid_destination:/{key}:control_character")
    candidate = PurePosixPath(value)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise SpecificationError(f"invalid_destination:/{key}:absolute_path")
    return value


@dataclass(frozen=True)
class StorageDestination:
    """Caller-selected native volume and exact final artifact location.

    :param namespace: Existing namespace containing the tool Pod.
    :param volume_source: Native Kubernetes ``VolumeSource`` passed through unchanged.
    :param mount_path: Absolute container mount point.
    :param artifact_path: Exact final artifact path below the mount point.
    :param build_output: Whether the producer writes to the destination or scratch.

    .. attribute :: namespace
        :type: str

        Existing namespace containing the tool Pod.

    .. attribute :: volume_source
        :type: JSONObject

        Operation-owned native Kubernetes ``VolumeSource``.

    .. attribute :: mount_path
        :type: str

        Absolute container mount point.

    .. attribute :: artifact_path
        :type: str

        Exact final artifact path below the mount point.

    .. attribute :: build_output
        :type: BuildOutput

        Whether the producer writes to the destination or scratch.
    """

    namespace: str
    volume_source: JSONObject = field(repr=False)
    mount_path: str
    artifact_path: str
    build_output: BuildOutput

    def __post_init__(self) -> None:
        """Validate only the ci-skills wrapper and retain native source bytes.

        :raises ValueError: If namespace, paths, output mode, or path relationship
            is invalid.
        :raises TypeError: If ``volume_source`` contains non-JSON values.
        """
        namespace = validate_namespace(self.namespace)
        mount_path = _absolute_path(self.mount_path, "mountPath")
        artifact_path = _absolute_path(self.artifact_path, "artifactPath")
        if self.build_output not in ("destination", "scratch"):
            raise ValueError("artifact_storage_build_output_invalid")
        if not self.volume_source:
            raise ValueError("artifact_storage_volume_source_empty")
        try:
            relative = PurePosixPath(artifact_path).relative_to(
                PurePosixPath(mount_path)
            )
        except ValueError as exc:
            raise ValueError("artifact_storage_artifact_outside_mount") from exc
        if str(relative) in ("", "."):
            raise ValueError("artifact_storage_artifact_path_is_mount")
        object.__setattr__(self, "namespace", namespace)
        object.__setattr__(self, "mount_path", mount_path)
        object.__setattr__(self, "artifact_path", artifact_path)
        try:
            owned_source = copy_json_object(self.volume_source)
        except TypeError as exc:
            raise TypeError("artifact_storage_volume_source_invalid") from exc
        object.__setattr__(self, "volume_source", MappingProxyType(owned_source))

    @classmethod
    def load(cls, source: Path) -> StorageDestination:
        """Read a destination wrapper without interpreting native volume fields.

        :param source: Caller-selected JSON document.
        :returns: Validated typed destination.
        :raises OSError: If the selected file cannot be read.
        :raises SpecificationError: If JSON or wrapper fields are invalid.
        """  # noqa: DOC503 - file reads propagate OSError.
        try:
            value = response_decoder().decode(source.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SpecificationError("invalid_destination:/:json") from exc
        if not isinstance(value, Mapping):
            raise SpecificationError("invalid_destination:/:object")
        return cls.from_document(value)

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> StorageDestination:
        """Construct a destination from the five ci-skills wrapper fields.

        :param document: Parsed caller wrapper.
        :returns: Validated typed destination.
        :raises SpecificationError: If fields are missing, unknown, or malformed.
        """
        fields = set(document)
        if fields != DESTINATION_FIELDS:
            missing = sorted(DESTINATION_FIELDS - fields)
            unknown = sorted(fields - DESTINATION_FIELDS)
            reason = "missing=" + ",".join(missing) if missing else ""
            if unknown:
                reason = f"{reason};" if reason else ""
                reason += "unknown=" + ",".join(unknown)
            raise SpecificationError(f"invalid_destination:/:fields:{reason}")
        volume_source = document["volumeSource"]
        if not isinstance(volume_source, Mapping):
            raise SpecificationError("invalid_destination:/volumeSource:object")
        build_output = _required_string(document, "buildOutput")
        if build_output not in ("destination", "scratch"):
            raise SpecificationError("invalid_destination:/buildOutput:enum")
        try:
            return cls(
                namespace=_required_string(document, "namespace"),
                volume_source=copy_json_object(volume_source),
                mount_path=_required_string(document, "mountPath"),
                artifact_path=_required_string(document, "artifactPath"),
                build_output=build_output,
            )
        except (TypeError, ValueError) as exc:
            raise SpecificationError(f"invalid_destination:/:{exc}") from exc

    @property
    def is_temporary(self) -> bool:
        """Report whether Kubernetes binds the selected storage to Pod lifetime.

        :returns: True for native ``emptyDir`` or generic ephemeral volume sources.
        """
        return "emptyDir" in self.volume_source or "ephemeral" in self.volume_source

    @property
    def fingerprint(self) -> str:
        """Bind the exact wrapper without exposing provider-specific values.

        :returns: Prefixed SHA-256 of canonical destination JSON.
        """
        encoded = json.dumps(
            self.as_dict(), allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def tool_pod_mount(self) -> ToolPodMount:
        """Create the unchanged native mount consumed by tool Pod preparation.

        :returns: Writable destination mount with a stable internal volume name.
        """
        return ToolPodMount(
            name=DESTINATION_VOLUME_NAME,
            volume_source=self.volume_source,
            mount_path=self.mount_path,
        )

    def as_dict(self) -> JSONObject:
        """Render the exact caller wrapper for plan and fingerprint binding.

        :returns: Operation-owned destination object.
        """
        return {
            "namespace": self.namespace,
            "volumeSource": copy_json_object(self.volume_source),
            "mountPath": self.mount_path,
            "artifactPath": self.artifact_path,
            "buildOutput": self.build_output,
        }


@dataclass(frozen=True)
class ArtifactStorageAction(SkillAction):
    """Select one destination for storage callbacks in a composed workflow.

    :param destination: Validated caller-selected native storage wrapper.

    .. attribute :: destination
        :type: StorageDestination

        Validated caller-selected native storage wrapper.
    """

    destination: StorageDestination


@dataclass(frozen=True)
class StorageHandle:
    """Verified in-Pod storage identity and access observations.

    :param destination: Exact caller destination bound to the observation.
    :param observed_mount_path: Mount point read from the container mount table.
    :param protocol: Filesystem type observed in the container mount table.
    :param access_route: Native mount source observed by the container.
    :param capacity_bytes: Available bytes when the container reports capacity.

    .. attribute :: destination
        :type: StorageDestination

        Exact caller destination bound to the observation.

    .. attribute :: observed_mount_path
        :type: str

        Mount point read from the container mount table.

    .. attribute :: protocol
        :type: str

        Filesystem type observed in the container mount table.

    .. attribute :: access_route
        :type: str

        Native mount source observed by the container.

    .. attribute :: capacity_bytes
        :type: Optional[int]

        Available bytes when the container reports capacity.
    """

    destination: StorageDestination
    observed_mount_path: str
    protocol: str
    access_route: str
    capacity_bytes: Optional[int]

    def as_dict(self) -> JSONObject:
        """Render safe storage identity, lifetime, and write-probe evidence.

        :returns: JSON object without provider credentials or file contents.
        """
        result: dict[str, JSONValue] = {
            "namespace": self.destination.namespace,
            "mount_path": self.destination.mount_path,
            "artifact_path": self.destination.artifact_path,
            "build_output": self.destination.build_output,
            "volume_source_types": sorted(self.destination.volume_source),
            "destination_fingerprint": self.destination.fingerprint,
            "observed_mount_path": self.observed_mount_path,
            "protocol": self.protocol,
            "access_route": self.access_route,
            "lifetime": (
                "pod" if self.destination.is_temporary else "caller_selected_volume"
            ),
            "writable": True,
            "output_absent": True,
        }
        if self.capacity_bytes is not None:
            result["capacity_bytes"] = self.capacity_bytes
        return result


@dataclass
class ArtifactStorageState(ToolPodState):
    """Share verified source and destination handles across storage callbacks.

    The native producer state subclasses this class and assigns ``source_artifact``
    only after it verifies the producer output. Storage callbacks never infer or
    reconstruct that output identity.

    .. attribute :: storage_handle
        :type: StorageHandle

        Destination identity assigned after its unique write probe succeeds.

    .. attribute :: source_artifact
        :type: ArtifactHandle

        Producer output assigned only after source checksum verification.

    .. attribute :: delivery_candidate
        :type: ArtifactHandle

        Direct or copied final path awaiting independent read-back.

    .. attribute :: stored_artifact
        :type: ArtifactHandle

        Final artifact assigned after independent checksum and size equality.

    .. attribute :: destination_created
        :type: bool

        Whether ``CopyArtifact`` exclusively created the final path.

    .. attribute :: destination_verified
        :type: bool

        Whether independent final-path read-back matched the source.

    .. attribute :: copied_destination
        :type: str

        Exact owned path eligible for cleanup before successful verification.

    .. attribute :: created_destination_directories
        :type: MutableSequence[str]

        Exact output-parent directories created by destination preparation.
    """

    storage_handle: StorageHandle = field(init=False)
    source_artifact: ArtifactHandle = field(init=False)
    delivery_candidate: ArtifactHandle = field(init=False)
    stored_artifact: ArtifactHandle = field(init=False)
    destination_created: bool = False
    destination_verified: bool = False
    copied_destination: str = ""
    created_destination_directories: MutableSequence[str] = field(default_factory=list)
