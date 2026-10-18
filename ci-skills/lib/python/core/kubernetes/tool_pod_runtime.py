"""Runtime helpers shared by reusable Kubernetes tool Pod callbacks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Final

from lib.python.core.abstract.skill_action import ActionT_co
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.kubernetes.kubernetes import K8SCallback, KubernetesError
from lib.python.core.kubernetes.run_scope import (
    ResourceIdentity,
    resource_identity,
)
from lib.python.core.kubernetes.tool_pod_models import (
    PodHandle,
    ToolPodState,
)
from lib.python.core.kubernetes.tool_pod_resources import (
    SPEC_FINGERPRINT_ANNOTATION,
    SPEC_FINGERPRINT_LABEL,
    TOOL_POD_CONTAINER,
    TOOL_POD_SCRIPT_ROLE,
    ToolPodResourceSpec,
)
from lib.python.core.runtime import error_class
from lib.python.core.tooling import Dependency

SHELL: Final = "/bin/sh"
OUTPUT_LIMIT: Final = 1024


class ToolPodK8SCallback(K8SCallback[ActionT_co]):
    """Expose the exact tool Pod state through shared Kubernetes behavior."""

    @property
    def tool_state(self) -> ToolPodState:
        """Return the state instance bound by the workflow executor.

        :returns: Concrete shared tool Pod state.
        :raises TypeError: If the workflow supplied another delivery state.
        """
        state = self.state
        if not isinstance(state, ToolPodState):
            raise TypeError("tool_pod_state_required")
        return state


def _metadata(document: JSONObject) -> Mapping[str, JSONValue]:
    """Require provider metadata needed for exact identity checks.

    :param document: Native Kubernetes object to inspect.
    :returns: Object metadata mapping.
    :raises KubernetesError: If metadata is absent or malformed.
    """
    metadata = document.get("metadata")
    if not isinstance(metadata, Mapping):
        raise KubernetesError("tool_pod_metadata_invalid")
    return metadata


def _pod_handle(document: JSONObject, expected: ToolPodResourceSpec) -> PodHandle:
    """Read the exact Pod/container identity and validate its resource shape.

    :param document: Native Pod read-back.
    :param expected: Resource shape and fingerprint expected by the action.
    :returns: Exact Pod and selected container identity.
    :raises KubernetesError: If identity, fingerprint, image, container, or restart
        evidence is absent or differs from the expected resource.
    """
    metadata = _metadata(document)
    labels = metadata.get("labels")
    annotations = metadata.get("annotations")
    if (
        not isinstance(labels, Mapping)
        or labels.get(SPEC_FINGERPRINT_LABEL) != expected.fingerprint_label
        or not isinstance(annotations, Mapping)
        or annotations.get(SPEC_FINGERPRINT_ANNOTATION) != expected.fingerprint
    ):
        raise KubernetesError("tool_pod_spec_fingerprint_mismatch")
    name, namespace, uid = (
        metadata.get("name"),
        metadata.get("namespace"),
        metadata.get("uid"),
    )
    spec = document.get("spec")
    containers = spec.get("containers") if isinstance(spec, Mapping) else None
    selected = (
        [
            item
            for item in containers
            if isinstance(item, Mapping) and item.get("name") == TOOL_POD_CONTAINER
        ]
        if isinstance(containers, list)
        else []
    )
    status = document.get("status")
    statuses = (
        status.get("containerStatuses", []) if isinstance(status, Mapping) else []
    )
    observed = (
        [
            item
            for item in statuses
            if isinstance(item, Mapping) and item.get("name") == TOOL_POD_CONTAINER
        ]
        if isinstance(statuses, list)
        else []
    )
    if (
        not all(isinstance(value, str) and value for value in (name, namespace, uid))
        or len(selected) != 1
        or selected[0].get("image") != expected.image
        or len(observed) != 1
    ):
        raise KubernetesError("tool_pod_identity_invalid")
    restart_count = observed[0].get("restartCount")
    if not isinstance(restart_count, int) or isinstance(restart_count, bool):
        raise KubernetesError("tool_pod_restart_count_invalid")
    return PodHandle(
        namespace=namespace,
        name=name,
        uid=uid,
        container=TOOL_POD_CONTAINER,
        image=expected.image,
        restart_count=restart_count,
    )


def _shell(state: ToolPodState, script: str) -> str:
    """Prefix shared installation paths without changing the Pod process.

    :param state: Tool Pod state containing ordered command search paths.
    :param script: Shell operation to execute.
    :returns: Operation with a PATH prefix, or the unchanged operation when empty.
    """
    if not state.search_paths:
        return script
    additions = ":".join(shlex.quote(path) for path in state.search_paths)
    return f"PATH={additions}:$PATH; export PATH; {script}"


def _run_shell(state: ToolPodState, script: str, *arguments: str) -> str:
    """Run one bounded shell operation in the exact prepared container.

    :param state: Tool Pod state containing the exact ready Pod handle.
    :param script: Shell operation to execute with the prepared PATH.
    :param *arguments: Literal positional arguments supplied after the shell label.
    :returns: Native standard output from the successful operation.
    :raises KubernetesError: If the operation returns a nonzero status.
    """
    pod = state.pod
    result = state.kubernetes.pod_exec(
        pod.namespace,
        pod.name,
        pod.container,
        SHELL,
        "-ceu",
        _shell(state, script),
        "operation",
        *arguments,
    )
    if result.returncode:
        raise KubernetesError(error_class(result))
    return result.stdout


def _presence(state: ToolPodState, dependency: Dependency) -> bool:
    """Observe every declared command through the shared presence operation.

    :param state: Tool Pod state containing the exact ready Pod handle.
    :param dependency: Dependency whose declared commands must be present.
    :returns: Whether the complete shared presence operation succeeds.
    """
    pod = state.pod
    result = state.kubernetes.pod_exec(
        pod.namespace,
        pod.name,
        pod.container,
        SHELL,
        "-ceu",
        _shell(state, dependency.presence_operation()),
    )
    return result.returncode == 0


def _resource(kind: str, name: str, namespace: str) -> ResourceIdentity:
    """Construct one normalized retained identity.

    :param kind: Native Kubernetes resource kind.
    :param name: Exact resource name.
    :param namespace: Exact namespace, or empty text for cluster scope.
    :returns: Resource identity with a case-normalized kind.
    """
    return resource_identity(kind, name, namespace)


def retain_tool_pod(state: ToolPodState) -> None:
    """Retain the exact run-owned Pod resources backing native work.

    :param state: Shared state containing the prepared Pod and resolved run scope.
    """
    scope = state.run_scope
    state.retained_resources.add(_resource("Pod", state.pod.name, state.pod.namespace))
    state.retained_resources.add(
        _resource(
            "ConfigMap",
            scope.name(TOOL_POD_SCRIPT_ROLE),
            scope.namespace,
        )
    )
    if scope.owns_namespace:
        state.retained_resources.add(_resource("Namespace", scope.namespace, ""))
