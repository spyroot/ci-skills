"""Dependency operations executed inside one reusable Kubernetes tool Pod.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Final, Optional

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import K8SCallback, KubernetesError
from lib.python.core.kubernetes.tool_pod_models import (
    ArtifactHandle,
    PrepareToolPodAction,
    ToolDependencyAction,
    ToolPodState,
    dependency_key,
)
from lib.python.core.kubernetes.tool_pod_runtime import (
    OUTPUT_LIMIT,
    ToolPodK8SCallback,
    _presence,
    _run_shell,
)
from lib.python.core.runtime import sanitize
from lib.python.core.tooling import Dependency
from lib.python.core.tooling.packages import (
    PackageManager,
    package_install_operation,
    package_manager_probe,
)
from lib.python.sys.status import APPLIED, NO_OP, PASS, VERIFIED

PACKAGE_WRITE_ROOTS: Final = {
    PackageManager.APT.value: (
        "/var/lib/dpkg",
        "/var/lib/apt/lists",
        "/var/cache/apt/archives",
    ),
    PackageManager.DNF.value: ("/var/lib/rpm", "/var/cache/dnf"),
    PackageManager.YUM.value: ("/var/lib/rpm", "/var/cache/yum"),
}
PREFLIGHT_SCRIPT: Final = (
    "command_count=$1; shift; index=0; "
    'while [ "$index" -lt "$command_count" ]; do '
    "command_name=$1; shift; "
    'if command -v "$command_name" >/dev/null 2>&1; then status=present; '
    "else status=missing; fi; "
    'printf \'command\\t%s\\t%s\\n\' "$index" "$status"; '
    "index=$((index + 1)); done; "
    'index=0; for requested_path in "$@"; do path=$requested_path; '
    'while [ ! -e "$path" ]; do parent=${path%/*}; '
    '[ -n "$parent" ] || parent=/; '
    '[ "$parent" != "$path" ] || break; path=$parent; done; '
    'if [ -w "$path" ]; then observed=$path; else observed=-; fi; '
    'printf \'path\\t%s\\t%s\\n\' "$index" "$observed"; '
    "index=$((index + 1)); done"
)


def _seed_search_paths(
    state: ToolPodState,
    dependencies: Sequence[Dependency],
) -> None:
    """Retain declared installation paths before any presence probe.

    :param state: Tool Pod state receiving ordered search paths.
    :param dependencies: Dependencies in declaration order.
    :returns: None after adding each unique declared path once.
    """
    for dependency in dependencies:
        if dependency.linux is None:
            continue
        for path in dependency.linux.installation_paths():
            if path not in state.search_paths:
                state.search_paths.append(path)


def _package_manager(state: ToolPodState) -> str:
    """Observe and validate the container's selected package-manager route.

    :param state: Tool Pod state containing the exact ready Pod handle.
    :returns: One shared package-manager value, including ``unavailable``.
    :raises KubernetesError: If the probe fails or returns an unknown value.
    """
    manager = _run_shell(state, package_manager_probe()).strip()
    try:
        PackageManager(manager)
    except ValueError as exc:
        raise KubernetesError("dependency_package_manager_probe_invalid") from exc
    return manager


def _execution_environment(state: ToolPodState, manager: str) -> JSONObject:
    """Capture bounded runtime identity used by later dependency evidence.

    :param state: Tool Pod state containing the exact ready Pod handle.
    :param manager: Validated package-manager value.
    :returns: Runtime, Pod, and effective search-path evidence.
    :raises KubernetesError: If the runtime observation command fails.
    """  # noqa: DOC502 - _run_shell propagates the declared failure.
    runtime = _run_shell(
        state,
        'printf \'uid=%s\\ngid=%s\\n\' "$(id -u)" "$(id -g)"; '
        "uname -srm; "
        "if [ -r /etc/os-release ]; then "
        ". /etc/os-release; printf 'os=%s\\nos_version=%s\\n' "
        '"${ID:-unknown}" "${VERSION_ID:-unknown}"; '
        "else printf 'os=unknown\\nos_version=unknown\\n'; fi",
    )
    return {
        "package_manager": manager,
        "runtime": sanitize(runtime, OUTPUT_LIMIT),
        "pod_uid": state.pod.uid,
        "restart_count": state.pod.restart_count,
        "search_paths": list(state.search_paths),
    }


def _preflight(
    state: ToolPodState,
    commands: Sequence[str],
    paths: Sequence[str],
) -> list[JSONObject]:
    """Check ordered prerequisites and writable ancestors in one Pod exec.

    :param state: Tool Pod state containing the exact ready Pod handle.
    :param commands: Executable names required by one missing dependency.
    :param paths: Installation and package paths to preflight in order.
    :returns: Requested paths paired with their observed writable ancestors.
    :raises KubernetesError: If execution fails, indexed output is malformed, a
        command is absent, or a path has no writable ancestor.
    """  # noqa: DOC502 - _run_shell propagates execution failures.
    output = _run_shell(
        state,
        PREFLIGHT_SCRIPT,
        str(len(commands)),
        *commands,
        *paths,
    )
    observed_commands: dict[int, str] = {}
    observed_paths: dict[int, str] = {}
    try:
        for line in output.splitlines():
            kind, raw_index, observed = line.split("\t", 2)
            index = int(raw_index)
            selected = (
                observed_commands
                if kind == "command"
                else observed_paths
                if kind == "path"
                else None
            )
            if selected is None or index in selected:
                raise KubernetesError("dependency_preflight_output_invalid")
            selected[index] = observed
    except (TypeError, ValueError) as exc:
        raise KubernetesError("dependency_preflight_output_invalid") from exc
    if set(observed_commands) != set(range(len(commands))) or set(
        observed_paths
    ) != set(range(len(paths))):
        raise KubernetesError("dependency_preflight_output_invalid")
    for index, command in enumerate(commands):
        if observed_commands[index] != "present":
            raise KubernetesError(
                f"dependency_prerequisite_missing:{sanitize(command, 120)}"
            )
    writable: list[JSONObject] = []
    for index, path in enumerate(paths):
        observed = observed_paths[index]
        if observed == "-":
            raise KubernetesError(
                f"dependency_write_permission_missing:{sanitize(path, 240)}"
            )
        writable.append({"requested": path, "observed": sanitize(observed, 240)})
    return writable


def _dependency_route(
    state: ToolPodState,
    dependency: Dependency,
    index: int,
    manager: str,
) -> tuple[str, str, list[JSONObject]]:
    """Validate the missing dependency's declared installation route.

    :param state: Tool Pod state containing the exact ready Pod handle.
    :param dependency: Missing dependency whose route is selected.
    :param index: Stable declaration index used for artifact staging.
    :param manager: Validated container package-manager value.
    :returns: Route name, selected manager, and writable-path evidence.
    :raises KubernetesError: If prerequisites, package mapping, write access, or
        the installation route is unusable.
    """
    if dependency.linux is not None:
        install = dependency.linux
        commands = list(install.prerequisite_commands())
        if install.artifact_backed:
            commands.extend(("rm", "wc"))
        paths = list(install.installation_roots())
        if install.artifact_backed:
            paths.append(str(PurePosixPath(install.artifact_path(index)).parent))
        selected_manager = ""
        if install.method.value == "deb":
            selected_manager = PackageManager.APT.value
            paths.extend(PACKAGE_WRITE_ROOTS[PackageManager.APT.value])
        writable = _preflight(state, commands, paths)
        route = "artifact" if install.artifact_backed else "linux"
        return route, selected_manager, writable
    if dependency.apt or dependency.yum:
        if manager == PackageManager.UNAVAILABLE.value:
            raise KubernetesError("dependency_package_manager_missing")
        if (manager == PackageManager.APT.value and not dependency.apt) or (
            manager in (PackageManager.DNF.value, PackageManager.YUM.value)
            and not dependency.yum
        ):
            raise KubernetesError("dependency_packages_missing_for_manager")
        return "package", manager, _preflight(state, (), PACKAGE_WRITE_ROOTS[manager])
    raise KubernetesError("dependency_install_route_missing")


def _dependency_observation(
    state: ToolPodState,
    dependency: Dependency,
    index: int,
    manager: str,
) -> JSONObject:
    """Observe one dependency once and assemble its route evidence.

    :param state: Tool Pod state containing the exact ready Pod handle.
    :param dependency: Dependency at the selected declaration index.
    :param index: Stable declaration index.
    :param manager: Validated container package-manager value.
    :returns: Presence, route, Pod identity, and write-preflight evidence.
    :raises KubernetesError: If a missing dependency has no usable route.
    """  # noqa: DOC502 - _dependency_route propagates the declared failure.
    present = _presence(state, dependency)
    route, selected_manager, writable = (
        ("present", "", [])
        if present
        else _dependency_route(state, dependency, index, manager)
    )
    record: JSONObject = {
        "index": index,
        "name": dependency.name,
        "commands": list(dependency.commands),
        "present": present,
        "route": route,
        "pod_uid": state.pod.uid,
        "restart_count": state.pod.restart_count,
        "writable": writable,
    }
    if selected_manager:
        record["package_manager"] = selected_manager
    return record


class ReadToolState(ToolPodK8SCallback[PrepareToolPodAction]):
    """Observe command presence and route prerequisites without installing."""

    def run(self) -> JSONObject:
        """Record present tools and validate prerequisites for missing routes.

        :returns: Runtime environment and per-index dependency observations.
        :raises KubernetesError: If package-manager output, a prerequisite, a
            write destination, or the selected installation route is unusable.
        """  # noqa: DOC502 - focused helpers propagate the declared failure.
        state, dependencies = self.tool_state, self.action.spec.dependencies
        _seed_search_paths(state, dependencies)
        manager = _package_manager(state)
        environment = _execution_environment(state, manager)
        state.execution_environment = environment
        observations: list[JSONObject] = []
        for index, dependency in enumerate(dependencies):
            key = dependency_key(index)
            record = _dependency_observation(state, dependency, index, manager)
            state.tool_observations[key] = record
            observations.append(record)
        return {
            "action": VERIFIED,
            "verified": True,
            "environment": environment,
            "tools": observations,
        }


class DownloadArtifact(ToolPodK8SCallback[ToolDependencyAction]):
    """Download and checksum one missing artifact-backed dependency."""

    def __init__(self, action: ToolDependencyAction):
        """Retain one dependency and initialize no staged artifact.

        :param action: Stable dependency index and confirmed preparation action.
        """
        super().__init__(action)
        self.staging_path: Optional[str] = None

    def run(self) -> JSONObject:
        """Transfer one artifact and retain its concrete Pod path and identity.

        :returns: Verified artifact handle, or no-op evidence for another route.
        :raises KubernetesError: If transfer, checksum, or byte-size proof fails.
        """
        dependency = self.action.dependency
        observed = self.tool_state.tool_observations[self.action.key]
        if observed.get("present") is True:
            return {
                "action": NO_OP,
                "verified": True,
                "index": self.action.index,
                "name": dependency.name,
            }
        route = dependency.linux
        if route is None or not route.artifact_backed:
            return {
                "action": NO_OP,
                "verified": True,
                "index": self.action.index,
                "name": dependency.name,
            }
        path = route.artifact_path(self.action.index)
        self.staging_path = path
        _run_shell(self.tool_state, route.download_operation(path))
        size_text = _run_shell(
            self.tool_state,
            f"wc -c < {shlex.quote(path)}",
        ).strip()
        try:
            size = int(size_text)
        except ValueError as exc:
            raise KubernetesError("artifact_size_invalid") from exc
        if route.sha256 is None:
            raise KubernetesError("artifact_checksum_missing")
        artifact = ArtifactHandle(self.tool_state.pod, path, route.sha256, size)
        self.tool_state.artifacts[self.action.key] = artifact
        return {
            "action": APPLIED,
            "verified": True,
            "index": self.action.index,
            "name": dependency.name,
            "artifact": artifact.as_dict(),
        }

    def cleanup(self) -> JSONObject:
        """Remove and independently verify this callback's staging path.

        :returns: Verified path-absence evidence, or an empty mapping when unused.
        """
        if self.staging_path is None:
            return {}
        path = self.staging_path
        _run_shell(
            self.tool_state,
            f"rm -f {shlex.quote(path)}; test ! -e {shlex.quote(path)}",
        )
        self.tool_state.artifacts.pop(self.action.key, None)
        evidence: JSONObject = {
            "kind": "PodArtifact",
            "index": self.action.index,
            "path": path,
            "status": PASS,
            "absent": True,
        }
        self.tool_state.cleanup_resources.append(evidence)
        return evidence


class InstallArtifact(ToolPodK8SCallback[ToolDependencyAction]):
    """Install one missing dependency without owning its transfer."""

    def run(self) -> JSONObject:
        """Apply the selected installation operation and read commands back.

        :returns: Installed command evidence, or a no-op for an existing tool.
        :raises KubernetesError: If no route exists or command read-back fails.
        """
        dependency = self.action.dependency
        observed = self.tool_state.tool_observations[self.action.key]
        if observed.get("present") is True:
            return {
                "action": NO_OP,
                "verified": True,
                "index": self.action.index,
                "name": dependency.name,
            }
        route = dependency.linux
        if route is not None:
            artifact = self.tool_state.artifacts.get(self.action.key)
            operation = route.install_operation(artifact.path if artifact else "")
            _run_shell(self.tool_state, operation)
        elif dependency.apt or dependency.yum:
            _run_shell(
                self.tool_state,
                package_install_operation(dependency.apt, dependency.yum),
            )
        else:
            raise KubernetesError("dependency_install_route_missing")
        if not _presence(self.tool_state, dependency):
            raise KubernetesError("dependency_install_readback_missing")
        updated = dict(observed)
        updated["present"] = True
        updated["installed"] = True
        self.tool_state.tool_observations[self.action.key] = updated
        return {
            "action": APPLIED,
            "verified": True,
            "index": self.action.index,
            "name": dependency.name,
            "commands": list(dependency.commands),
        }


def tool_callbacks(action: PrepareToolPodAction) -> Sequence[K8SCallback[SkillAction]]:
    """Expand declared dependency operations in deterministic input order.

    :param action: Confirmed preparation action shared by every callback.
    :returns: Optional transfer callbacks followed by each install callback.
    """
    callbacks: list[K8SCallback[SkillAction]] = []
    for index, dependency in enumerate(action.spec.dependencies):
        selected = ToolDependencyAction(action, index)
        if dependency.linux is not None and dependency.linux.artifact_backed:
            callbacks.append(DownloadArtifact(selected))
        callbacks.append(InstallArtifact(selected))
    return tuple(callbacks)
