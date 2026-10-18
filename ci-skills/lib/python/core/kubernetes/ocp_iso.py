"""Typed state and shared native dispatch for OpenShift node ISO workflows.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Final, Optional

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.artifact_storage_models import ArtifactStorageState
from lib.python.core.kubernetes.ocp_boot_image import NodeToolState
from lib.python.core.kubernetes.ocp_input_callbacks import NodeInputState
from lib.python.core.kubernetes.ocp_native_input import NativeNodeInput
from lib.python.core.kubernetes.pod_credential_models import PodCredentialState
from lib.python.core.kubernetes.tool_pod_models import ArtifactHandle
from lib.python.core.kubernetes.tool_pod_runtime import (
    SHELL,
    ToolPodK8SCallback,
    _shell,
)
from lib.python.core.paths import SKILL_ROOT
from lib.python.core.runtime import CommandResult

OCP_NODE_IMAGE_SCRIPT: Final = SKILL_ROOT / "lib/bash/kubernetes/ocp-node-image.sh"
NATIVE_REPORT_NAME: Final = "report.json"
NATIVE_STATUS_NAME: Final = "node-image-status.json"


def _build_path(value: str) -> str:
    """Require an exact absolute native output file without normalizing it.

    :param value: Selected direct or run-owned scratch output path.
    :returns: Exact selected path.
    :raises ValueError: If the path is relative, traversing, empty, or a directory.
    """
    if (
        not isinstance(value, str)
        or not value
        or value.endswith("/")
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise ValueError("node_image_build_path_invalid")
    candidate = PurePosixPath(value)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("node_image_build_path_invalid")
    output_name = value.rsplit("/", 1)[-1]
    if output_name == ".":
        raise ValueError("node_image_build_path_invalid")
    if output_name == NATIVE_REPORT_NAME:
        raise ValueError("node_image_output_name_reserved")
    return value


@dataclass(frozen=True)
class NodeImageAction(SkillAction):
    """Bind unchanged native input to one selected in-Pod output path.

    :param node_input: Caller-selected MAC flags or exact native YAML bytes.
    :param build_path: Exact direct destination or run-owned scratch ISO path.

    .. attribute :: node_input
        :type: NativeNodeInput

        Caller-selected MAC flags or exact native YAML bytes.

    .. attribute :: build_path
        :type: str

        Exact direct destination or run-owned scratch ISO path.
    """

    node_input: NativeNodeInput
    build_path: str

    def __post_init__(self) -> None:
        """Validate only the ci-skills output wrapper.

        :raises TypeError: If native input has another type.
        :raises ValueError: If the exact output path is structurally unsafe.
        """  # noqa: DOC503 - _build_path raises the declared ValueError.
        if not isinstance(self.node_input, NativeNodeInput):
            raise TypeError("node_image_native_input_invalid")
        _build_path(self.build_path)

    @property
    def asset_directory(self) -> str:
        """Return the exact lexical parent supplied to native ``--dir``.

        :returns: Selected path prefix without normalizing caller text.
        """
        parent, _, _ = self.build_path.rpartition("/")
        return parent or "/"

    @property
    def output_name(self) -> str:
        """Return the exact final component supplied to ``--output-name``.

        :returns: Selected output filename.
        """
        return self.build_path.rsplit("/", 1)[-1]


@dataclass
class OCPState(
    ArtifactStorageState,
    PodCredentialState,
    NodeInputState,
    NodeToolState,
):
    """Compose tools, storage, credentials, input, and native ISO evidence.

    .. attribute :: native_exit_code
        :type: int

        Native node-image exit status, or -1 before a result is available.

    .. attribute :: native_transport_exit_code
        :type: int

        Local Kubernetes transport status, or -1 before transport returns.

    .. attribute :: native_error
        :type: str

        Bounded sanitized native failure detail.

    .. attribute :: native_completion_unknown
        :type: bool

        Whether local transport ended without proving remote native completion.

    .. attribute :: native_status_path
        :type: str

        Run-private in-Pod completion record, never a laptop state file.

    .. attribute :: native_status_file
        :type: ArtifactHandle

        Exact observed completion-record identity, excluded from public results.

    .. attribute :: build_completed
        :type: bool

        Whether native node-image creation returned success.

    .. attribute :: build_artifact
        :type: ArtifactHandle

        Verified generated ISO identity retained immediately after inspection.

    .. attribute :: embedded_ignition
        :type: JSONObject

        Safe evidence selected from actual embedded Ignition output.
    """

    native_exit_code: int = -1
    native_transport_exit_code: int = -1
    native_error: str = ""
    native_completion_unknown: bool = False
    native_status_path: str = ""
    native_status_file: ArtifactHandle = field(init=False, repr=False)
    build_completed: bool = False
    build_artifact: ArtifactHandle = field(init=False)
    embedded_ignition: JSONObject = field(default_factory=dict)


class NodeImageCallback(ToolPodK8SCallback[NodeImageAction]):
    """Expose composed OpenShift ISO state to native callbacks."""

    @property
    def ocp_state(self) -> OCPState:
        """Return the exact composed state required by native ISO callbacks.

        :returns: Shared OCP state.
        :raises TypeError: If composition supplied another state class.
        """
        state = self.tool_state
        if not isinstance(state, OCPState):
            raise TypeError("ocp_iso_state_required")
        return state


@lru_cache(maxsize=1)
def _script() -> str:
    """Read the installed linted native operation script once.

    :returns: Exact installed shell source.
    :raises OSError: If the installed script cannot be read.
    :raises UnicodeError: If the script is not UTF-8.
    """  # noqa: DOC502 - Path.read_text propagates decode failures.
    return OCP_NODE_IMAGE_SCRIPT.read_text(encoding="utf-8")


def run_node_image_script(
    state: OCPState,
    operation: str,
    *arguments: str,
) -> CommandResult:
    """Dispatch one installed native operation in the exact prepared Pod.

    :param state: Shared OCP state containing the selected Pod and tools.
    :param operation: Fixed preflight, run, or inspect operation.
    :param *arguments: Literal native paths and caller arguments.
    :returns: Bounded native command result for the atomic callback to interpret.
    :raises OSError: If the installed script cannot be read.
    """  # noqa: DOC502,DOC503 - installed script access propagates OSError.
    pod = state.pod
    return state.kubernetes.pod_exec(
        pod.namespace,
        pod.name,
        pod.container,
        SHELL,
        "-ceu",
        _shell(state, _script()),
        "ocp-node-image",
        operation,
        *arguments,
    )


def native_status_path(state: OCPState) -> str:
    """Derive one run-private completion path beside staged credentials.

    :param state: Shared state containing the staged kubeconfig identity.
    :returns: Exact in-Pod path reserved for native completion read-back.
    :raises AttributeError: If workload credentials have not been staged.
    """  # noqa: DOC502 - ordering requires the staged kubeconfig field.
    return str(PurePosixPath(state.kubeconfig_file.path).with_name(NATIVE_STATUS_NAME))


def read_native_exit_code(state: OCPState) -> Optional[int]:
    """Read a completed native exit record from the same prepared Pod.

    :param state: Shared state retaining the run-private status path.
    :returns: Exact native exit status, or ``None`` while no record exists.
    :raises ValueError: If the completion record is malformed.
    """
    if not state.native_status_path:
        raise ValueError("node_image_native_status_path_missing")
    result = run_node_image_script(state, "status", state.native_status_path)
    if result.returncode == 46:
        return None
    if result.returncode:
        raise ValueError("node_image_native_status_read_failed")
    try:
        value = response_decoder().decode(result.stdout)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("node_image_native_status_invalid") from exc
    exit_code = value.get("exit_code") if isinstance(value, dict) else None
    if (
        not isinstance(exit_code, int)
        or isinstance(exit_code, bool)
        or not 0 <= exit_code <= 255
    ):
        raise ValueError("node_image_native_status_invalid")
    return exit_code


__all__ = [
    "NodeImageAction",
    "NodeImageCallback",
    "OCPState",
    "native_status_path",
    "read_native_exit_code",
    "run_node_image_script",
]
