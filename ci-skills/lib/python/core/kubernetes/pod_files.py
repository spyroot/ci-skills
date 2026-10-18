"""Stage exact bytes in a prepared Pod and retain explicit file ownership.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.tool_pod_models import ArtifactHandle, ToolPodState
from lib.python.core.paths import SKILL_ROOT
from lib.python.core.runtime import CommandResult, sanitize

POD_FILE_SCRIPT: Final = SKILL_ROOT / "lib/bash/kubernetes/pod-file.sh"


@dataclass(frozen=True)
class StagedPodFile:
    """Identify verified bytes and whether this invocation created their file.

    :param artifact: Exact observed file and Pod identity.
    :param created: True only when exclusive creation succeeded.

    .. attribute :: artifact
        :type: ArtifactHandle

        Exact observed file and Pod identity.

    .. attribute :: created
        :type: bool

        Whether this invocation owns file removal.
    """

    artifact: ArtifactHandle
    created: bool


def _file_command(
    state: ToolPodState, path: PurePosixPath, operation: str, content: bytes
) -> CommandResult:
    """Dispatch one bounded file operation without exposing stdin in arguments.

    :param state: Authenticated service and prepared Pod.
    :param path: Exact absolute container path.
    :param operation: Fixed write, stage or remove operation.
    :param content: Private stdin bytes consumed by the operation.
    :returns: Successful native result for the operation owner to decode.
    :raises ValueError: The path is not absolute.
    :raises OSError: The installed script cannot be read.
    :raises KubernetesError: The native operation fails.
    """  # noqa: DOC503 - Path.read_text propagates file access errors.
    if not path.is_absolute():
        raise ValueError("pod_file_path_must_be_absolute")
    pod = state.pod
    result = state.kubernetes.pod_exec(
        pod.namespace,
        pod.name,
        pod.container,
        "/bin/sh",
        "-ceu",
        POD_FILE_SCRIPT.read_text(encoding="utf-8"),
        "pod-file",
        str(path),
        operation,
        input_data=content,
    )
    if result.returncode:
        raise KubernetesError(
            f"pod_file_{operation}_failed:exit={result.returncode}:"
            f"{sanitize(result.stderr)}"
        )
    return result


def _stage(
    state: ToolPodState, path: PurePosixPath, content: bytes, operation: str
) -> StagedPodFile:
    """Verify one exclusive creation or identical-byte reuse result.

    :param state: Authenticated service and prepared Pod.
    :param path: Exact absolute container destination.
    :param content: Unchanged caller bytes.
    :param operation: Write requires creation; stage permits identical reuse.
    :returns: Verified identity with native ownership evidence.
    :raises KubernetesError: Digest, byte count or ownership evidence differs.
    """
    result = _file_command(state, path, operation, content)
    expected_digest = hashlib.sha256(content).hexdigest()
    observed = result.stdout.split()
    if (
        len(observed) != 3
        or observed[:2] != [expected_digest, str(len(content))]
        or observed[2] not in ("created", "reused")
        or (operation == "write" and observed[2] != "created")
    ):
        raise KubernetesError("pod_file_readback_mismatch")
    return StagedPodFile(
        ArtifactHandle(state.pod, str(path), expected_digest, len(content)),
        observed[2] == "created",
    )


def write_pod_file(
    state: ToolPodState, path: PurePosixPath, content: bytes
) -> ArtifactHandle:
    """Write a new mode-0600 file without replacing an existing path.

    :param state: Authenticated service and prepared Pod.
    :param path: Exact absolute destination inside the Pod.
    :param content: Exact bytes, sent through private stdin.
    :returns: Verified identity of the exclusively created file.
    :raises ValueError: The path is not absolute.
    :raises OSError: The installed script cannot be read.
    :raises KubernetesError: Creation fails or native read-back differs.
    """  # noqa: DOC502 - File operation errors propagate from the shared helper.
    return _stage(state, path, content, "write").artifact


def stage_pod_file(
    state: ToolPodState, path: PurePosixPath, content: bytes
) -> StagedPodFile:
    """Create a private file or reuse identical caller-owned bytes unchanged.

    :param state: Authenticated service and prepared Pod.
    :param path: Exact absolute destination inside the Pod.
    :param content: Exact bytes, sent through private stdin.
    :returns: Verified identity and whether this invocation owns the file.
    :raises ValueError: The path is not absolute.
    :raises OSError: The installed script cannot be read.
    :raises KubernetesError: A different file exists or native read-back fails.
    """  # noqa: DOC502 - File operation errors propagate from the shared helper.
    return _stage(state, path, content, "stage")


def remove_pod_file(state: ToolPodState, artifact: ArtifactHandle) -> bool:
    """Remove an owned file only while its observed contents still match.

    The callback owning the handle must call this only for files it created.
    Identical caller-owned files returned as reused are never cleanup inputs.

    :param state: Authenticated service and the same prepared Pod.
    :param artifact: Previously verified owned file identity.
    :returns: True when removed, false when already absent.
    :raises ValueError: The handle belongs to another Pod.
    :raises KubernetesError: The file changed or deletion/read-back failed.
    """
    if artifact.pod != state.pod:
        raise ValueError("pod_file_cleanup_pod_mismatch")
    expected = f"{artifact.sha256} {artifact.size}\n".encode("ascii")
    result = _file_command(state, PurePosixPath(artifact.path), "remove", expected)
    observed = result.stdout.strip()
    if observed not in ("removed", "absent"):
        raise KubernetesError("pod_file_cleanup_readback_mismatch")
    return observed == "removed"
