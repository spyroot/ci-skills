"""Compose live lookup, verification and cleanup for recorded node images.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from lib.python.core.abstract.callbacks import AbstractCallbacks
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.kubernetes.kubernetes import K8SAuth
from lib.python.core.kubernetes.ocp_artifact_lifecycle import (
    DeleteNodeArtifactRecord,
    SelectArtifactRunForRemoval,
    VerifyArtifactAfterRemoval,
    VerifyArtifactBeforeRemoval,
    VerifyArtifactReference,
    VerifyTemporaryBackingRemoved,
    verification_pod_inputs,
)
from lib.python.core.kubernetes.ocp_artifact_run import (
    ArtifactLifetime,
    OCPArtifactState,
    RequireNodeArtifactRecord,
    ResolveNodeArtifactRecord,
)
from lib.python.core.kubernetes.ocp_build_workflow import execute_callbacks
from lib.python.core.kubernetes.ocp_invocation import NodeOperation
from lib.python.core.kubernetes.run_scope import (
    RemoveScope,
    ResolveRunScopeAction,
    ResolveRunScopes,
)
from lib.python.core.kubernetes.tool_pod import RemoveToolPod, RemoveToolPodAction
from lib.python.core.kubernetes.tool_pod_workflow import prepare_tool_pod_callbacks
from lib.python.core.target import Target


def resolve_artifact(
    state: OCPArtifactState,
    target: Target,
    operation: NodeOperation,
    run_id: str,
    timeout: int,
) -> None:
    """Read labeled ownership and the artifact record before choosing a reader.

    :param state: Invocation state receiving the immutable live artifact reference.
    :param target: Credential-bound selected Kubernetes target.
    :param operation: Verify requires a record; repeated remove permits absence.
    :param run_id: Exact caller-supplied build correlation identity.
    :param timeout: Positive native API operation bound.
    :raises BaseException: Authentication, lookup or record decoding fails.
    """  # noqa: DOC502 - the common executor preserves provider failures.
    action = ResolveRunScopeAction(run_id)
    record = (
        RequireNodeArtifactRecord
        if operation == NodeOperation.VERIFY
        else ResolveNodeArtifactRecord
    )
    execute_callbacks(
        state, [K8SAuth(target, timeout), ResolveRunScopes(action), record(action)]
    )


def _persistent_removal(
    action: ResolveRunScopeAction,
) -> AbstractCallbacks[DeliveryState, SkillAction]:
    """Verify persistent bytes before and after removing original build resources.

    :param action: Existing artifact run selected by the caller.
    :returns: Ordered read-back and removal callbacks; storage bytes are preserved.
    """
    return [
        VerifyArtifactBeforeRemoval(action),
        SelectArtifactRunForRemoval(action),
        RemoveToolPod(RemoveToolPodAction(action.run_id)),
        VerifyArtifactAfterRemoval(action),
        DeleteNodeArtifactRecord(action),
        RemoveScope(action),
    ]


def _temporary_removal(
    action: ResolveRunScopeAction,
) -> AbstractCallbacks[DeliveryState, SkillAction]:
    """Remove the exact Pod backing temporary output and verify its absence.

    :param action: Existing artifact run selected by the caller.
    :returns: Ordered resource removal and lifetime evidence callbacks.
    """
    return [
        SelectArtifactRunForRemoval(action),
        RemoveToolPod(RemoveToolPodAction(action.run_id)),
        VerifyTemporaryBackingRemoved(action),
        DeleteNodeArtifactRecord(action),
        RemoveScope(action),
    ]


def artifact_callbacks(
    state: OCPArtifactState,
    operation: NodeOperation,
    run_id: str,
    timeout: int,
) -> AbstractCallbacks[DeliveryState, SkillAction]:
    """Compose one operation using the artifact resolved before confirmation.

    :param state: State containing live lookup evidence and any resolved record.
    :param operation: Verify or remove; build uses the production composition.
    :param run_id: Exact run whose ownership was resolved.
    :param timeout: Positive operation bound for the temporary artifact reader.
    :returns: Atomic verification or removal callbacks with existing Pod preparation.
    :raises ValueError: The supplied operation is not verify or remove.
    """
    if operation not in (NodeOperation.VERIFY, NodeOperation.REMOVE):
        raise ValueError("node_artifact_operation_invalid")
    lookup = ResolveRunScopeAction(run_id)
    if not state.artifact_record_present:
        if operation == NodeOperation.VERIFY:
            raise ValueError("node_artifact_record_not_found")
        return [DeleteNodeArtifactRecord(lookup), RemoveScope(lookup)]
    record = state.artifact_record
    if operation == NodeOperation.REMOVE and record.lifetime == ArtifactLifetime.POD:
        return _temporary_removal(lookup)
    reader = verification_pod_inputs(record, state.artifact_scope, timeout=timeout)
    callbacks = prepare_tool_pod_callbacks(reader.preparation, reader.scope)
    if operation == NodeOperation.VERIFY:
        callbacks.append(VerifyArtifactReference(lookup))
    else:
        callbacks.extend(_persistent_removal(lookup))
    return callbacks
