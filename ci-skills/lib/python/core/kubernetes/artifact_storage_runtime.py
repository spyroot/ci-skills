"""Bound in-Pod operations for artifact storage callbacks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Final

from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import (
    JSONObject,
    json_array,
    json_integer,
    json_object,
    json_string,
)
from lib.python.core.kubernetes.artifact_storage_models import (
    ArtifactStorageState,
    StorageDestination,
    StorageHandle,
)
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.tool_pod_models import ArtifactHandle
from lib.python.core.kubernetes.tool_pod_runtime import SHELL, _shell
from lib.python.core.paths import SKILL_ROOT

ARTIFACT_STORAGE_SCRIPT: Final = SKILL_ROOT / "lib/bash/kubernetes/artifact-storage.sh"
_DIGEST: Final = re.compile(r"[0-9a-f]{64}")
_PROBE_PREFIX: Final = ".ci-skills-storage-probe-"
_ERRORS: Final = {
    ("mount-info", 40): "artifact_storage_mount_missing",
    ("ensure-directory", 41): "artifact_storage_parent_conflict",
    ("ensure-directory", 48): "artifact_storage_parent_create_failed",
    ("probe", 42): "artifact_storage_destination_conflict",
    ("probe", 43): "artifact_storage_probe_conflict",
    ("probe", 44): "artifact_storage_probe_readback_mismatch",
    ("probe", 45): "artifact_storage_probe_cleanup_incomplete",
    ("copy", 50): "artifact_storage_source_missing",
    ("copy", 51): "artifact_storage_destination_conflict",
    ("copy", 52): "artifact_storage_destination_create_failed",
    ("copy", 53): "artifact_storage_copy_failed",
    ("copy", 60): "artifact_storage_copy_readback_missing",
    ("copy", 61): "artifact_storage_copy_checksum_failed",
    ("copy", 62): "artifact_storage_copy_size_failed",
    ("verify", 60): "artifact_storage_readback_missing",
    ("verify", 61): "artifact_storage_readback_checksum_failed",
    ("verify", 62): "artifact_storage_readback_size_failed",
    ("remove-file", 70): "artifact_storage_cleanup_failed",
    ("remove-file", 71): "artifact_storage_cleanup_incomplete",
    ("remove-directory", 72): "artifact_storage_directory_not_empty",
    ("remove-directory", 73): "artifact_storage_directory_cleanup_incomplete",
}


@lru_cache(maxsize=1)
def _script() -> str:
    """Read the installed linted script once for in-Pod dispatch.

    :returns: Exact installed shell source.
    :raises OSError: If the installed script cannot be read.
    :raises UnicodeError: If the installed script is not UTF-8.
    """  # noqa: DOC502 - Path.read_text propagates decode failures.
    return ARTIFACT_STORAGE_SCRIPT.read_text(encoding="utf-8")


def _run(
    state: ArtifactStorageState,
    operation: str,
    *arguments: str,
) -> JSONObject:
    """Dispatch one installed operation and strictly decode its JSON output.

    :param state: Shared state containing the verified Pod and Kubernetes service.
    :param operation: Fixed operation selected by the callback.
    :param *arguments: Literal paths or run identifiers.
    :returns: Strictly decoded result object.
    :raises OSError: If the installed script cannot be read.
    :raises KubernetesError: If the Pod operation or strict decoding fails.
    """  # noqa: DOC503 - installed script reads propagate OSError.
    pod = state.pod
    result = state.kubernetes.pod_exec(
        pod.namespace,
        pod.name,
        pod.container,
        SHELL,
        "-ceu",
        _shell(state, _script()),
        "artifact-storage",
        operation,
        *arguments,
    )
    if result.returncode:
        raise KubernetesError(
            _ERRORS.get(
                (operation, result.returncode),
                f"artifact_storage_{operation.replace('-', '_')}_failed",
            )
        )
    try:
        value = response_decoder().decode(result.stdout)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise KubernetesError("artifact_storage_response_invalid") from exc
    if not isinstance(value, Mapping):
        raise KubernetesError("artifact_storage_response_invalid")
    return value


def _required_boolean(document: JSONObject, key: str) -> bool:
    """Read one strict Boolean response field.

    :param document: Decoded operation response.
    :param key: Required field name.
    :returns: Exact Boolean value.
    :raises KubernetesError: If the field is absent or another JSON type.
    """
    value = document.get(key)
    if not isinstance(value, bool):
        raise KubernetesError("artifact_storage_response_invalid")
    return value


def _artifact_observation(
    document: JSONObject,
    pod_path: str,
    state: ArtifactStorageState,
) -> ArtifactHandle:
    """Parse one strict checksum and size observation.

    :param document: Decoded script response.
    :param pod_path: Exact in-Pod artifact path.
    :param state: State containing the Pod that produced the observation.
    :returns: Typed verified artifact handle.
    :raises KubernetesError: If output shape, digest, or size is invalid.
    """
    try:
        digest = json_string(document["sha256"])
        size = json_integer(document["size"])
    except (KeyError, TypeError) as exc:
        raise KubernetesError("artifact_storage_readback_invalid") from exc
    if _DIGEST.fullmatch(digest) is None or size < 0:
        raise KubernetesError("artifact_storage_readback_invalid")
    return ArtifactHandle(
        pod=state.pod,
        path=pod_path,
        sha256=digest,
        size=size,
    )


def normalized_digest(value: str) -> str:
    """Normalize a raw or prefixed SHA-256 for exact identity comparison.

    :param value: Expected hexadecimal digest, optionally prefixed by ``sha256:``.
    :returns: Lowercase raw hexadecimal digest.
    :raises KubernetesError: If the value is not one SHA-256 identity.
    """
    digest = value.removeprefix("sha256:").casefold()
    if _DIGEST.fullmatch(digest) is None:
        raise KubernetesError("artifact_storage_source_digest_invalid")
    return digest


def _mount_observation(
    state: ArtifactStorageState,
    path: str,
) -> tuple[str, str, str]:
    """Read one actual backing mount through native ``findmnt`` JSON.

    :param state: Shared state containing the prepared Pod.
    :param path: Existing path whose backing mount must be observed.
    :returns: Actual mount target, filesystem type, and native source.
    :raises KubernetesError: If native JSON lacks one complete filesystem record.
    """
    document = _run(state, "mount-info", path)
    try:
        filesystems = json_array(document["filesystems"])
        if len(filesystems) != 1:
            raise KubernetesError("artifact_storage_mount_observation_invalid")
        filesystem = json_object(filesystems[0])
        return (
            json_string(filesystem["target"]),
            json_string(filesystem["fstype"]),
            json_string(filesystem["source"]),
        )
    except (KeyError, TypeError) as exc:
        raise KubernetesError("artifact_storage_mount_observation_invalid") from exc


def _destination_directories(destination: StorageDestination) -> Sequence[str]:
    """Return each lexical output-parent directory below the declared mount.

    :param destination: Caller-selected mount and final artifact path.
    :returns: Parent paths ordered from the mount child to the final parent.
    """
    mount = destination.mount_path.rstrip("/") or "/"
    parent = _artifact_parent(destination)
    relative = parent[len(mount) :]
    if relative and not relative.startswith("/"):
        relative = "/" + relative
    current = mount
    directories: list[str] = []
    for component in re.finditer(r"/+[^/]+", relative):
        current += component.group()
        directories.append(current)
    return tuple(directories)


def _artifact_parent(destination: StorageDestination) -> str:
    """Return the exact caller-selected artifact parent without normalization.

    :param destination: Caller-selected destination with an absolute artifact path.
    :returns: Exact lexical path prefix before the artifact filename.
    """
    parent, _, _ = destination.artifact_path.rpartition("/")
    return parent or "/"


def _prepare_parent(
    state: ArtifactStorageState,
    destination: StorageDestination,
    declared_target: str,
) -> tuple[str, ...]:
    """Make only absent output-parent directories and record exact ownership.

    :param state: Shared state containing the prepared Pod.
    :param destination: Caller-selected destination.
    :param declared_target: Actual target backing the declared volume mount.
    :returns: Newly created directory paths in creation order.
    :raises KubernetesError: If an existing component is not a directory or native
        creation fails.
    """
    created: list[str] = []
    for path in _destination_directories(destination):
        result = _run(state, "ensure-directory", path)
        if _required_boolean(result, "created"):
            created.append(path)
            state.created_destination_directories.append(path)
        observed_target, _, _ = _mount_observation(state, path)
        if not _under_mount(observed_target, declared_target):
            raise KubernetesError("artifact_storage_parent_mount_escape")
    return tuple(created)


def _under_mount(candidate: str, declared: str) -> bool:
    """Check whether an observed nested mount belongs below the declared mount.

    :param candidate: Actual mount target backing the artifact parent.
    :param declared: Actual target observed for the declared mount path.
    :returns: Whether the targets are equal or candidate is nested below declared.
    """
    if candidate == declared:
        return True
    prefix = declared if declared == "/" else declared.rstrip("/") + "/"
    return candidate.startswith(prefix)


def check_destination(
    state: ArtifactStorageState,
    destination: StorageDestination,
) -> tuple[StorageHandle, tuple[str, ...]]:
    """Prepare and probe the actual requested output parent.

    :param state: Shared state containing the prepared Pod and run scope.
    :param destination: Exact caller-selected destination.
    :returns: Verified storage handle and exact directories created by this run.
    :raises KubernetesError: If declared storage is fallback, actual parent escapes
        that storage, output exists, probing fails, or probe cleanup is incomplete.
    """
    declared_target, _, _ = _mount_observation(state, destination.mount_path)
    if PurePosixPath(declared_target) != PurePosixPath(destination.mount_path):
        raise KubernetesError("artifact_storage_container_fallback")
    created = _prepare_parent(state, destination, declared_target)
    parent = _artifact_parent(destination)
    parent_target, protocol, route = _mount_observation(state, parent)
    if not _under_mount(parent_target, declared_target):
        raise KubernetesError("artifact_storage_parent_mount_escape")
    probe_name = f"{_PROBE_PREFIX}{state.run_scope.run_id}"
    separator = "" if parent.endswith("/") else "/"
    probe_path = f"{parent}{separator}{probe_name}"
    probe = _run(
        state,
        "probe",
        destination.artifact_path,
        probe_path,
        state.run_scope.run_id,
    )
    if not _required_boolean(probe, "verified"):
        raise KubernetesError("artifact_storage_probe_readback_mismatch")
    return (
        StorageHandle(
            destination=destination,
            observed_mount_path=parent_target,
            protocol=protocol,
            access_route=route,
            capacity_bytes=None,
        ),
        created,
    )


def copy_artifact(
    state: ArtifactStorageState,
    source: ArtifactHandle,
    destination_path: str,
) -> ArtifactHandle:
    """Copy one verified source into an exclusively created final path.

    :param state: Shared state containing the prepared Pod.
    :param source: Verified producer output retained in that Pod.
    :param destination_path: Exact caller-selected final path.
    :returns: Immediate destination checksum and size observation.
    :raises KubernetesError: If source identity differs, output exists, or copy fails.
    """
    if source.pod != state.pod:
        raise KubernetesError("artifact_storage_source_pod_mismatch")
    document = _run(state, "copy", source.path, destination_path)
    state.destination_created = True
    state.copied_destination = destination_path
    return _artifact_observation(document, destination_path, state)


def observe_artifact(
    state: ArtifactStorageState,
    path: str,
) -> ArtifactHandle:
    """Independently read the artifact identity from the prepared Pod.

    :param state: Shared state containing the exact Pod.
    :param path: Exact in-Pod artifact path.
    :returns: Read-back checksum and byte size.
    :raises KubernetesError: If the file is absent or evidence is malformed.
    """  # noqa: DOC502 - the bounded Pod operation raises on failed read-back.
    return _artifact_observation(_run(state, "verify", path), path, state)


def remove_artifact(state: ArtifactStorageState, path: str) -> None:
    """Remove one exact unverified path created by this invocation.

    :param state: Shared state containing the exact Pod.
    :param path: Exact path previously recorded by ``copy_artifact``.
    :raises KubernetesError: If removal fails or the path remains.
    """
    if not state.destination_created or state.copied_destination != path:
        raise KubernetesError("artifact_storage_cleanup_ownership_mismatch")
    result = _run(state, "remove-file", path)
    if not _required_boolean(result, "absent"):
        raise KubernetesError("artifact_storage_cleanup_incomplete")
    state.destination_created = False
    state.copied_destination = ""


def remove_directories(
    state: ArtifactStorageState,
    directories: Sequence[str],
) -> tuple[str, ...]:
    """Remove only empty directories recorded as created by this invocation.

    :param state: Shared state containing the exact Pod.
    :param directories: Exact owned paths in creation order.
    :returns: Removed paths ordered deepest first.
    :raises KubernetesError: If an owned path is nonempty or remains after removal.
    """
    removed: list[str] = []
    for path in reversed(directories):
        result = _run(state, "remove-directory", path)
        if not _required_boolean(result, "absent"):
            raise KubernetesError("artifact_storage_directory_cleanup_incomplete")
        removed.append(path)
    return tuple(removed)
