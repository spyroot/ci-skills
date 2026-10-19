"""Persist minimal non-secret recovery identity before native ISO execution.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import (
    JSONObject,
    copy_json_object,
    json_object,
    json_string,
)
from lib.python.core.kubernetes.artifact_storage_models import StorageDestination
from lib.python.core.kubernetes.artifact_storage_runtime import observe_artifact
from lib.python.core.kubernetes.kubernetes import K8SCallback, KubernetesError
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.kubernetes.ocp_artifact_models import (
    OCPArtifactState,
    decode_pod_handle,
)
from lib.python.core.kubernetes.ocp_iso import (
    NodeImageAction,
    read_native_exit_code,
    retain_native_recovery,
)
from lib.python.core.kubernetes.ocp_iso_build import cleanup_failed_native_outputs
from lib.python.core.kubernetes.ocp_iso_verify import inspect_verified_node_image
from lib.python.core.kubernetes.pod_files import remove_pod_file
from lib.python.core.kubernetes.resources import matches
from lib.python.core.kubernetes.run_scope import (
    ResolveRunScopeAction,
    RunScope,
    resource_identity,
    run_owned,
    validate_run_id,
)
from lib.python.core.kubernetes.tool_pod_models import PodHandle, PrepareToolPodAction
from lib.python.core.kubernetes.tool_pod_resources import (
    SPEC_FINGERPRINT_ANNOTATION,
    SPEC_FINGERPRINT_LABEL,
    TOOL_POD_ROLE,
)
from lib.python.core.kubernetes.tool_pod_runtime import ToolPodK8SCallback
from lib.python.sys.status import APPLIED, NO_OP, VERIFIED

RECOVERY_RECORD_ROLE: Final = "node-artifact-recovery"
RECOVERY_RECORD_DATA_KEY: Final = "recovery.json"
RECOVERY_RECORD_VERSION: Final = "1.0"
_DIGEST: Final = re.compile(r"sha256:[0-9a-f]{64}")
_FIELDS: Final = frozenset(
    {
        "schema_version",
        "run_id",
        "input_fingerprint",
        "destination",
        "build_path",
        "status_path",
        "pod",
        "tool_pod_fingerprint",
        "tool_pod_fingerprint_label",
    }
)


def _absolute_path(value: str) -> str:
    """Require one unchanged absolute in-Pod path.

    :param value: Persisted path selected by the confirmed build.
    :returns: The unchanged safe path.
    :raises ValueError: If the path is empty, relative, traversing, or contains a
        process-argument control character.
    """
    candidate = PurePosixPath(value)
    if (
        not value
        or not candidate.is_absolute()
        or ".." in candidate.parts
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise ValueError("node_recovery_path_invalid")
    return value


@dataclass(frozen=True)
class RecoveryArtifactAction(SkillAction):
    """Bind the confirmed build inputs needed for recovery metadata.

    :param preparation: Exact tool Pod action used by the build.
    :param node: Exact native build output selection.
    :param destination: Caller-selected final storage wrapper.
    :param input_fingerprint: Digest of target, functional code, and build inputs.

    .. attribute :: preparation
        :type: PrepareToolPodAction

        Exact tool Pod action used by the build.

    .. attribute :: node
        :type: NodeImageAction

        Exact native build output selection.

    .. attribute :: destination
        :type: StorageDestination

        Caller-selected final storage wrapper.

    .. attribute :: input_fingerprint
        :type: str

        Digest of target, functional code, and build inputs.
    """

    preparation: PrepareToolPodAction
    node: NodeImageAction
    destination: StorageDestination
    input_fingerprint: str

    def __post_init__(self) -> None:
        """Validate the non-secret identity before provider access.

        :raises ValueError: If the fingerprint is not one SHA-256 identity.
        """
        if _DIGEST.fullmatch(self.input_fingerprint) is None:
            raise ValueError("node_recovery_input_fingerprint_invalid")


@dataclass(frozen=True)
class ReuseNodeArtifactAction(SkillAction):
    """Bind one existing run to the exact confirmed build fingerprint.

    :param run_id: Caller-selected existing build run.
    :param input_fingerprint: Digest of the currently confirmed build plan.

    .. attribute :: run_id
        :type: str

        Caller-selected existing build run.

    .. attribute :: input_fingerprint
        :type: str

        Digest of the currently confirmed build plan.
    """

    run_id: str
    input_fingerprint: str

    def __post_init__(self) -> None:
        """Validate run and digest identities before provider access.

        :raises ValueError: If either identity is malformed.
        """
        validate_run_id(self.run_id)
        if _DIGEST.fullmatch(self.input_fingerprint) is None:
            raise ValueError("node_recovery_input_fingerprint_invalid")


@dataclass(frozen=True)
class NodeRecoveryRecord:
    """Retain enough identity to reconcile one interrupted native command.

    :param run_id: Exact ownership label value.
    :param input_fingerprint: Confirmed target, code, and input identity.
    :param destination: Caller-selected final storage wrapper.
    :param build_path: Exact native output path in the retained Pod.
    :param status_path: Exact private native completion path.
    :param pod: Immutable producer Pod identity.
    :param tool_pod_fingerprint: Full expected prepared-Pod fingerprint.
    :param tool_pod_fingerprint_label: Label-safe prepared-Pod fingerprint.

    .. attribute :: run_id
        :type: str

        Exact ownership label value.

    .. attribute :: input_fingerprint
        :type: str

        Confirmed target, code, and input identity.

    .. attribute :: destination
        :type: StorageDestination

        Caller-selected final storage wrapper.

    .. attribute :: build_path
        :type: str

        Exact native output path in the retained Pod.

    .. attribute :: status_path
        :type: str

        Exact private native completion path.

    .. attribute :: pod
        :type: PodHandle

        Immutable producer Pod identity.

    .. attribute :: tool_pod_fingerprint
        :type: str

        Full expected prepared-Pod fingerprint.

    .. attribute :: tool_pod_fingerprint_label
        :type: str

        Label-safe prepared-Pod fingerprint.
    """

    run_id: str
    input_fingerprint: str
    destination: StorageDestination
    build_path: str
    status_path: str
    pod: PodHandle
    tool_pod_fingerprint: str
    tool_pod_fingerprint_label: str

    def __post_init__(self) -> None:
        """Reject cross-run, path, destination, or fingerprint mismatches.

        :raises ValueError: If persisted identities are inconsistent.
        """
        validate_run_id(self.run_id)
        if _DIGEST.fullmatch(self.input_fingerprint) is None:
            raise ValueError("node_recovery_input_fingerprint_invalid")
        if self.pod.namespace != self.destination.namespace:
            raise ValueError("node_recovery_namespace_mismatch")
        _absolute_path(self.build_path)
        _absolute_path(self.status_path)
        if not self.tool_pod_fingerprint or not self.tool_pod_fingerprint_label:
            raise ValueError("node_recovery_tool_fingerprint_invalid")

    @classmethod
    def from_document(cls, document: JSONObject) -> NodeRecoveryRecord:
        """Decode one exact internal recovery record.

        :param document: Strictly decoded ConfigMap payload.
        :returns: Validated immutable recovery identity.
        :raises KubernetesError: If fields or cross-field identities are invalid.
        """  # noqa: DOC503 - caught validation is normalized to KubernetesError.
        if set(document) != _FIELDS:
            raise KubernetesError("node_recovery_record_fields_invalid")
        try:
            if document["schema_version"] != RECOVERY_RECORD_VERSION:
                raise ValueError("unsupported recovery record version")
            return cls(
                run_id=json_string(document["run_id"]),
                input_fingerprint=json_string(document["input_fingerprint"]),
                destination=StorageDestination.from_document(
                    copy_json_object(json_object(document["destination"]))
                ),
                build_path=json_string(document["build_path"]),
                status_path=json_string(document["status_path"]),
                pod=decode_pod_handle(json_object(document["pod"])),
                tool_pod_fingerprint=json_string(document["tool_pod_fingerprint"]),
                tool_pod_fingerprint_label=json_string(
                    document["tool_pod_fingerprint_label"]
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise KubernetesError("node_recovery_record_invalid") from exc

    def as_dict(self) -> JSONObject:
        """Render the safe immutable recovery payload.

        :returns: Non-secret run, Pod, path, destination, and fingerprint identity.
        """
        return {
            "schema_version": RECOVERY_RECORD_VERSION,
            "run_id": self.run_id,
            "input_fingerprint": self.input_fingerprint,
            "destination": self.destination.as_dict(),
            "build_path": self.build_path,
            "status_path": self.status_path,
            "pod": self.pod.as_dict(),
            "tool_pod_fingerprint": self.tool_pod_fingerprint,
            "tool_pod_fingerprint_label": self.tool_pod_fingerprint_label,
        }


def _canonical(record: NodeRecoveryRecord) -> str:
    """Serialize one recovery record deterministically.

    :param record: Validated non-secret recovery record.
    :returns: Canonical compact JSON text.
    """
    return json.dumps(
        record.as_dict(), allow_nan=False, separators=(",", ":"), sort_keys=True
    )


def recovery_record_document(scope: RunScope, record: NodeRecoveryRecord) -> JSONObject:
    """Render one immutable run-owned recovery ConfigMap.

    :param scope: Exact build scope.
    :param record: Recovery identity captured after preflight.
    :returns: Native immutable ConfigMap document.
    :raises ValueError: If record and scope identities differ.
    """
    if record.run_id != scope.run_id or record.pod.namespace != scope.namespace:
        raise ValueError("node_recovery_scope_mismatch")
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": scope.name(RECOVERY_RECORD_ROLE),
            "namespace": scope.namespace,
            "labels": dict(scope.labels),
        },
        "immutable": True,
        "data": {RECOVERY_RECORD_DATA_KEY: _canonical(record)},
    }


def decode_recovery_record_resource(document: JSONObject) -> NodeRecoveryRecord:
    """Decode one recovery payload from a live ConfigMap.

    :param document: Native ConfigMap read-back.
    :returns: Typed immutable recovery record.
    :raises KubernetesError: If data or strict JSON shape is invalid.
    """
    data = document.get("data")
    if not isinstance(data, Mapping):
        raise KubernetesError("node_recovery_record_data_invalid")
    payload = data.get(RECOVERY_RECORD_DATA_KEY)
    if not isinstance(payload, str):
        raise KubernetesError("node_recovery_record_payload_missing")
    try:
        return NodeRecoveryRecord.from_document(
            json_object(response_decoder().decode(payload))
        )
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise KubernetesError("node_recovery_record_payload_invalid") from exc


def _state(callback: K8SCallback[SkillAction]) -> OCPArtifactState:
    """Return the artifact-aware shared state.

    :param callback: Bound Kubernetes callback.
    :returns: Exact artifact-aware state.
    :raises TypeError: If another state class was supplied.
    """
    state = callback.state
    if not isinstance(state, OCPArtifactState):
        raise TypeError("ocp_artifact_state_required")
    return state


def _delete_resolved_recovery_record(state: OCPArtifactState) -> JSONObject:
    """Delete the exact resolved recovery ConfigMap after identity read-back.

    :param state: Shared state containing record, scope, UID, and provider service.
    :returns: Deleted ConfigMap identity.
    :raises KubernetesError: If ownership, content, UID, or deletion differs.
    """
    record, scope = state.recovery_record, state.recovery_scope
    name = scope.name(RECOVERY_RECORD_ROLE)
    observed = state.kubernetes.read("ConfigMap", name, scope.namespace)
    if observed is None or not run_owned(observed, record.run_id):
        raise KubernetesError("node_recovery_record_disappeared")
    if decode_recovery_record_resource(observed) != record:
        raise KubernetesError("node_recovery_record_content_changed")
    try:
        uid = KubernetesObject(observed).metadata.required_text("uid")
    except TypeError as exc:
        raise KubernetesError("node_recovery_record_identity_missing") from exc
    if uid != state.recovery_record_uid:
        raise KubernetesError("node_recovery_record_uid_changed")
    scope.resources(state.kubernetes).delete("ConfigMap", name)
    identity = resource_identity("ConfigMap", name, scope.namespace)
    state.retained_resources.discard(identity)
    state.recovery_record_present = False
    state.recovery_record_deleted = True
    return identity.as_dict()


def _restore_recovery_pod(state: OCPArtifactState) -> None:
    """Restore the exact retained Pod only after ownership and fingerprint reads.

    :param state: Shared state containing one resolved recovery record and scope.
    :raises KubernetesError: If the retained Pod is absent or no longer identical.
    """
    record, scope = state.recovery_record, state.recovery_scope
    if record.pod.name != scope.name(TOOL_POD_ROLE):
        raise KubernetesError("node_recovery_pod_name_mismatch")
    observed = state.kubernetes.read("Pod", record.pod.name, scope.namespace)
    if observed is None or not run_owned(observed, record.run_id):
        raise KubernetesError("node_recovery_pod_missing")
    try:
        metadata = KubernetesObject(observed).metadata
        uid = metadata.required_text("uid")
        labels = metadata.object("labels").document
        annotations = metadata.object("annotations").document
    except TypeError as exc:
        raise KubernetesError("node_recovery_pod_identity_invalid") from exc
    if (
        uid != record.pod.uid
        or labels.get(SPEC_FINGERPRINT_LABEL) != record.tool_pod_fingerprint_label
        or annotations.get(SPEC_FINGERPRINT_ANNOTATION) != record.tool_pod_fingerprint
    ):
        raise KubernetesError("node_recovery_pod_identity_changed")
    state.run_scope = scope
    state.pod = record.pod
    state.native_status_path = record.status_path


class ReconcileNodeRecovery(K8SCallback[ResolveRunScopeAction]):
    """Read the retained native completion record for one exact owned run."""

    def run(self) -> JSONObject:
        """Restore the retained Pod and classify current native completion.

        :returns: Verified known native exit evidence.
        :raises KubernetesError: If the Pod changed or completion remains unknown.
        """
        state = _state(self)
        if (
            not state.recovery_record_present
            or state.recovery_record.run_id != self.action.run_id
        ):
            raise KubernetesError("node_recovery_record_required")
        _restore_recovery_pod(state)
        native_exit_code = read_native_exit_code(state)
        if native_exit_code is None:
            state.native_started = True
            state.native_completion_unknown = True
            retain_native_recovery(state)
            raise KubernetesError("node_recovery_native_completion_unknown")
        state.native_started = True
        state.native_completion_unknown = False
        state.native_exit_code = native_exit_code
        state.native_status_file = observe_artifact(state, state.native_status_path)
        state.build_completed = native_exit_code == 0
        evidence: JSONObject = {
            "action": VERIFIED,
            "verified": True,
            "run_id": self.action.run_id,
            "native_exit_code": native_exit_code,
            "completion_unknown": False,
        }
        state.readback["node_image_recovery"] = evidence
        return evidence


class VerifyRecoveredNodeImage(K8SCallback[ResolveRunScopeAction]):
    """Verify successful native output retained after interrupted transport."""

    def run(self) -> JSONObject:
        """Observe the retained ISO and inspect its embedded Ignition evidence.

        :returns: Verified retained build artifact and inspection.
        :raises KubernetesError: If native completion failed or bytes are invalid.
        """
        state = _state(self)
        if state.native_exit_code != 0 or not state.build_completed:
            raise KubernetesError(
                f"node_recovery_native_failed:exit={state.native_exit_code}"
            )
        artifact = observe_artifact(state, state.recovery_record.build_path)
        inspection = inspect_verified_node_image(state, artifact)
        comparison: JSONObject = {
            "status": "unverified",
            "artifact_sha256": artifact.sha256,
            "input_identity": {
                "input_fingerprint": state.recovery_record.input_fingerprint
            },
            "matched_fields": [],
            "unverified_fields": ["recovery_input_unavailable"],
        }
        state.readback["native_input_comparison"] = comparison
        state.build_artifact = artifact
        state.readback["node_image_source"] = artifact.as_dict()
        return {
            "action": VERIFIED,
            "verified": True,
            "run_id": self.action.run_id,
            "artifact": artifact.as_dict(),
            "inspection": inspection,
            "native_input_comparison": comparison,
            "final_delivery_recorded": False,
        }


class RequireReusableNodeArtifact(K8SCallback[ReuseNodeArtifactAction]):
    """Accept reuse only for matching recovery and final artifact records."""

    def run(self) -> JSONObject:
        """Compare run, destination, and confirmed input identities.

        :returns: Verified reuse eligibility before any temporary reader is staged.
        :raises KubernetesError: If records are missing or identities differ.
        """
        state = _state(self)
        if not state.artifact_record_present or not state.recovery_record_present:
            raise KubernetesError("node_artifact_reuse_records_required")
        artifact = state.artifact_record
        recovery = state.recovery_record
        if (
            artifact.run_id != self.action.run_id
            or recovery.run_id != self.action.run_id
        ):
            raise KubernetesError("node_artifact_reuse_run_mismatch")
        if artifact.destination != recovery.destination:
            raise KubernetesError("node_artifact_reuse_destination_mismatch")
        if recovery.input_fingerprint != self.action.input_fingerprint:
            raise KubernetesError("node_artifact_reuse_input_mismatch")
        state.build_reused = True
        return {
            "action": VERIFIED,
            "verified": True,
            "run_id": self.action.run_id,
            "input_fingerprint": self.action.input_fingerprint,
            "reusable": True,
        }


class ReportReusedNodeArtifact(K8SCallback[ReuseNodeArtifactAction]):
    """Report a verified existing artifact as an idempotent build result."""

    def run(self) -> JSONObject:
        """Return a no-op only after live final-byte verification completed.

        :returns: Existing artifact identity and explicit no-op evidence.
        :raises KubernetesError: If eligibility or live read-back is absent.
        """
        state = _state(self)
        if not state.build_reused or not hasattr(state, "stored_artifact"):
            raise KubernetesError("node_artifact_reuse_readback_required")
        return {
            "action": NO_OP,
            "verified": True,
            "run_id": self.action.run_id,
            "artifact": state.stored_artifact.as_dict(),
            "reason": "matching_artifact_verified",
        }


class CleanupRecoveredNodeRun(K8SCallback[ResolveRunScopeAction]):
    """Remove exact private status and only proven partial native output."""

    def run(self) -> JSONObject:
        """Delete exact partial output after failure and the private status record.

        A successful run can be removed after its final record was never written. Its
        native output is preserved because the preflight does not prove that output
        is partial. A known failed run removes only paths proved absent before native
        execution.

        :returns: Verified exact-path cleanup evidence.
        :raises KubernetesError: If completion is unknown or cleanup fails.
        """
        state = _state(self)
        if state.native_completion_unknown or state.native_exit_code < 0:
            raise KubernetesError("node_recovery_known_completion_required")
        failed_outputs_removed = False
        if state.native_exit_code > 0:
            build_path = state.recovery_record.build_path
            asset_directory, _, output_name = build_path.rpartition("/")
            cleanup_failed_native_outputs(state, asset_directory or "/", output_name)
            failed_outputs_removed = True
        removed_status = remove_pod_file(state, state.native_status_file)
        return {
            "action": APPLIED,
            "verified": True,
            "run_id": self.action.run_id,
            "native_exit_code": state.native_exit_code,
            "failed_outputs_removed": failed_outputs_removed,
            "completion_record_removed": removed_status,
        }


class PersistNodeRecoveryRecord(ToolPodK8SCallback[RecoveryArtifactAction]):
    """Persist recovery identity after absence preflight and before native execution."""

    def run(self) -> JSONObject:
        """Create and read back the immutable recovery record.

        :returns: ConfigMap and non-secret recovery identity.
        :raises KubernetesError: If preflight is absent or provider read-back differs.
        """
        state = _state(self)
        if not state.preflight_verified or not state.native_status_path:
            raise KubernetesError("node_recovery_preflight_required")
        record = NodeRecoveryRecord(
            run_id=state.run_scope.run_id,
            input_fingerprint=self.action.input_fingerprint,
            destination=self.action.destination,
            build_path=self.action.node.build_path,
            status_path=state.native_status_path,
            pod=state.pod,
            tool_pod_fingerprint=self.action.preparation.fingerprint,
            tool_pod_fingerprint_label=self.action.preparation.fingerprint_label,
        )
        resources = state.run_scope.resources(self.kubernetes)
        desired = recovery_record_document(state.run_scope, record)
        evidence = resources.apply(desired)
        name = json_string(evidence["name"])
        observed = self.kubernetes.read("ConfigMap", name, state.run_scope.namespace)
        if observed is None or not matches(resources.managed(dict(desired)), observed):
            raise KubernetesError("node_recovery_record_readback_mismatch")
        readback = decode_recovery_record_resource(observed)
        if readback != record:
            raise KubernetesError("node_recovery_record_readback_mismatch")
        try:
            uid = KubernetesObject(observed).metadata.required_text("uid")
        except TypeError as exc:
            raise KubernetesError("node_recovery_record_identity_missing") from exc
        identity = resource_identity("ConfigMap", name, state.run_scope.namespace)
        state.retained_resources.add(identity)
        state.recovery_record = readback
        state.recovery_scope = state.run_scope
        state.recovery_record_uid = uid
        state.recovery_record_present = True
        state.readback["recovery_record"] = readback.as_dict()
        return {
            "action": json_string(evidence["action"]),
            "verified": True,
            "resource": {**identity.as_dict(), "uid": uid},
            "recovery": readback.as_dict(),
        }

    def cleanup(self) -> JSONObject:
        """Preserve recoverable work or remove a fully cleaned failed-run record.

        :returns: Retained or removed recovery-record evidence.
        :raises KubernetesError: If exact owned deletion cannot be verified.
        """  # noqa: DOC502 - exact deletion helpers propagate KubernetesError.
        state = _state(self)
        if not state.recovery_record_present:
            return {"action": NO_OP, "verified": True}
        identity = resource_identity(
            "ConfigMap",
            state.recovery_scope.name(RECOVERY_RECORD_ROLE),
            state.recovery_scope.namespace,
        )
        if not state.native_started or (
            state.native_failure_cleaned and not state.build_completed
        ):
            removed = _delete_resolved_recovery_record(state)
            return {
                "action": APPLIED,
                "verified": True,
                "removed": removed,
            }
        if not state.artifact_record_present:
            retain_native_recovery(state)
        return {
            "action": NO_OP,
            "verified": True,
            "retained": identity.as_dict(),
        }


class ResolveNodeRecoveryRecord(K8SCallback[ResolveRunScopeAction]):
    """Resolve an optional recovery record from exact live scope evidence."""

    def run(self) -> JSONObject:
        """Read one run-owned recovery ConfigMap without requiring its presence.

        :returns: Verified recovery identity or an absent no-op.
        :raises KubernetesError: If lookup, ownership, or payload evidence differs.
        """
        state = _state(self)
        if state.resolved_run_id != self.action.run_id or any(
            scope.run_id != self.action.run_id for scope in state.found_scopes
        ):
            raise KubernetesError("node_recovery_scope_lookup_mismatch")
        if not state.found_scopes:
            state.recovery_record_present = False
            return {
                "action": NO_OP,
                "verified": True,
                "run_id": self.action.run_id,
                "present": False,
            }
        if len(state.found_scopes) != 1:
            raise KubernetesError("node_recovery_scope_ambiguous")
        scope = state.found_scopes[0]
        name = scope.name(RECOVERY_RECORD_ROLE)
        observed = self.kubernetes.read("ConfigMap", name, scope.namespace)
        if observed is None:
            state.recovery_record_present = False
            return {
                "action": NO_OP,
                "verified": True,
                "run_id": self.action.run_id,
                "present": False,
            }
        if not run_owned(observed, self.action.run_id):
            raise KubernetesError("node_recovery_record_ownership_mismatch")
        record = decode_recovery_record_resource(observed)
        if (
            record.run_id != self.action.run_id
            or record.pod.namespace != scope.namespace
        ):
            raise KubernetesError("node_recovery_record_scope_mismatch")
        try:
            uid = KubernetesObject(observed).metadata.required_text("uid")
        except TypeError as exc:
            raise KubernetesError("node_recovery_record_identity_missing") from exc
        state.recovery_record = record
        state.recovery_scope = scope
        state.recovery_record_uid = uid
        state.recovery_record_present = True
        state.preflight_verified = True
        identity = resource_identity("ConfigMap", name, scope.namespace)
        state.retained_resources.add(identity)
        state.readback["recovery_record"] = record.as_dict()
        return {
            "action": VERIFIED,
            "verified": True,
            "run_id": record.run_id,
            "present": True,
            "resource": {**identity.as_dict(), "uid": uid},
            "recovery": record.as_dict(),
        }


class DeleteNodeRecoveryRecord(K8SCallback[ResolveRunScopeAction]):
    """Delete only the exact resolved recovery record after owned-resource cleanup."""

    def run(self) -> JSONObject:
        """Remove the immutable ConfigMap after UID, ownership, and content checks.

        :returns: Verified deletion or an idempotent absent no-op.
        :raises KubernetesError: If live identity or content changed.
        """
        state = _state(self)
        if not state.recovery_record_present:
            return {
                "action": NO_OP,
                "verified": True,
                "run_id": self.action.run_id,
                "recovery_record_absent": True,
            }
        record = state.recovery_record
        if record.run_id != self.action.run_id:
            raise KubernetesError("node_recovery_action_mismatch")
        removed = _delete_resolved_recovery_record(state)
        return {
            "action": APPLIED,
            "verified": True,
            "run_id": record.run_id,
            "record": removed,
            "recovery_record_absent": True,
        }


__all__ = [
    "RECOVERY_RECORD_ROLE",
    "CleanupRecoveredNodeRun",
    "DeleteNodeRecoveryRecord",
    "NodeRecoveryRecord",
    "PersistNodeRecoveryRecord",
    "ReconcileNodeRecovery",
    "RecoveryArtifactAction",
    "ReportReusedNodeArtifact",
    "RequireReusableNodeArtifact",
    "ResolveNodeRecoveryRecord",
    "ReuseNodeArtifactAction",
    "VerifyRecoveredNodeImage",
    "decode_recovery_record_resource",
    "recovery_record_document",
]
