"""Native OpenShift node ISO build callback.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.artifact_storage_runtime import observe_artifact
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.ocp_iso import (
    NodeImageCallback,
    native_status_path,
    read_native_exit_code,
    run_node_image_script,
)
from lib.python.core.kubernetes.pod_files import remove_pod_file
from lib.python.core.kubernetes.tool_pod_runtime import retain_tool_pod
from lib.python.core.runtime import sanitize
from lib.python.sys.status import APPLIED, NO_OP

_PREFLIGHT_ERRORS = {
    40: "node_image_asset_directory_missing",
    41: "node_image_output_conflict",
    42: "node_image_report_conflict",
    43: "node_image_existing_iso_conflict",
    44: "node_image_asset_scan_failed",
    45: "node_image_native_status_conflict",
}
_UNCERTAIN_EXIT_CODES = frozenset({124, 125, 130, 137, 143})


def _preserve_uncertain_native_run(callback: NodeImageCallback) -> None:
    """Retain exact resources and files while native completion is unknown.

    :param callback: Bound node-image callback whose remote process may continue.
    """
    state = callback.ocp_state
    state.native_completion_unknown = True
    retain_tool_pod(state)
    state.retained_pod_files.add(state.native_status_path)
    if hasattr(state, "kubeconfig_file"):
        state.retained_pod_files.add(state.kubeconfig_file.path)
    if hasattr(state, "pull_secret_file"):
        state.retained_pod_files.add(state.pull_secret_file.path)
    state.retained_pod_files.update(
        staged.artifact.path for staged in state.staged_inputs
    )


def _verify_asset_preflight(callback: NodeImageCallback) -> None:
    """Prove native output, report, and recursive ISO paths are absent.

    :param callback: Bound node-image callback with selected path and Pod state.
    :raises KubernetesError: If native asset inspection fails or finds a conflict.
    """
    action = callback.action
    result = run_node_image_script(
        callback.ocp_state,
        "preflight",
        action.asset_directory,
        action.output_name,
        callback.ocp_state.native_status_path,
    )
    if result.returncode:
        raise KubernetesError(
            _PREFLIGHT_ERRORS.get(
                result.returncode, "node_image_asset_preflight_failed"
            )
        )
    try:
        value = response_decoder().decode(result.stdout)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise KubernetesError("node_image_asset_preflight_invalid") from exc
    if not isinstance(value, dict) or value != {"verified": True}:
        raise KubernetesError("node_image_asset_preflight_invalid")


class BuildNodeImage(NodeImageCallback):
    """Run only the release-compatible native node-image creation command."""

    def run(self) -> JSONObject:
        """Protect caller assets, then run and wait for native ISO creation.

        :returns: Native successful completion awaiting ISO inspection.
        :raises KubernetesError: If prerequisites are absent, assets conflict, or
            native creation returns a failure or uncertain transport result.
        :raises BaseException: A signal or runtime interruption is preserved after
            marking native completion unknown.
        """  # noqa: DOC503 - preserve interruptions after recording uncertainty.
        state = self.ocp_state
        if not hasattr(state, "kubeconfig_file"):
            raise KubernetesError("node_image_kubeconfig_missing")
        if not hasattr(state, "pull_secret_file"):
            raise KubernetesError("node_image_pull_secret_missing")
        state.native_status_path = native_status_path(state)
        _verify_asset_preflight(self)
        state.native_completion_unknown = False
        try:
            result = run_node_image_script(
                state,
                "run",
                self.action.asset_directory,
                self.action.output_name,
                state.kubeconfig_file.path,
                state.pull_secret_file.path,
                state.native_status_path,
                *self.action.node_input.arguments(),
            )
        except BaseException:
            _preserve_uncertain_native_run(self)
            raise
        state.native_transport_exit_code = result.returncode
        try:
            native_exit_code = read_native_exit_code(state)
        except ValueError as exc:
            _preserve_uncertain_native_run(self)
            state.native_error = str(exc)
            raise KubernetesError(state.native_error) from exc
        if native_exit_code is None:
            _preserve_uncertain_native_run(self)
            state.native_error = sanitize(result.stderr or result.stdout, 4096)
            reason = state.native_error or "native_completion_record_missing"
            state.readback["node_image_failure"] = {
                "transport_exit_code": result.returncode,
                "error": reason,
                "completion_unknown": True,
            }
            raise KubernetesError(
                f"node_image_native_completion_unknown:"
                f"transport_exit={result.returncode}:{reason}"
            )
        state.native_exit_code = native_exit_code
        state.native_status_file = observe_artifact(state, state.native_status_path)
        if (
            result.returncode not in _UNCERTAIN_EXIT_CODES
            and result.returncode != native_exit_code
        ):
            _preserve_uncertain_native_run(self)
            state.native_error = "node_image_native_status_mismatch"
            raise KubernetesError(state.native_error)
        if native_exit_code:
            state.native_error = sanitize(result.stderr or result.stdout, 4096)
            reason = state.native_error or "native_command_failed"
            state.readback["node_image_failure"] = {
                "exit_code": native_exit_code,
                "transport_exit_code": result.returncode,
                "error": reason,
                "completion_unknown": False,
            }
            raise KubernetesError(
                f"node_image_native_failed:exit={native_exit_code}:{reason}"
            )
        state.build_completed = True
        state.native_error = ""
        return {
            "action": APPLIED,
            "verified": False,
            "build_path": self.action.build_path,
            "native_exit_code": 0,
            "transport_exit_code": result.returncode,
        }

    def cleanup(self) -> JSONObject:
        """Report unresolved native ownership without guessing provider resources.

        :returns: No-op evidence and whether exact completion remains unknown.
        """
        state = self.ocp_state
        removed = False
        if not state.native_completion_unknown and hasattr(state, "native_status_file"):
            removed = remove_pod_file(state, state.native_status_file)
        return {
            "action": APPLIED if removed else NO_OP,
            "verified": False,
            "native_completion_unknown": state.native_completion_unknown,
            "requires_preservation": state.native_completion_unknown,
            "completion_record_removed": removed,
        }


__all__ = ["BuildNodeImage"]
