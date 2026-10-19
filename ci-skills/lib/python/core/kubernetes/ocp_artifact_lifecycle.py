"""Verify and remove persisted OpenShift node-image artifacts in-cluster.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.json_types import JSONObject, copy_json_object
from lib.python.core.kubernetes.artifact_storage_runtime import (
    normalized_digest,
    observe_artifact,
)
from lib.python.core.kubernetes.kubernetes import K8SCallback, KubernetesError
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.kubernetes.ocp_artifact_run import (
    ARTIFACT_RECORD_ROLE,
    ArtifactLifetime,
    NodeArtifactRecord,
    OCPArtifactState,
    decode_artifact_record_resource,
)
from lib.python.core.kubernetes.ocp_iso_verify import inspect_verified_node_image
from lib.python.core.kubernetes.run_scope import (
    ResolveRunScopeAction,
    RunScope,
    resource_identity,
    run_owned,
)
from lib.python.core.kubernetes.tool_pod_models import PrepareToolPodAction
from lib.python.core.kubernetes.tool_pod_resources import TOOL_POD_SCRIPT_ROLE
from lib.python.sys.status import APPLIED, NO_OP, VERIFIED


@dataclass(frozen=True)
class ArtifactPodInputs:
    """Bind a resolved artifact to one existing or temporary CI12.0 Pod.

    :param scope: Exact scope to open before staging the Pod.
    :param preparation: Existing CI12.0 preparation action for that scope.
    :param reuses_build_pod: Whether the expected Pod is the original producer.

    .. attribute :: scope
        :type: RunScope

        Exact scope to open before staging the Pod.

    .. attribute :: preparation
        :type: PrepareToolPodAction

        Existing CI12.0 preparation action for that scope.

    .. attribute :: reuses_build_pod
        :type: bool

        Whether the expected Pod is the original producer.
    """

    scope: RunScope
    preparation: PrepareToolPodAction
    reuses_build_pod: bool


def verification_pod_inputs(
    record: NodeArtifactRecord,
    artifact_scope: RunScope,
    *,
    timeout: int,
) -> ArtifactPodInputs:
    """Select the existing temporary Pod or a fresh persistent-volume reader.

    :param record: Artifact record resolved from live labels.
    :param artifact_scope: Original live scope that contained the record.
    :param timeout: Positive CI12.0 native-operation timeout.
    :returns: Scope and preparation action for existing lifecycle callbacks.
    :raises ValueError: If record and live scope identities differ or timeout is invalid.
    """
    if (
        artifact_scope.run_id != record.run_id
        or artifact_scope.namespace != record.destination.namespace
    ):
        raise ValueError("node_artifact_scope_mismatch")
    if timeout <= 0:
        raise ValueError("node_artifact_reader_timeout_invalid")
    if record.lifetime is ArtifactLifetime.POD:
        preparation = PrepareToolPodAction(
            spec=record.tool_spec,
            run_id=record.run_id,
            namespace=artifact_scope.namespace,
            timeout=timeout,
            retain=True,
            mounts=record.tool_mounts,
            node_selector=record.node_selector,
        )
        return ArtifactPodInputs(artifact_scope, preparation, True)
    reader_scope = RunScope.new(namespace=artifact_scope.namespace)
    preparation = PrepareToolPodAction(
        spec=record.tool_spec,
        run_id=reader_scope.run_id,
        namespace=reader_scope.namespace,
        timeout=timeout,
        retain=False,
        mounts=(record.destination.tool_pod_mount(),),
        node_selector=record.node_selector,
    )
    return ArtifactPodInputs(reader_scope, preparation, False)


def _state(callback: K8SCallback[SkillAction]) -> OCPArtifactState:
    """Return the artifact-aware state bound to a lifecycle callback.

    :param callback: Bound Kubernetes callback.
    :returns: Exact artifact-aware state.
    :raises TypeError: If another state class was supplied.
    """
    state = callback.state
    if not isinstance(state, OCPArtifactState):
        raise TypeError("ocp_artifact_state_required")
    return state


def _record(state: OCPArtifactState) -> NodeArtifactRecord:
    """Require the exact record resolved earlier in this invocation.

    :param state: Shared artifact-aware state.
    :returns: Resolved immutable artifact record.
    :raises KubernetesError: If no record was resolved.
    """
    if not state.artifact_record_present or not hasattr(state, "artifact_record"):
        raise KubernetesError("node_artifact_record_required")
    return state.artifact_record


def _readback(state: OCPArtifactState) -> JSONObject:
    """Observe final bytes through the current Pod and compare the run record.

    :param state: Shared state containing the current verification Pod.
    :returns: Exact artifact identity observed in-cluster.
    :raises KubernetesError: If Pod lifetime, path, digest, or size differs.
    """
    record = _record(state)
    if record.lifetime is ArtifactLifetime.POD and state.pod != record.artifact.pod:
        raise KubernetesError("node_artifact_backing_pod_mismatch")
    observed = observe_artifact(state, record.artifact.path)
    if (
        normalized_digest(observed.sha256) != normalized_digest(record.artifact.sha256)
        or observed.size != record.artifact.size
    ):
        raise KubernetesError("node_artifact_content_readback_mismatch")
    state.stored_artifact = observed
    state.readback["artifact_live_readback"] = observed.as_dict()
    return observed.as_dict()


def _comparison_readback(record: NodeArtifactRecord) -> JSONObject:
    """Report the original comparison only after the ISO digest is unchanged.

    :param record: Immutable record whose artifact was freshly read back.
    :returns: Historical comparison or explicit legacy unverified evidence.
    """
    if record.native_input_comparison is None:
        return {
            "status": "unverified",
            "artifact_sha256": record.artifact.sha256,
            "input_identity": {},
            "matched_fields": [],
            "unverified_fields": ["legacy_record_without_input_comparison"],
            "artifact_unchanged": True,
        }
    result = copy_json_object(record.native_input_comparison)
    result["artifact_unchanged"] = True
    return result


def _require_build_resources_absent(state: OCPArtifactState) -> None:
    """Prove original Pod and script ConfigMap removal through live reads.

    :param state: Shared state containing the resolved record and original scope.
    :raises KubernetesError: If either exact run-owned build resource remains.
    """
    record = _record(state)
    producer = record.artifact.pod
    if state.kubernetes.read("Pod", producer.name, producer.namespace) is not None:
        raise KubernetesError("node_artifact_build_pod_still_present")
    script_name = state.artifact_scope.name(TOOL_POD_SCRIPT_ROLE)
    if (
        state.kubernetes.read("ConfigMap", script_name, state.artifact_scope.namespace)
        is not None
    ):
        raise KubernetesError("node_artifact_build_script_still_present")


class VerifyArtifactReference(K8SCallback[ResolveRunScopeAction]):
    """Verify the persisted final artifact through the selected in-cluster Pod."""

    def run(self) -> JSONObject:
        """Compare live final bytes with the immutable run record.

        :returns: Verified artifact identity and selected lifetime.
        :raises TypeError: If the workflow state is not artifact-aware.
        :raises KubernetesError: If lookup, Pod, digest, or size differs.
        """  # noqa: DOC503 - state narrowing raises the documented TypeError.
        state = _state(self)
        record = _record(state)
        if self.action.run_id != record.run_id:
            raise KubernetesError("node_artifact_action_mismatch")
        artifact = _readback(state)
        inspection = inspect_verified_node_image(state, state.stored_artifact)
        comparison = _comparison_readback(record)
        state.readback["native_input_comparison"] = comparison
        return {
            "action": VERIFIED,
            "verified": True,
            "run_id": record.run_id,
            "lifetime": record.lifetime.value,
            "artifact": artifact,
            "inspection": inspection,
            "native_input_comparison": comparison,
        }


class VerifyArtifactBeforeRemoval(VerifyArtifactReference):
    """Capture persistent artifact identity before build-resource removal."""

    def run(self) -> JSONObject:
        """Record the pre-removal bytes and exact temporary reader identity.

        :returns: Verified pre-removal artifact evidence.
        :raises KubernetesError: If called for Pod-lifetime output or read-back differs.
        """  # noqa: DOC503 - state narrowing raises the documented TypeError.
        state = _state(self)
        if _record(state).lifetime is not ArtifactLifetime.CALLER_SELECTED_VOLUME:
            raise KubernetesError("node_artifact_persistent_lifetime_required")
        result = super().run()
        state.artifact_before_removal_verified = True
        state.artifact_readback_pod = state.pod
        return {**result, "phase": "before_build_resource_removal"}


class SelectArtifactRunForRemoval(K8SCallback[ResolveRunScopeAction]):
    """Restore original live lookup evidence before existing removal callbacks."""

    def run(self) -> JSONObject:
        """Select the original build scope without reconstructing its namespace.

        :returns: Exact original scope selected for shared removal callbacks.
        :raises TypeError: If the workflow state is not artifact-aware.
        :raises KubernetesError: If action and live record identities differ.
        """  # noqa: DOC503 - state narrowing raises the documented TypeError.
        state = _state(self)
        record = _record(state)
        scope = state.artifact_scope
        if self.action.run_id != record.run_id or scope.run_id != record.run_id:
            raise KubernetesError("node_artifact_action_mismatch")
        state.resolved_run_id = record.run_id
        state.found_scopes = (scope,)
        state.run_scope = scope
        return {
            "action": VERIFIED,
            "verified": True,
            "run_id": record.run_id,
            "scope": scope.as_dict(),
        }


class VerifyArtifactAfterRemoval(VerifyArtifactReference):
    """Prove persistent bytes survive removal of original build resources."""

    def run(self) -> JSONObject:
        """Verify original Pod absence and final bytes through the same reader.

        :returns: Verified post-removal artifact and producer-Pod absence evidence.
        :raises KubernetesError: If the pre-read is missing, reader changes, original
            Pod remains, or final bytes differ.
        """  # noqa: DOC503 - state narrowing raises the documented TypeError.
        state = _state(self)
        record = _record(state)
        if record.lifetime is not ArtifactLifetime.CALLER_SELECTED_VOLUME:
            raise KubernetesError("node_artifact_persistent_lifetime_required")
        if not state.artifact_before_removal_verified:
            raise KubernetesError("node_artifact_pre_removal_readback_required")
        if state.pod != state.artifact_readback_pod:
            raise KubernetesError("node_artifact_reader_identity_changed")
        if state.pod.uid == record.artifact.pod.uid:
            raise KubernetesError("node_artifact_separate_reader_required")
        _require_build_resources_absent(state)
        result = super().run()
        state.artifact_after_removal_verified = True
        return {
            **result,
            "phase": "after_build_resource_removal",
            "build_pod_absent": True,
        }


class VerifyTemporaryBackingRemoved(K8SCallback[ResolveRunScopeAction]):
    """Prove removal of the Pod that owned one temporary final artifact."""

    def run(self) -> JSONObject:
        """Read back exact producer-Pod absence after shared scope cleanup.

        :returns: Verified backing-Pod absence evidence.
        :raises TypeError: If the workflow state is not artifact-aware.
        :raises KubernetesError: If called for persistent output or the Pod remains.
        """  # noqa: DOC503 - state narrowing raises the documented TypeError.
        state = _state(self)
        record = _record(state)
        if self.action.run_id != record.run_id:
            raise KubernetesError("node_artifact_action_mismatch")
        if record.lifetime is not ArtifactLifetime.POD:
            raise KubernetesError("node_artifact_pod_lifetime_required")
        _require_build_resources_absent(state)
        state.temporary_backing_absent = True
        return {
            "action": VERIFIED,
            "verified": True,
            "run_id": record.run_id,
            "build_pod_absent": True,
            "artifact_lifetime_ended": True,
        }


class DeleteNodeArtifactRecord(K8SCallback[ResolveRunScopeAction]):
    """Delete only the exact owned record after lifetime-specific proof."""

    def run(self) -> JSONObject:
        """Remove the immutable ConfigMap while preserving final storage and bytes.

        :returns: Verified record deletion or an absent-run no-op.
        :raises TypeError: If the workflow state is not artifact-aware.
        :raises KubernetesError: If lifecycle proof, ownership, UID, or content differs.
        """  # noqa: DOC503 - state narrowing raises the documented TypeError.
        state = _state(self)
        if not state.artifact_record_present:
            if state.found_scopes:
                raise KubernetesError("node_artifact_record_missing")
            return {
                "action": NO_OP,
                "verified": True,
                "run_id": self.action.run_id,
                "record_absent": True,
            }
        record = _record(state)
        if self.action.run_id != record.run_id:
            raise KubernetesError("node_artifact_action_mismatch")
        if record.lifetime is ArtifactLifetime.CALLER_SELECTED_VOLUME:
            if not (
                state.artifact_before_removal_verified
                and state.artifact_after_removal_verified
            ):
                raise KubernetesError("node_artifact_persistent_readback_required")
        elif not state.temporary_backing_absent:
            raise KubernetesError("node_artifact_backing_absence_required")
        scope = state.artifact_scope
        name = scope.name(ARTIFACT_RECORD_ROLE)
        observed = self.kubernetes.read("ConfigMap", name, scope.namespace)
        if observed is None:
            raise KubernetesError("node_artifact_record_disappeared")
        if not run_owned(observed, record.run_id):
            raise KubernetesError("node_artifact_record_ownership_mismatch")
        if decode_artifact_record_resource(observed) != record:
            raise KubernetesError("node_artifact_record_content_changed")
        try:
            uid = KubernetesObject(observed).metadata.required_text("uid")
        except TypeError as exc:
            raise KubernetesError("node_artifact_record_identity_missing") from exc
        if uid != state.artifact_record_uid:
            raise KubernetesError("node_artifact_record_uid_changed")
        resources = scope.resources(self.kubernetes)
        resources.delete("ConfigMap", name)
        identity = resource_identity("ConfigMap", name, scope.namespace)
        state.retained_resources.discard(identity)
        state.artifact_record_present = False
        state.artifact_record_deleted = True
        return {
            "action": APPLIED,
            "verified": True,
            "run_id": record.run_id,
            "record": identity.as_dict(),
            "record_absent": True,
            "artifact_preserved": (
                record.lifetime is ArtifactLifetime.CALLER_SELECTED_VOLUME
            ),
        }


__all__ = [
    "ArtifactPodInputs",
    "DeleteNodeArtifactRecord",
    "SelectArtifactRunForRemoval",
    "VerifyArtifactAfterRemoval",
    "VerifyArtifactBeforeRemoval",
    "VerifyArtifactReference",
    "VerifyTemporaryBackingRemoved",
    "verification_pod_inputs",
]
