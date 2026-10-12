"""Select and read one existing Pod on the explicitly targeted Kubernetes node.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping, MutableSequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Optional

from lib.python.access.kubernetes import kubectl_argv, kubernetes_env
from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.runtime import (
    CommandResult,
    error_class,
    run_command,
    run_command_tail,
)
from lib.python.core.target import JournalPodRoute, NodePodRoute, Target, TargetError


class NodePodError(RuntimeError):
    """The declared existing-Pod route could not be read unambiguously."""


@dataclass(frozen=True)
class NodePodReader:
    """Recheck one selected Pod before noninteractive container commands.

    :param target: Bound Kubernetes target and node diagnostics selection.
    :param route: Existing Pod namespace, selector, and container route.
    :param name: Exact Pod name selected from the route.
    :param uid: Pod UID pinned for independent read-back before exec.

    .. attribute :: target
        :type: Target

        Bound Kubernetes target and node diagnostics selection.

    .. attribute :: route
        :type: NodePodRoute

        Existing Pod namespace, selector, and container route.

    .. attribute :: name
        :type: str

        Exact Pod name selected from the route.

    .. attribute :: uid
        :type: str

        Pod UID pinned for independent read-back before exec.
    """

    target: Target
    route: NodePodRoute
    name: str
    uid: str

    def _assert_current(self) -> None:
        """Read back the selected Pod before executing another command.

        :raises NodePodError: Pod read fails, response is invalid, UID changes, or
            node/container selection no longer matches.
        :raises TargetError: Bound Kubernetes source changes before read-back.
        :raises OSError: If the Pod read-back process cannot be started.
        """  # noqa: DOC503 - Bound credentials and native Pod reads can fail.
        result = run_command(
            kubectl_argv(
                self.target,
                "-n",
                self.route.namespace,
                "get",
                "pod",
                self.name,
                "-o",
                "json",
            ),
            env=kubernetes_env(self.target),
        )
        if result.returncode:
            raise NodePodError(f"pod_readback:{error_class(result)}")
        try:
            pod = response_decoder().decode(result.stdout)
        except (ValueError, RecursionError) as exc:
            raise NodePodError("pod_readback:invalid_json") from exc
        if not isinstance(pod, Mapping):
            raise NodePodError("pod_readback:invalid_response")
        metadata = pod.get("metadata")
        if not isinstance(metadata, Mapping) or metadata.get("uid") != self.uid:
            raise NodePodError("pod_readback:identity_changed")
        _selected_pod({"items": [pod]}, self.target, self.route)

    def command(self, *args: str) -> MutableSequence[str]:
        """Build a noninteractive exec command for the selected Pod container.

        :param *args: Command and arguments to run inside the container.
        :returns: Full ``kubectl`` argument vector with context, namespace, Pod,
            container, and supplied command.
        :raises TargetError: Bound kubeconfig changes before command construction.
        """  # noqa: DOC502 - kubectl_argv verifies bound kubeconfig sources.
        return kubectl_argv(
            self.target,
            "-n",
            self.route.namespace,
            "exec",
            f"pod/{self.name}",
            "-c",
            self.route.container,
            "--",
            *args,
        )

    def run(self, *args: str, timeout: int = 35) -> CommandResult:
        """Verify Pod identity and run a bounded command in its container.

        :param *args: Command and arguments to run inside the container.
        :param timeout: Maximum native command runtime in seconds.
        :returns: Command result with at most 1 MiB and 20,000 stdout lines retained.
        :raises NodePodError: The Pod identity or selected container changed.
        :raises TargetError: Bound Kubernetes source changes before execution.
        :raises OSError: If a native read or exec process cannot be started.
        """  # noqa: DOC502 - Pod verification and bounded native readers can fail.
        self._assert_current()
        return run_command_tail(
            self.command(*args),
            timeout=timeout,
            max_bytes=1_048_576,
            max_lines=20_000,
            env=kubernetes_env(self.target),
        )

    def run_tail(
        self, *args: str, timeout: int, max_bytes: int, max_lines: int
    ) -> CommandResult:
        """Verify Pod identity and run a command with caller-selected tail limits.

        :param *args: Command and arguments to run inside the container.
        :param timeout: Maximum native command runtime in seconds.
        :param max_bytes: Maximum stdout tail bytes retained.
        :param max_lines: Maximum stdout tail lines returned.
        :returns: Bounded command result from the selected container.
        :raises NodePodError: The Pod identity or selected container changed.
        :raises ValueError: A byte or line limit is nonpositive.
        :raises TargetError: Bound Kubernetes source changes before execution.
        :raises OSError: If a native read or exec process cannot be started.
        """  # noqa: DOC502 - Pod verification and bounded native readers can fail.
        self._assert_current()
        return run_command_tail(
            self.command(*args),
            timeout=timeout,
            max_bytes=max_bytes,
            max_lines=max_lines,
            env=kubernetes_env(self.target),
        )

    def evidence(self) -> Mapping[str, str]:
        """Identify the exact node, Pod, and container selected for reads.

        :returns: Node, namespace, selector, Pod name and UID, and container fields.
        :raises TargetError: If node diagnostics are absent from the bound target.
        """
        if self.target.kubernetes is None:
            raise TargetError("kubernetes_target_missing")
        selected = self.target.kubernetes.node_diagnostics
        if selected is None:
            raise TargetError("kubernetes.node_diagnostics is required")
        return {
            "node": selected.node,
            "namespace": self.route.namespace,
            "selector": self.route.selector,
            "pod": self.name,
            "pod_uid": self.uid,
            "container": self.route.container,
        }


def _selected_pod(value: JSONValue, target: Target, route: NodePodRoute) -> JSONObject:
    """Validate that a Pod list uniquely selects the intended running container.

    :param value: Decoded Kubernetes Pod List response.
    :param target: Target declaring the exact diagnostic node.
    :param route: Namespace, selector, and container expected from the target.
    :returns: Sole matching running Pod object.
    :raises NodePodError: The response is malformed, selection is absent or
        ambiguous, or Pod/container identity does not match.
    :raises TargetError: Node diagnostics are absent from the selected target.
    """
    items = value.get("items") if isinstance(value, Mapping) else None
    if not isinstance(items, list):
        raise NodePodError("pod_selection:invalid_response")
    if len(items) != 1 or not isinstance(items[0], Mapping):
        raise NodePodError(
            "pod_selection:no_pod" if not items else "pod_selection:ambiguous_pods"
        )
    pod = items[0]
    metadata = pod.get("metadata")
    spec = pod.get("spec")
    status = pod.get("status")
    if (
        not isinstance(metadata, Mapping)
        or not isinstance(spec, Mapping)
        or not isinstance(status, Mapping)
    ):
        raise NodePodError("pod_selection:invalid_pod")
    containers = spec.get("containers")
    names = (
        [item.get("name") for item in containers if isinstance(item, Mapping)]
        if isinstance(containers, list)
        else []
    )
    if target.kubernetes is None:
        raise TargetError("kubernetes_target_missing")
    selected = target.kubernetes.node_diagnostics
    if selected is None:
        raise TargetError("kubernetes.node_diagnostics is required")
    if (
        metadata.get("namespace") != route.namespace
        or not isinstance(metadata.get("name"), str)
        or not metadata["name"]
        or not isinstance(metadata.get("uid"), str)
        or not metadata["uid"]
        or spec.get("nodeName") != selected.node
        or status.get("phase") != "Running"
        or names.count(route.container) != 1
    ):
        raise NodePodError("pod_selection:wrong_pod_or_container")
    container_statuses = status.get("containerStatuses")
    matching_statuses = (
        [
            item
            for item in container_statuses
            if isinstance(item, Mapping) and item.get("name") == route.container
        ]
        if isinstance(container_statuses, list)
        else []
    )
    running_state = (
        matching_statuses[0].get("state") if len(matching_statuses) == 1 else None
    )
    if not isinstance(running_state, Mapping) or not isinstance(
        running_state.get("running"), Mapping
    ):
        raise NodePodError("pod_selection:container_not_running")
    return pod


def _host_journal_mounted(pod: JSONObject, route: JournalPodRoute) -> bool:
    """Check that the selected container maps its journal directory to the host.

    :param pod: Selected Pod object with its containers, mounts, and volumes.
    :param route: Journal route declaring the container directory and host path.
    :returns: Whether a selected mount resolves to the exact host path; malformed
        or absent mount evidence returns false.
    """
    try:
        spec = KubernetesObject(pod).object("spec")
        containers = [
            item
            for item in spec.objects("containers")
            if item.text("name") == route.pod.container
        ]
        if len(containers) != 1:
            return False
        mounts = containers[0].objects("volumeMounts")
        volumes = spec.objects("volumes")
        directory = PurePosixPath(route.directory)
        host_journal = PurePosixPath(route.host_path)
        for mount in mounts:
            mount_path = mount.text("mountPath")
            if not mount_path:
                continue
            try:
                relative = directory.relative_to(PurePosixPath(mount_path))
            except ValueError:
                continue
            for volume in volumes:
                if volume.text("name") != mount.text("name"):
                    continue
                host_path = volume.object("hostPath").text("path")
                if host_path and PurePosixPath(host_path) / relative == host_journal:
                    return True
    except TypeError:
        return False
    return False


def select_node_pod(
    target: Target, route: NodePodRoute, journal: Optional[JournalPodRoute] = None
) -> NodePodReader:
    """Select one existing running Pod on the declared node.

    :param target: Bound Kubernetes target with node diagnostics settings.
    :param route: Namespace, selector, and container route to select.
    :param journal: Optional route requiring an exact host journal mount.
    :returns: Reader bound to the selected Pod name and UID.
    :raises TargetError: Node diagnostics are absent or bound sources change.
    :raises OSError: If a native Pod list process cannot be started.
    :raises NodePodError: Listing fails, Pod selection is invalid or ambiguous,
        or the requested journal mount is absent.
    """  # noqa: DOC503 - Bound credentials and native Pod reads can fail.
    if target.kubernetes is None:
        raise TargetError("kubernetes_target_missing")
    selected = target.kubernetes.node_diagnostics
    if selected is None:
        raise TargetError("kubernetes.node_diagnostics is required")
    argv = kubectl_argv(
        target,
        "-n",
        route.namespace,
        "get",
        "pods",
        "-l",
        route.selector,
        "--field-selector",
        f"spec.nodeName={selected.node}",
        "-o",
        "json",
    )
    result = run_command(argv, env=kubernetes_env(target))
    if result.returncode:
        raise NodePodError(f"pod_selection:{error_class(result)}")
    try:
        value = response_decoder().decode(result.stdout)
    except (ValueError, RecursionError) as exc:
        raise NodePodError("pod_selection:invalid_json") from exc
    pod = _selected_pod(value, target, route)
    if journal is not None and not _host_journal_mounted(pod, journal):
        raise NodePodError("journal:host_directory_not_mounted")
    metadata = KubernetesObject(pod).metadata
    return NodePodReader(
        target, route, metadata.required_text("name"), metadata.required_text("uid")
    )
