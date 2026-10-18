"""Atomic callbacks for safe in-cluster artifact storage delivery.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.artifact_storage_models import (
    ArtifactStorageAction,
    ArtifactStorageState,
)
from lib.python.core.kubernetes.artifact_storage_runtime import (
    check_destination,
    copy_artifact,
    normalized_digest,
    observe_artifact,
    remove_artifact,
    remove_directories,
)
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.tool_pod_models import ArtifactHandle
from lib.python.core.kubernetes.tool_pod_resources import TOOL_POD_SCRIPT_ROLE
from lib.python.core.kubernetes.tool_pod_runtime import (
    ToolPodK8SCallback,
    _resource,
    retain_tool_pod,
)
from lib.python.sys.status import APPLIED, NO_OP, VERIFIED


def retain_verified_source(
    state: ArtifactStorageState,
    artifact: ArtifactHandle,
) -> None:
    """Retain one verified source and every run-owned resource backing it.

    :param state: Shared storage state containing the exact prepared Pod.
    :param artifact: Producer output after checksum and size verification.
    :raises KubernetesError: If Pod, digest, size, or path identity is invalid.
    """
    if artifact.pod != state.pod:
        raise KubernetesError("artifact_storage_source_pod_mismatch")
    normalized_digest(artifact.sha256)
    if artifact.size < 0 or not artifact.path.startswith("/"):
        raise KubernetesError("artifact_storage_source_identity_invalid")
    state.source_artifact = artifact
    retain_tool_pod(state)


class ArtifactStorageCallback(ToolPodK8SCallback[ArtifactStorageAction]):
    """Expose the exact artifact storage state to concrete callbacks."""

    @property
    def artifact_state(self) -> ArtifactStorageState:
        """Return the state required by artifact storage composition.

        :returns: Concrete shared storage state.
        :raises TypeError: If the workflow supplied another state type.
        """
        state = self.tool_state
        if not isinstance(state, ArtifactStorageState):
            raise TypeError("artifact_storage_state_required")
        return state


class CheckDestinationStorage(ArtifactStorageCallback):
    """Verify one caller-selected mount without touching caller files."""

    def run(self) -> JSONObject:
        """Confirm the mount, exact output absence, and unique probe lifecycle.

        :returns: Safe protocol, access-route, capacity, and path evidence.
        :raises KubernetesError: If namespace, mount identity, write/read/delete,
            or output-absence checks fail.
        """
        state, destination = self.artifact_state, self.action.destination
        if state.pod.namespace != destination.namespace:
            raise KubernetesError("artifact_storage_namespace_mismatch")
        handle, created = check_destination(state, destination)
        state.storage_handle = handle
        state.readback["destination_storage"] = handle.as_dict()
        return {
            "action": VERIFIED,
            "verified": True,
            "run_id": state.run_scope.run_id,
            "storage": handle.as_dict(),
            "created_directories": list(created),
        }

    def cleanup(self) -> JSONObject:
        """Remove only empty output-parent directories created by this callback.

        :returns: Exact removed paths or preservation evidence.
        :raises KubernetesError: If an owned directory is nonempty or remains.
        """  # noqa: DOC502 - exact cleanup delegates to the bounded Pod operation.
        state = self.artifact_state
        preserve_source = (
            hasattr(state, "source_artifact")
            and state.source_artifact.path == self.action.destination.artifact_path
        )
        if state.destination_verified or preserve_source:
            return {
                "action": NO_OP,
                "verified": True,
                "preserved_directories": list(state.created_destination_directories),
            }
        removed = remove_directories(
            state,
            state.created_destination_directories,
        )
        state.created_destination_directories.clear()
        return {
            "action": APPLIED if removed else NO_OP,
            "verified": True,
            "removed_directories": list(removed),
        }


class CopyArtifact(ArtifactStorageCallback):
    """Copy scratch output once or bind an artifact built at its final path."""

    def run(self) -> JSONObject:
        """Produce one delivery candidate without overwriting an existing path.

        :returns: Applied copy or direct-output no-op evidence.
        :raises KubernetesError: If storage was not checked, source identity differs,
            direct output has the wrong path, or an exclusive copy fails.
        """
        state, destination = self.artifact_state, self.action.destination
        if (
            not hasattr(state, "storage_handle")
            or state.storage_handle.destination != destination
        ):
            raise KubernetesError("artifact_storage_check_required")
        if not hasattr(state, "source_artifact"):
            raise KubernetesError("artifact_storage_source_unverified")
        source = state.source_artifact
        retain_verified_source(state, source)
        if destination.build_output == "destination":
            if source.path != destination.artifact_path:
                raise KubernetesError("artifact_storage_direct_path_mismatch")
            candidate = source
            action = NO_OP
        else:
            candidate = copy_artifact(state, source, destination.artifact_path)
            action = APPLIED
        state.delivery_candidate = candidate
        return {
            "action": action,
            "verified": False,
            "run_id": state.run_scope.run_id,
            "source": source.as_dict(),
            "candidate": candidate.as_dict(),
        }

    def cleanup(self) -> JSONObject:
        """Delete only this callback's exact unverified copied destination.

        :returns: Exact removal evidence or a no-op when no owned path is pending.
        :raises KubernetesError: If exact-path cleanup cannot verify absence.
        """  # noqa: DOC502 - exact cleanup delegates to the bounded Pod operation.
        state = self.artifact_state
        if not state.destination_created or state.destination_verified:
            return {
                "action": NO_OP,
                "verified": True,
                "preserved_source": hasattr(state, "source_artifact"),
            }
        path = state.copied_destination
        remove_artifact(state, path)
        return {
            "action": APPLIED,
            "verified": True,
            "removed_unverified_path": path,
            "preserved_source": True,
        }


class VerifyStoredArtifact(ArtifactStorageCallback):
    """Read back final bytes and release source retention only after equality."""

    def run(self) -> JSONObject:
        """Compare source and final identities using a separate Pod read-back.

        :returns: Final artifact identity and resource-retention evidence.
        :raises KubernetesError: If source/candidate is absent or read-back differs.
        """
        state, destination = self.artifact_state, self.action.destination
        if (
            not hasattr(state, "storage_handle")
            or state.storage_handle.destination != destination
        ):
            raise KubernetesError("artifact_storage_check_required")
        if not hasattr(state, "source_artifact"):
            raise KubernetesError("artifact_storage_source_unverified")
        source = state.source_artifact
        if destination.build_output == "destination":
            if source.path != destination.artifact_path:
                raise KubernetesError("artifact_storage_direct_path_mismatch")
        elif not hasattr(state, "delivery_candidate"):
            raise KubernetesError("artifact_storage_delivery_candidate_missing")
        observed = observe_artifact(state, destination.artifact_path)
        if (
            normalized_digest(observed.sha256) != normalized_digest(source.sha256)
            or observed.size != source.size
        ):
            raise KubernetesError("artifact_storage_readback_mismatch")
        state.stored_artifact = observed
        state.destination_verified = True
        released = self._release_source_retention()
        state.readback["stored_artifact"] = observed.as_dict()
        state.readback["retained_resources"] = [
            item.as_dict()
            for item in sorted(
                state.retained_resources,
                key=lambda item: (item.namespace, item.kind, item.name),
            )
        ]
        return {
            "action": VERIFIED,
            "verified": True,
            "run_id": state.run_scope.run_id,
            "artifact": observed.as_dict(),
            "retention": (
                "pod" if destination.is_temporary else "caller_selected_volume"
            ),
            "released_resources": released,
            "retained_resources": state.readback["retained_resources"],
        }

    def _release_source_retention(self) -> list[JSONObject]:
        """Release only tool Pod resources after pod-independent verification.

        :returns: Resource identities removed from the retention set.
        """
        state = self.artifact_state
        if self.action.destination.is_temporary:
            return []
        scope = state.run_scope
        candidates = {
            _resource("Pod", state.pod.name, state.pod.namespace),
            _resource(
                "ConfigMap",
                scope.name(TOOL_POD_SCRIPT_ROLE),
                scope.namespace,
            ),
        }
        if scope.owns_namespace:
            candidates.add(_resource("Namespace", scope.namespace, ""))
        released = sorted(
            state.retained_resources.intersection(candidates),
            key=lambda item: (item.namespace, item.kind, item.name),
        )
        state.retained_resources.difference_update(candidates)
        return [item.as_dict() for item in released]
