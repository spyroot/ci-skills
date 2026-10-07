"""Select and read one existing Pod on the explicitly targeted Kubernetes node."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .access import kubectl_argv, kubernetes_env
from .runtime import CommandResult, error_class, run_command, run_command_tail
from .target import JournalPodRoute, NodePodRoute, Target, TargetError


class NodePodError(RuntimeError):
    """The declared existing-Pod route could not be read unambiguously."""


@dataclass(frozen=True)
class NodePodReader:
    """A verified Pod identity and a non-TTY kubectl exec transport."""

    target: Target
    route: NodePodRoute
    name: str
    uid: str

    def _assert_current(self) -> None:
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
            pod = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise NodePodError("pod_readback:invalid_json") from exc
        if not isinstance(pod, dict):
            raise NodePodError("pod_readback:invalid_response")
        metadata = pod.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("uid") != self.uid:
            raise NodePodError("pod_readback:identity_changed")
        _selected_pod({"items": [pod]}, self.target, self.route)

    def command(self, *args: str) -> list[str]:
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
        self._assert_current()
        return run_command_tail(
            self.command(*args),
            timeout=timeout,
            max_bytes=max_bytes,
            max_lines=max_lines,
            env=kubernetes_env(self.target),
        )

    def evidence(self) -> dict[str, str]:
        return {
            "node": self.target.kubernetes.node_diagnostics.node,
            "namespace": self.route.namespace,
            "selector": self.route.selector,
            "pod": self.name,
            "pod_uid": self.uid,
            "container": self.route.container,
        }


def _selected_pod(value: Any, target: Target, route: NodePodRoute) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise NodePodError("pod_selection:invalid_response")
    items = value["items"]
    if len(items) != 1 or not isinstance(items[0], dict):
        raise NodePodError(
            "pod_selection:no_pod" if not items else "pod_selection:ambiguous_pods"
        )
    pod = items[0]
    metadata = pod.get("metadata")
    spec = pod.get("spec")
    status = pod.get("status")
    if not all(isinstance(item, dict) for item in (metadata, spec, status)):
        raise NodePodError("pod_selection:invalid_pod")
    containers = spec.get("containers")
    names = (
        [item.get("name") for item in containers if isinstance(item, dict)]
        if isinstance(containers, list)
        else []
    )
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
            if isinstance(item, dict) and item.get("name") == route.container
        ]
        if isinstance(container_statuses, list)
        else []
    )
    if (
        len(matching_statuses) != 1
        or not isinstance(matching_statuses[0].get("state"), dict)
        or not isinstance(matching_statuses[0]["state"].get("running"), dict)
    ):
        raise NodePodError("pod_selection:container_not_running")
    return pod


def _host_journal_mounted(pod: dict[str, Any], route: JournalPodRoute) -> bool:
    """Map a Pod path through a hostPath mount to the declared host directory."""
    spec = pod["spec"]
    containers = spec["containers"]
    selected = next(
        item for item in containers if item.get("name") == route.pod.container
    )
    mounts = selected.get("volumeMounts")
    volumes = spec.get("volumes")
    if not isinstance(mounts, list) or not isinstance(volumes, list):
        return False
    directory = PurePosixPath(route.directory)
    host_journal = PurePosixPath(route.host_path)
    for mount in mounts:
        if not isinstance(mount, dict) or not isinstance(mount.get("mountPath"), str):
            continue
        mount_path = PurePosixPath(mount["mountPath"])
        try:
            relative = directory.relative_to(mount_path)
        except ValueError:
            continue
        for volume in volumes:
            if not isinstance(volume, dict) or volume.get("name") != mount.get("name"):
                continue
            host = volume.get("hostPath")
            if (
                isinstance(host, dict)
                and isinstance(host.get("path"), str)
                and PurePosixPath(host["path"]) / relative == host_journal
            ):
                return True
    return False


def select_node_pod(
    target: Target, route: NodePodRoute, *, journal: JournalPodRoute | None = None
) -> NodePodReader:
    """Read back one Running Pod, node, container, and optional host mount."""
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
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise NodePodError("pod_selection:invalid_json") from exc
    pod = _selected_pod(value, target, route)
    if journal is not None and not _host_journal_mounted(pod, journal):
        raise NodePodError("journal:host_directory_not_mounted")
    metadata = pod["metadata"]
    return NodePodReader(target, route, metadata["name"], metadata["uid"])
