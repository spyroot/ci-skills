"""Atomic callbacks for private credentials inside a prepared tool Pod.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence

from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import JSONObject, JSONValue, json_object
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.pod_credential_models import (
    CredentialAction,
    NativePermission,
    PodCredentialState,
)
from lib.python.core.kubernetes.pod_files import remove_pod_file, write_pod_file
from lib.python.core.kubernetes.tool_pod_runtime import (
    SHELL,
    ToolPodK8SCallback,
    _shell,
)
from lib.python.core.runtime import CommandResult, error_class
from lib.python.sys.status import APPLIED, NO_OP, VERIFIED


def _strict_object(payload: str, error: str) -> JSONObject:
    """Decode one strict JSON object without exposing its source bytes.

    :param payload: Provider or native response kept inside the callback boundary.
    :param error: Stable error raised when decoding or shape validation fails.
    :returns: Strictly decoded JSON object.
    :raises KubernetesError: If JSON is malformed, duplicated, or not an object.
    """
    try:
        return json_object(response_decoder().decode(payload))
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise KubernetesError(error) from exc


def _sequence(value: JSONValue, error: str) -> Sequence[JSONValue]:
    """Require a non-string JSON sequence from a provider document.

    :param value: Decoded JSON value.
    :param error: Stable error raised for another shape.
    :returns: Existing sequence through its read-only interface.
    :raises KubernetesError: If the value is not a JSON array.
    """
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise KubernetesError(error)
    return value


def _exec_command(configuration: JSONObject) -> str:
    """Read an optional exec-plugin command from one minified kubeconfig.

    :param configuration: Strictly decoded native flattened kubeconfig.
    :returns: Exact exec command, or empty text for inline credentials.
    :raises KubernetesError: If selected user or exec configuration is malformed.
    """
    try:
        users = _sequence(configuration["users"], "pod_kubeconfig_user_invalid")
        if len(users) != 1:
            raise KubernetesError("pod_kubeconfig_user_invalid")
        selected = json_object(users[0])
        user = json_object(selected["user"])
    except (KeyError, TypeError) as exc:
        raise KubernetesError("pod_kubeconfig_user_invalid") from exc
    plugin = user.get("exec")
    if plugin is None:
        return ""
    if not isinstance(plugin, Mapping):
        raise KubernetesError("pod_kubeconfig_exec_plugin_invalid")
    command = plugin.get("command")
    if not isinstance(command, str) or not command:
        raise KubernetesError("pod_kubeconfig_exec_plugin_invalid")
    return command


def _pod_command(state: PodCredentialState, *arguments: str) -> CommandResult:
    """Run ``oc`` with the prepared dependency search path.

    :param state: Shared state containing the exact prepared Pod.
    :param *arguments: Literal native arguments passed after ``oc``.
    :returns: Bounded native result without interpreting stdout or stderr.
    """
    pod = state.pod
    return state.kubernetes.pod_exec(
        pod.namespace,
        pod.name,
        pod.container,
        SHELL,
        "-ceu",
        _shell(state, 'exec oc "$@"'),
        "pod-credential",
        *arguments,
    )


def _exec_plugin_available(state: PodCredentialState, command: str) -> bool:
    """Check whether one selected external auth command exists in the Pod.

    :param state: Shared state containing the exact prepared Pod.
    :param command: Exact command named by the flattened kubeconfig.
    :returns: Whether the prepared Pod can resolve that executable.
    """
    pod = state.pod
    result = state.kubernetes.pod_exec(
        pod.namespace,
        pod.name,
        pod.container,
        SHELL,
        "-ceu",
        _shell(
            state,
            'IFS= read -r plugin_command; command -v "$plugin_command" >/dev/null 2>&1',
        ),
        "pod-credential",
        input_data=f"{command}\n".encode(),
    )
    return result.returncode == 0


def _workload_identity(document: JSONObject) -> str:
    """Read the username from native ``oc auth whoami`` JSON.

    :param document: Strict native identity response.
    :returns: Nonempty workload username.
    :raises KubernetesError: If native identity fields are absent or malformed.
    """
    status = document.get("status")
    user_info = status.get("userInfo") if isinstance(status, Mapping) else None
    if not isinstance(user_info, Mapping):
        user_info = document.get("userInfo")
    identity = user_info.get("username") if isinstance(user_info, Mapping) else None
    if not isinstance(identity, str) or not identity:
        raise KubernetesError("pod_workload_identity_invalid")
    return identity


def _registry_bytes(value: str) -> bytes:
    """Decode and structurally verify one native registry configuration.

    :param value: Base64 text from the selected Secret data field.
    :returns: Exact decoded bytes without JSON normalization.
    :raises KubernetesError: If base64, UTF-8, JSON, or object shape is invalid.
    """
    try:
        payload = base64.b64decode(value.encode("ascii"), validate=True)
        _strict_object(payload.decode("utf-8"), "pull_secret_payload_invalid")
    except (UnicodeEncodeError, UnicodeDecodeError, binascii.Error) as exc:
        raise KubernetesError("pull_secret_payload_invalid") from exc
    return payload


class PodCredentialCallback(ToolPodK8SCallback[CredentialAction]):
    """Expose the exact private-credential state to concrete callbacks."""

    @property
    def credential_state(self) -> PodCredentialState:
        """Return the shared state required by private credential callbacks.

        :returns: Bound Pod credential state.
        :raises TypeError: If composition supplied another state class.
        """
        state = self.tool_state
        if not isinstance(state, PodCredentialState):
            raise TypeError("pod_credential_state_required")
        return state


class StageWorkloadCredential(PodCredentialCallback):
    """Export the selected caller kubeconfig and stage it without argv bytes."""

    def run(self) -> JSONObject:
        """Stage one flattened kubeconfig after checking external auth tooling.

        :returns: Safe path, identity, and auth-transport evidence.
        :raises KubernetesError: If export, strict decoding, plugin discovery, or
            private staging fails.
        """
        state = self.credential_state
        result = state.kubernetes.command(
            "config",
            "view",
            "--raw",
            "--minify",
            "--flatten",
            "-o",
            "json",
        )
        if result.returncode:
            raise KubernetesError(error_class(result))
        configuration = _strict_object(
            result.stdout,
            "pod_kubeconfig_export_invalid",
        )
        exec_command = _exec_command(configuration)
        if exec_command and not _exec_plugin_available(state, exec_command):
            raise KubernetesError("pod_kubeconfig_exec_plugin_unavailable")
        if not hasattr(state.kubernetes, "identity"):
            raise KubernetesError("pod_caller_identity_unverified")
        state.caller_identity = state.kubernetes.identity
        state.kubeconfig_exec_plugin = bool(exec_command)
        state.kubeconfig_file = write_pod_file(
            state,
            self.action.kubeconfig_path,
            result.stdout.encode("utf-8"),
        )
        return {
            "action": APPLIED,
            "verified": True,
            "path": str(self.action.kubeconfig_path),
            "caller_identity": state.caller_identity,
            "exec_plugin": state.kubeconfig_exec_plugin,
        }

    def cleanup(self) -> JSONObject:
        """Remove only the exact private kubeconfig created by this callback.

        :returns: Applied removal or an idempotent no-op.
        :raises KubernetesError: If an existing file no longer matches its handle.
        """  # noqa: DOC502 - remove_pod_file protects exact content identity.
        state = self.credential_state
        if not hasattr(state, "kubeconfig_file"):
            return {"action": NO_OP, "verified": True}
        path = state.kubeconfig_file.path
        if path in state.retained_pod_files:
            return {
                "action": NO_OP,
                "verified": True,
                "preserved_private_file": path,
            }
        removed = remove_pod_file(state, state.kubeconfig_file)
        del state.kubeconfig_file
        return {
            "action": APPLIED if removed else NO_OP,
            "verified": True,
            "removed_private_file": path,
        }


class VerifyWorkloadCredential(PodCredentialCallback):
    """Read back Pod-native identity and required native operations."""

    def run(self) -> JSONObject:
        """Require caller identity parity and every declared native permission.

        :returns: Native identity and ordered positive permission evidence.
        :raises KubernetesError: If credential staging is absent, native identity
            differs, a required operation is denied, or ``oc`` fails.
        """
        state = self.credential_state
        if not hasattr(state, "kubeconfig_file"):
            raise KubernetesError("pod_kubeconfig_not_staged")
        kubeconfig = state.kubeconfig_file.path
        identity_result = _pod_command(
            state,
            "--kubeconfig",
            kubeconfig,
            "auth",
            "whoami",
            "-o",
            "json",
        )
        if identity_result.returncode:
            failure = (
                "pod_kubeconfig_exec_plugin_failed"
                if state.kubeconfig_exec_plugin
                else "pod_workload_identity_failed"
            )
            raise KubernetesError(failure)
        state.workload_identity = _workload_identity(
            _strict_object(identity_result.stdout, "pod_workload_identity_invalid")
        )
        if state.workload_identity != state.caller_identity:
            raise KubernetesError("pod_workload_identity_mismatch")
        state.credential_permissions.clear()
        for permission in self.action.required_permissions:
            self._verify_permission(state, kubeconfig, permission)
            state.credential_permissions.append(permission.as_dict())
        evidence: JSONObject = {
            "action": VERIFIED,
            "verified": True,
            "identity": state.workload_identity,
            "permissions": list(state.credential_permissions),
        }
        state.readback["workload_credential"] = evidence
        return evidence

    @staticmethod
    def _verify_permission(
        state: PodCredentialState,
        kubeconfig: str,
        permission: NativePermission,
    ) -> None:
        """Read one exact native permission with the staged workload identity.

        :param state: Shared Pod credential state.
        :param kubeconfig: Exact private file path inside the prepared Pod.
        :param permission: Required native operation selected by the consumer.
        :raises KubernetesError: If the permission is denied or native execution fails.
        """
        result = _pod_command(
            state,
            "--kubeconfig",
            kubeconfig,
            *permission.arguments(),
        )
        if result.returncode == 0:
            return
        if result.returncode == 1:
            scope = "*" if permission.all_namespaces else permission.namespace
            raise KubernetesError(
                "pod_workload_permission_denied:"
                f"{permission.verb}:{permission.resource}:{scope}"
            )
        raise KubernetesError("pod_workload_permission_check_failed")


class ResolvePullSecret(PodCredentialCallback):
    """Read one existing pull secret and stage its registry bytes privately."""

    def run(self) -> JSONObject:
        """Stage exact registry configuration bytes without reporting content.

        :returns: Existing Secret reference and private Pod path evidence.
        :raises KubernetesError: If the Secret/key is absent, malformed, or cannot
            be staged privately.
        """
        state = self.credential_state
        selected = self.action.pull_secret
        secret = state.kubernetes.read("Secret", selected.name, selected.namespace)
        if secret is None:
            raise KubernetesError("pull_secret_missing")
        data = secret.get("data")
        encoded = data.get(selected.key) if isinstance(data, Mapping) else None
        if not isinstance(encoded, str) or not encoded:
            raise KubernetesError("pull_secret_key_missing")
        state.pull_secret_file = write_pod_file(
            state,
            self.action.pull_secret_path,
            _registry_bytes(encoded),
        )
        return {
            "action": APPLIED,
            "verified": True,
            "source": selected.as_dict(),
            "path": str(self.action.pull_secret_path),
        }

    def cleanup(self) -> JSONObject:
        """Remove only the exact registry file created by this callback.

        :returns: Applied removal or an idempotent no-op.
        :raises KubernetesError: If an existing file no longer matches its handle.
        """  # noqa: DOC502 - remove_pod_file protects exact content identity.
        state = self.credential_state
        if not hasattr(state, "pull_secret_file"):
            return {"action": NO_OP, "verified": True}
        path = state.pull_secret_file.path
        if path in state.retained_pod_files:
            return {
                "action": NO_OP,
                "verified": True,
                "preserved_private_file": path,
            }
        removed = remove_pod_file(state, state.pull_secret_file)
        del state.pull_secret_file
        return {
            "action": APPLIED if removed else NO_OP,
            "verified": True,
            "removed_private_file": path,
        }
