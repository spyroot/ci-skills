"""Compose native node-image production from reusable Kubernetes callbacks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Final, Optional

from lib.python.core.abstract.callbacks import AbstractCallbacks, Callbacks
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.executor import WorkflowExecutor
from lib.python.core.kubernetes.artifact_storage_callbacks import (
    CheckDestinationStorage,
    CopyArtifact,
    VerifyStoredArtifact,
)
from lib.python.core.kubernetes.artifact_storage_models import ArtifactStorageAction
from lib.python.core.kubernetes.kubernetes import K8SAuth, KubernetesError
from lib.python.core.kubernetes.ocp_artifact_run import (
    OCPArtifactState,
    PersistNodeArtifactRecord,
    RecordArtifactAction,
)
from lib.python.core.kubernetes.ocp_boot_image import (
    NodeToolAction,
    ResolveNodeImageTools,
)
from lib.python.core.kubernetes.ocp_input_callbacks import (
    StageNodeInput,
    StageNodeInputAction,
)
from lib.python.core.kubernetes.ocp_invocation import NodeBuildInputs
from lib.python.core.kubernetes.ocp_iso import NodeImageAction
from lib.python.core.kubernetes.ocp_iso_build import BuildNodeImage, PreflightNodeImage
from lib.python.core.kubernetes.ocp_iso_verify import VerifyNodeImage
from lib.python.core.kubernetes.ocp_recovery import (
    PersistNodeRecoveryRecord,
    RecoveryArtifactAction,
)
from lib.python.core.kubernetes.pod_credentials import (
    CredentialAction,
    NativePermission,
    ResolvePullSecret,
    StageWorkloadCredential,
    VerifyWorkloadCredential,
)
from lib.python.core.kubernetes.run_scope import RunScope
from lib.python.core.kubernetes.tool_pod_models import (
    PrepareToolPodAction,
    ToolPodMount,
)
from lib.python.core.kubernetes.tool_pod_workflow import prepare_tool_pod_callbacks
from lib.python.core.target import Target

WORK_VOLUME: Final = "node-image-work"
WORK_DIRECTORY: Final = PurePosixPath("/ci-skills-work")
PRIVATE_DIRECTORY: Final = "private"


def execute_callbacks(
    state: OCPArtifactState,
    callbacks: AbstractCallbacks[DeliveryState, SkillAction],
) -> None:
    """Run an ordered phase against the same concrete state and stop on failure.

    :param state: Invocation state retaining every phase's callback evidence.
    :param callbacks: Atomic operations sharing that state.
    :raises KubernetesError: A callback reports incomplete execution.
    :raises BaseException: Callback execution or verified cleanup fails.
    """  # noqa: DOC503 - shared executor preserves provider and cleanup failures.
    WorkflowExecutor(context=state, callbacks=Callbacks(callbacks)).run()
    if state.failed:
        raise KubernetesError("callback_incomplete")


def resolve_build_tools(
    state: OCPArtifactState,
    target: Target,
    inputs: NodeBuildInputs,
    timeout: int,
) -> None:
    """Authenticate and resolve tools before constructing a confirmation plan.

    :param state: Shared invocation state receiving typed tool inputs.
    :param target: Credential-bound Kubernetes target.
    :param inputs: Captured native inputs and optional exact tool override.
    :param timeout: Positive provider operation bound.
    :raises BaseException: Authentication or provider discovery fails.
    """  # noqa: DOC502 - shared executor preserves callback failures.
    execute_callbacks(
        state,
        [K8SAuth(target, timeout), ResolveNodeImageTools(NodeToolAction(inputs.tools))],
    )


def native_permissions(inputs: NodeBuildInputs) -> tuple[NativePermission, ...]:
    """Describe native node-image API operations without granting permissions.

    OpenShift creates a temporary namespace, role, binding and builder resources.
    Namespace-scoped checks cover generated names through the native all-namespace
    authorization query. Admission and optional release fallbacks remain native.

    :param inputs: Existing registry Secret selected by the caller.
    :returns: Native authorization reads for the documented creation procedure.
    """
    cluster = (
        ("get", "clusterversions.config.openshift.io"),
        ("create", "namespaces"),
        ("delete", "namespaces"),
        ("create", "clusterroles.rbac.authorization.k8s.io"),
        ("create", "clusterrolebindings.rbac.authorization.k8s.io"),
    )
    namespaced = (
        ("create", "serviceaccounts"),
        ("create", "configmaps"),
        ("create", "pods"),
        ("get", "pods"),
        ("get", "pods/log"),
        ("create", "pods/exec"),
    )
    return (
        *(NativePermission(verb, resource) for verb, resource in cluster),
        *(
            NativePermission(verb, resource, all_namespaces=True)
            for verb, resource in namespaced
        ),
        NativePermission("get", "secrets", namespace=inputs.pull_secret.namespace),
    )


def build_callbacks(
    state: OCPArtifactState,
    inputs: NodeBuildInputs,
    *,
    input_fingerprint: str,
    run_id: Optional[str],
    timeout: int,
) -> AbstractCallbacks[DeliveryState, SkillAction]:
    """Compose preparation, native generation and verified cluster delivery.

    The caller must authenticate, resolve tools and confirm the plan first. This
    function constructs actions only; all provider operations occur in callbacks.

    :param state: State containing provider-resolved or caller-selected tools.
    :param inputs: Exact native bytes and destination used in the confirmed plan.
    :param input_fingerprint: Confirmed plan digest used for safe repeat detection.
    :param run_id: Supplied correlation identity, or absent for a fresh run.
    :param timeout: Positive provider operation bound.
    :returns: Ordered preparation, credential, native and storage callbacks.
    """
    destination = inputs.destination
    scope = RunScope.new(run_id=run_id, namespace=destination.namespace)
    work = WORK_DIRECTORY / scope.run_id
    preparation = PrepareToolPodAction(
        spec=state.resolved_tools,
        run_id=run_id,
        namespace=destination.namespace,
        timeout=timeout,
        retain=False,
        node_selector=state.node_selector,
        mounts=(
            destination.tool_pod_mount(),
            ToolPodMount(WORK_VOLUME, {"emptyDir": {}}, str(work)),
        ),
    )
    build_path = destination.artifact_path
    if destination.build_output == "scratch":
        build_path = str(work / PurePosixPath(destination.artifact_path).name)
    node = NodeImageAction(inputs.node, build_path)
    storage = ArtifactStorageAction(destination)
    credentials = CredentialAction(
        str(work / PRIVATE_DIRECTORY), native_permissions(inputs), inputs.pull_secret
    )
    callbacks = prepare_tool_pod_callbacks(preparation, scope)
    callbacks.extend(
        (
            CheckDestinationStorage(storage),
            StageWorkloadCredential(credentials),
            VerifyWorkloadCredential(credentials),
            ResolvePullSecret(credentials),
            StageNodeInput(
                StageNodeInputAction(inputs.node, PurePosixPath(node.asset_directory))
            ),
            PreflightNodeImage(node),
            PersistNodeRecoveryRecord(
                RecoveryArtifactAction(
                    preparation,
                    node,
                    destination,
                    input_fingerprint,
                )
            ),
            BuildNodeImage(node),
            VerifyNodeImage(node),
        )
    )
    if destination.build_output == "scratch":
        callbacks.append(CopyArtifact(storage))
    callbacks.extend(
        (
            VerifyStoredArtifact(storage),
            PersistNodeArtifactRecord(RecordArtifactAction(preparation)),
        )
    )
    return callbacks
