"""Persist and resolve one verified OpenShift node-image artifact record.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import (
    JSONObject,
    copy_json_object,
    json_object,
    json_string,
)
from lib.python.core.kubernetes.kubernetes import K8SCallback, KubernetesError
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.kubernetes.ocp_artifact_models import (
    ArtifactLifetime,
    NodeArtifactRecord,
    OCPArtifactState,
    RecordArtifactAction,
)
from lib.python.core.kubernetes.resources import matches
from lib.python.core.kubernetes.run_scope import (
    ResolveRunScopeAction,
    RunScope,
    resource_identity,
    run_owned,
)
from lib.python.core.kubernetes.tool_pod_runtime import ToolPodK8SCallback
from lib.python.sys.status import NO_OP, VERIFIED

ARTIFACT_RECORD_ROLE: Final = "node-artifact-record"
ARTIFACT_RECORD_DATA_KEY: Final = "artifact.json"


def _canonical(record: NodeArtifactRecord) -> str:
    """Serialize one record deterministically for an immutable ConfigMap.

    :param record: Validated run record.
    :returns: Canonical compact JSON text.
    :raises TypeError: If a nested value is not JSON serializable.
    :raises ValueError: If a nested number is not finite JSON.
    """
    return json.dumps(
        record.as_dict(), allow_nan=False, separators=(",", ":"), sort_keys=True
    )


def artifact_record_document(scope: RunScope, record: NodeArtifactRecord) -> JSONObject:
    """Render one immutable labeled ConfigMap for later live resolution.

    :param scope: Original build scope resolved or opened by CI12.0.
    :param record: Verified final artifact and non-secret reconstruction inputs.
    :returns: Native ConfigMap document ready for ResourceSet ownership labels.
    :raises ValueError: If record identity differs from the selected scope.
    """
    if record.run_id != scope.run_id or record.destination.namespace != scope.namespace:
        raise ValueError("node_artifact_scope_mismatch")
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": scope.name(ARTIFACT_RECORD_ROLE),
            "namespace": scope.namespace,
            "labels": dict(scope.labels),
        },
        "immutable": True,
        "data": {ARTIFACT_RECORD_DATA_KEY: _canonical(record)},
    }


def decode_artifact_record_resource(document: JSONObject) -> NodeArtifactRecord:
    """Decode the artifact payload from one live ConfigMap.

    :param document: Native ConfigMap read-back.
    :returns: Typed immutable run record.
    :raises KubernetesError: If ConfigMap data or payload is malformed.
    """
    data = document.get("data")
    if not isinstance(data, Mapping):
        raise KubernetesError("node_artifact_record_data_invalid")
    payload = data.get(ARTIFACT_RECORD_DATA_KEY)
    if not isinstance(payload, str):
        raise KubernetesError("node_artifact_record_payload_missing")
    try:
        value = response_decoder().decode(payload)
        return NodeArtifactRecord.from_document(json_object(value))
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise KubernetesError("node_artifact_record_payload_invalid") from exc


def _state(callback: K8SCallback[SkillAction]) -> OCPArtifactState:
    """Return the artifact-aware state bound to one Kubernetes callback.

    :param callback: Bound callback using the shared execution state.
    :returns: Exact artifact-aware state.
    :raises TypeError: If another state class was supplied.
    """
    state = callback.state
    if not isinstance(state, OCPArtifactState):
        raise TypeError("ocp_artifact_state_required")
    return state


class PersistNodeArtifactRecord(ToolPodK8SCallback[RecordArtifactAction]):
    """Persist one final verified artifact record through ResourceSet."""

    def run(self) -> JSONObject:
        """Create and independently read back the immutable run record.

        :returns: ConfigMap identity plus exact non-secret artifact record.
        :raises TypeError: If the workflow state is not artifact-aware.
        :raises KubernetesError: If final artifact evidence is absent or read-back differs.
        """  # noqa: DOC503 - state narrowing raises the documented TypeError.
        state = _state(self)
        if not state.destination_verified or not hasattr(state, "stored_artifact"):
            raise KubernetesError("node_artifact_final_verification_required")
        if not hasattr(state, "storage_handle"):
            raise KubernetesError("node_artifact_storage_evidence_missing")
        lifetime = (
            ArtifactLifetime.POD
            if state.storage_handle.destination.is_temporary
            else ArtifactLifetime.CALLER_SELECTED_VOLUME
        )
        comparison_value = state.readback.get("native_input_comparison")
        comparison = (
            copy_json_object(json_object(comparison_value))
            if comparison_value is not None
            else None
        )
        record = NodeArtifactRecord(
            run_id=state.run_scope.run_id,
            destination=state.storage_handle.destination,
            tool_spec=self.action.preparation.spec,
            tool_mounts=self.action.preparation.mounts,
            artifact=state.stored_artifact,
            storage=state.storage_handle,
            lifetime=lifetime,
            node_selector=self.action.preparation.node_selector,
            native_input_comparison=comparison,
        )
        resources = state.run_scope.resources(self.kubernetes)
        desired = artifact_record_document(state.run_scope, record)
        evidence = resources.apply(desired)
        name = json_string(evidence["name"])
        observed = self.kubernetes.read("ConfigMap", name, state.run_scope.namespace)
        if observed is None or not matches(resources.managed(dict(desired)), observed):
            raise KubernetesError("node_artifact_record_readback_mismatch")
        readback = decode_artifact_record_resource(observed)
        if readback != record:
            raise KubernetesError("node_artifact_record_readback_mismatch")
        try:
            uid = KubernetesObject(observed).metadata.required_text("uid")
        except TypeError as exc:
            raise KubernetesError("node_artifact_record_identity_missing") from exc
        identity = resource_identity("ConfigMap", name, state.run_scope.namespace)
        state.retained_resources.add(identity)
        if state.run_scope.owns_namespace:
            state.retained_resources.add(
                resource_identity("Namespace", state.run_scope.namespace, "")
            )
        state.artifact_record = readback
        state.artifact_scope = state.run_scope
        state.artifact_record_uid = uid
        state.artifact_record_present = True
        state.readback["artifact_record"] = readback.as_dict()
        return {
            "action": json_string(evidence["action"]),
            "verified": True,
            "resource": {
                "kind": "ConfigMap",
                "name": name,
                "namespace": state.run_scope.namespace,
                "uid": uid,
            },
            "record": readback.as_dict(),
        }

    def cleanup(self) -> JSONObject:
        """Preserve the verified run record for later verify and remove commands.

        :returns: Explicit retained-record evidence.
        """
        state = _state(self)
        if not state.artifact_record_present:
            return {"action": NO_OP, "verified": True}
        return {
            "action": NO_OP,
            "verified": True,
            "retained": resource_identity(
                "ConfigMap",
                state.artifact_scope.name(ARTIFACT_RECORD_ROLE),
                state.artifact_scope.namespace,
            ).as_dict(),
        }


class ResolveNodeArtifactRecord(K8SCallback[ResolveRunScopeAction]):
    """Read one artifact record from the scope discovered by live labels."""

    def run(self) -> JSONObject:
        """Resolve the exact immutable record without guessing its namespace.

        :returns: Verified record evidence or an absent no-op for repeated removal.
        :raises TypeError: If the workflow state is not artifact-aware.
        :raises KubernetesError: If lookup evidence is stale, ambiguous, not
            run-owned, or malformed.
        """  # noqa: DOC503 - state narrowing raises the documented TypeError.
        state = _state(self)
        if state.resolved_run_id != self.action.run_id or any(
            scope.run_id != self.action.run_id for scope in state.found_scopes
        ):
            raise KubernetesError("node_artifact_scope_lookup_mismatch")
        if not state.found_scopes:
            state.artifact_record_present = False
            return {
                "action": NO_OP,
                "verified": True,
                "run_id": self.action.run_id,
                "present": False,
            }
        if len(state.found_scopes) != 1:
            raise KubernetesError("node_artifact_scope_ambiguous")
        scope = state.found_scopes[0]
        name = scope.name(ARTIFACT_RECORD_ROLE)
        observed = self.kubernetes.read("ConfigMap", name, scope.namespace)
        if observed is None:
            state.artifact_record_present = False
            return {
                "action": NO_OP,
                "verified": True,
                "run_id": self.action.run_id,
                "present": False,
            }
        if not run_owned(observed, self.action.run_id):
            raise KubernetesError("node_artifact_record_ownership_mismatch")
        record = decode_artifact_record_resource(observed)
        if (
            record.run_id != self.action.run_id
            or record.destination.namespace != scope.namespace
        ):
            raise KubernetesError("node_artifact_record_scope_mismatch")
        try:
            uid = KubernetesObject(observed).metadata.required_text("uid")
        except TypeError as exc:
            raise KubernetesError("node_artifact_record_identity_missing") from exc
        state.artifact_record = record
        state.artifact_scope = scope
        state.artifact_record_uid = uid
        state.artifact_record_present = True
        state.retained_resources.add(
            resource_identity("ConfigMap", name, scope.namespace)
        )
        state.readback["artifact_record"] = record.as_dict()
        return {
            "action": VERIFIED,
            "verified": True,
            "run_id": self.action.run_id,
            "present": True,
            "resource": {
                "kind": "ConfigMap",
                "name": name,
                "namespace": scope.namespace,
                "uid": uid,
            },
            "record": record.as_dict(),
        }


class RequireNodeArtifactRecord(ResolveNodeArtifactRecord):
    """Resolve a record and reject absence for the verify command."""

    def run(self) -> JSONObject:
        """Require a live artifact record after label resolution.

        :returns: Verified record evidence.
        :raises KubernetesError: If the run or record is absent.
        """
        result = super().run()
        if not _state(self).artifact_record_present:
            raise KubernetesError("node_artifact_record_not_found")
        return result


__all__ = [
    "ARTIFACT_RECORD_ROLE",
    "ArtifactLifetime",
    "NodeArtifactRecord",
    "OCPArtifactState",
    "PersistNodeArtifactRecord",
    "RecordArtifactAction",
    "RequireNodeArtifactRecord",
    "ResolveNodeArtifactRecord",
    "artifact_record_document",
    "decode_artifact_record_resource",
]
