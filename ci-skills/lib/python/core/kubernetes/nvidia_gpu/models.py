"""Typed models and constants for NVIDIA GPU Operator operations on OpenShift (CI17).

All Kubernetes reads and writes use qualified resource names (DEC-CI17-11) and
carry zero site-specific values.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Final, Optional

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.action_modes import ActionMode, ActionModePolicy
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.json_types import JSONObject
from lib.python.core.target import Target

GPU_OPERATOR_KIND: Final = "nvidia_gpu_operator"
DEFAULT_GPU_NAMESPACE: Final = "nvidia-gpu-operator"
DEFAULT_NFD_NAMESPACE: Final = "openshift-nfd"
GPU_OPERATOR_PACKAGE: Final = "gpu-operator-certified"
NFD_PACKAGE: Final = "nfd"
DEFAULT_GPU_TIMEOUT: Final = 600
MAX_GPU_TIMEOUT: Final = 3600

# Qualified Kubernetes resource names (DEC-CI17-11).
CLUSTER_POLICIES: Final = "clusterpolicies.nvidia.com"
GPU_CLUSTERS: Final = "gpuclusters.nvidia.com"
NVIDIA_DRIVERS: Final = "nvidiadrivers.nvidia.com"
NODE_FEATURE_DISCOVERIES: Final = "nodefeaturediscoveries.nfd.openshift.io"
MACHINE_SETS: Final = "machinesets.machine.openshift.io"
MACHINE_CONFIGS: Final = "machineconfigs.machineconfiguration.openshift.io"
MACHINE_CONFIG_POOLS: Final = "machineconfigpools.machineconfiguration.openshift.io"
RUNTIME_CLASSES: Final = "runtimeclasses.node.k8s.io"
DEVICE_CLASSES: Final = "deviceclasses.resource.k8s.io"
RESOURCE_SLICES: Final = "resourceslices.resource.k8s.io"
DAEMONSETS: Final = "daemonsets.apps"
PODS: Final = "pods"
NODES: Final = "nodes"

# Documented NVIDIA and NFD node labels.
LABEL_PCI_NVIDIA: Final = "feature.node.kubernetes.io/pci-10de.present"
LABEL_KERNEL_FULL: Final = "feature.node.kubernetes.io/kernel-version.full"
LABEL_OSTREE_VERSION: Final = (
    "feature.node.kubernetes.io/system-os_release.OSTREE_VERSION"
)
LABEL_GPU_PRESENT: Final = "nvidia.com/gpu.present"
LABEL_GPU_PRODUCT: Final = "nvidia.com/gpu.product"
LABEL_CUDA_DRIVER_FULL: Final = "nvidia.com/cuda.driver-version.full"
LABEL_CUDA_RUNTIME_FULL: Final = "nvidia.com/cuda.runtime-version.full"
LABEL_UPGRADE_STATE: Final = "nvidia.com/gpu-driver-upgrade-state"
LABEL_UPGRADE_SKIP: Final = "nvidia.com/gpu-driver-upgrade.skip"
LABEL_DEPLOY_OPERANDS: Final = "nvidia.com/gpu.deploy.operands"
LABEL_DEPLOY_DRIVER: Final = "nvidia.com/gpu.deploy.driver"
LABEL_MIG_CAPABLE: Final = "nvidia.com/mig.capable"
LABEL_MIG_CONFIG: Final = "nvidia.com/mig.config"
NVIDIA_GPU_RESOURCE: Final = "nvidia.com/gpu"
NVIDIA_DRA_DEVICE_CLASS: Final = "gpu.nvidia.com"

# Official NVIDIA / Red Hat OpenShift driver image repositories.
STOCK_DRIVER_REPOSITORIES: Final[frozenset[str]] = frozenset(
    {
        "nvcr.io/nvidia",
        "registry.redhat.io/nvidia",
    }
)

# Allocation modes.
ALLOCATION_DEVICE_PLUGIN: Final = "device-plugin"
ALLOCATION_DRA: Final = "dra"
ALLOCATION_CONFLICT: Final = "conflict"
ALLOCATION_NONE: Final = "none"

# Validator pod prefixes documented in NVIDIA OpenShift install verification.
COMPLETED_VALIDATOR_PREFIXES: Final[tuple[str, ...]] = (
    "nvidia-cuda-validator-",
    "nvidia-device-plugin-validator-",
)
RUNNING_VALIDATOR_PREFIXES: Final[tuple[str, ...]] = ("nvidia-operator-validator-",)
OPERATOR_CONTROLLER_PREFIX: Final = "gpu-operator-"

# Pause / resume scopes (DEC-CI17-19).
PAUSE_SCOPE_UPGRADE_SKIP: Final = "upgrade-skip"
PAUSE_SCOPE_OPERANDS: Final = "operands"
PAUSE_SCOPE_DRIVER: Final = "driver"
PAUSE_SCOPE_AUTO_UPGRADE: Final = "auto-upgrade"
PAUSE_SCOPE_MCP: Final = "mcp"
PAUSE_SCOPES: Final[tuple[str, ...]] = (
    PAUSE_SCOPE_UPGRADE_SKIP,
    PAUSE_SCOPE_OPERANDS,
    PAUSE_SCOPE_DRIVER,
    PAUSE_SCOPE_AUTO_UPGRADE,
    PAUSE_SCOPE_MCP,
)

# Machine-readable needs codes.
NEED_ALLOCATION_CONFLICT: Final = "gpu_allocation_mode_conflict"
NEED_TARGET_MISSING: Final = "gpu_pause_target_missing"
NEED_SCOPE_REQUIRED: Final = "gpu_pause_scope_required"
NEED_NODE_REQUIRED: Final = "gpu_node_required"
NEED_MCP_REQUIRED: Final = "gpu_mcp_required"
NEED_DRIVER_CR_REQUIRED: Final = "nvidia_driver_crd_required"
NEED_MCP_DEGRADED: Final = "machine_config_pool_degraded"

GPU_OPERATOR_MODE_POLICY: Final = ActionModePolicy(
    default_mode=ActionMode.LIVE_PLAN,
    receipt_modes=frozenset({ActionMode.READ, ActionMode.LIVE_PLAN, ActionMode.APPLY}),
    allow_offline_output=False,
    maximum_timeout=MAX_GPU_TIMEOUT,
)


@dataclass(frozen=True, slots=True)
class GPUNeed:
    """State one caller-suppliable decision or prerequisite blocker.

    .. attribute :: need
        :type: str

        Machine-readable identifier of the missing or conflicting condition.

    .. attribute :: evidence
        :type: str

        Cluster observation explaining why the operation cannot proceed.

    .. attribute :: supply
        :type: str

        Flag, prerequisite, or recovery step the caller supplies next.
    """

    need: str
    evidence: str
    supply: str


@dataclass(frozen=True, slots=True)
class DriverPoolInfo:
    """Summarize one ``NVIDIADriver`` custom resource on the cluster.

    .. attribute :: name
        :type: str

        ``NVIDIADriver`` resource name.

    .. attribute :: uid
        :type: str

        Kubernetes UID for guarded confirmation binding.

    .. attribute :: version
        :type: Optional[str]

        Configured driver image version tag.

    .. attribute :: repository
        :type: Optional[str]

        Configured container image repository.

    .. attribute :: image
        :type: Optional[str]

        Configured container image name.

    .. attribute :: kernel_module_type
        :type: Optional[str]

        Configured kernel module selection (``auto``, ``open``, ``proprietary``).

    .. attribute :: use_precompiled
        :type: Optional[bool]

        Whether precompiled driver containers are enabled.

    .. attribute :: node_selector
        :type: dict[str, str]

        Node selector labels governing which GPU nodes belong to this pool.

    .. attribute :: state
        :type: Optional[str]

        Observed ``status.state`` written by the operator.

    .. attribute :: non_standard_driver
        :type: bool

        True when the pool uses a non-stock repository or precompiled image.
    """

    name: str
    uid: str
    version: Optional[str]
    repository: Optional[str]
    image: Optional[str]
    kernel_module_type: Optional[str]
    use_precompiled: Optional[bool]
    node_selector: dict[str, str]
    state: Optional[str]
    non_standard_driver: bool


@dataclass(frozen=True, slots=True)
class GPUNodeInfo:
    """Summarize observed GPU, CUDA, driver, and pause state for one node.

    .. attribute :: name
        :type: str

        Kubernetes Node name.

    .. attribute :: gpu_present
        :type: bool

        Whether NVIDIA PCI or GPU presence labels are active on the node.

    .. attribute :: gpu_product
        :type: Optional[str]

        GFD GPU product label value.

    .. attribute :: allocatable_gpus
        :type: int

        Count of allocatable ``nvidia.com/gpu`` resources.

    .. attribute :: cuda_driver_version
        :type: Optional[str]

        Observed host CUDA driver version from GFD labels.

    .. attribute :: cuda_runtime_version
        :type: Optional[str]

        Observed host CUDA runtime ceiling from GFD labels.

    .. attribute :: upgrade_state
        :type: Optional[str]

        Observed ``nvidia.com/gpu-driver-upgrade-state`` label value.

    .. attribute :: upgrade_skip
        :type: bool

        True when ``nvidia.com/gpu-driver-upgrade.skip`` is ``true``.

    .. attribute :: operands_paused
        :type: bool

        True when ``nvidia.com/gpu.deploy.operands`` or ``driver`` is ``false``.

    .. attribute :: rhcos_kernel
        :type: Optional[str]

        Observed ``feature.node.kubernetes.io/kernel-version.full`` label.

    .. attribute :: ostree_version
        :type: Optional[str]

        Observed RHCOS ``OSTREE_VERSION`` label.

    .. attribute :: mig_config
        :type: Optional[str]

        Observed ``nvidia.com/mig.config`` label (read-only).

    .. attribute :: matched_driver_crs
        :type: tuple[str, ...]

        Names of ``NVIDIADriver`` CRs whose ``nodeSelector`` matches this node.

    .. attribute :: desired_driver_version
        :type: Optional[str]

        Expected driver version from the governing CR or ``ClusterPolicy``.

    .. attribute :: driver_drift
        :type: bool

        True when desired and observed driver versions are both known and differ.
    """

    name: str
    gpu_present: bool
    gpu_product: Optional[str]
    allocatable_gpus: int
    cuda_driver_version: Optional[str]
    cuda_runtime_version: Optional[str]
    upgrade_state: Optional[str]
    upgrade_skip: bool
    operands_paused: bool
    rhcos_kernel: Optional[str]
    ostree_version: Optional[str]
    mig_config: Optional[str]
    matched_driver_crs: tuple[str, ...]
    desired_driver_version: Optional[str]
    driver_drift: bool


@dataclass(frozen=True, slots=True)
class DaemonSetHealth:
    """Summarize rollout readiness for one GPU Operator DaemonSet.

    .. attribute :: name
        :type: str

        DaemonSet resource name.

    .. attribute :: desired
        :type: int

        Desired scheduled pods.

    .. attribute :: current
        :type: int

        Currently scheduled pods.

    .. attribute :: ready
        :type: int

        Ready pods.

    .. attribute :: updated
        :type: int

        Up-to-date scheduled pods.

    .. attribute :: available
        :type: int

        Available pods.

    .. attribute :: healthy
        :type: bool

        True when desired equals current, ready, updated, and available.
    """

    name: str
    desired: int
    current: int
    ready: int
    updated: int
    available: int
    healthy: bool


@dataclass(frozen=True, slots=True)
class PodHealth:
    """Summarize observed status of a controller, validator, or unhealthy pod.

    .. attribute :: name
        :type: str

        Pod resource name.

    .. attribute :: role
        :type: str

        Classification (``controller``, ``validator``, ``driver``, ``operand``).

    .. attribute :: phase
        :type: str

        Kubernetes pod phase.

    .. attribute :: ready
        :type: bool

        Whether all containers report ready or completed as expected.

    .. attribute :: reason
        :type: Optional[str]

        Waiting or terminated failure reason when unhealthy.
    """

    name: str
    role: str
    phase: str
    ready: bool
    reason: Optional[str]


@dataclass(frozen=True, slots=True)
class MachineConfigPoolInfo:
    """Summarize OpenShift ``MachineConfigPool`` convergence and pause state.

    .. attribute :: name
        :type: str

        MachineConfigPool name (for example ``worker``).

    .. attribute :: uid
        :type: str

        Kubernetes UID for guarded confirmation binding.

    .. attribute :: paused
        :type: bool

        Whether ``spec.paused`` is true.

    .. attribute :: updated
        :type: bool

        Whether the ``Updated`` condition is ``True``.

    .. attribute :: updating
        :type: bool

        Whether the ``Updating`` condition is ``True``.

    .. attribute :: degraded
        :type: bool

        Whether the ``Degraded`` condition is ``True``.

    .. attribute :: machine_count
        :type: int

        Total machines in the pool.

    .. attribute :: ready_machine_count
        :type: int

        Ready machines in the pool.
    """

    name: str
    uid: str
    paused: bool
    updated: bool
    updating: bool
    degraded: bool
    machine_count: int
    ready_machine_count: int


@dataclass(frozen=True, slots=True)
class PausePlan:
    """Hold one digest-bound pause or resume plan for ``NvidiaPauseOperator``.

    .. attribute :: action
        :type: str

        Requested direction (``pause`` or ``resume``).

    .. attribute :: scope
        :type: str

        Pause scope (``upgrade-skip``, ``operands``, ``driver``,
        ``auto-upgrade``, or ``mcp``).

    .. attribute :: resource
        :type: str

        Qualified Kubernetes resource name to mutate.

    .. attribute :: name
        :type: str

        Exact Kubernetes object name.

    .. attribute :: uid
        :type: str

        Kubernetes object UID bound into ``plan_digest``.

    .. attribute :: before
        :type: JSONObject

        Observed values before mutation.

    .. attribute :: after
        :type: JSONObject

        Desired values after mutation.

    .. attribute :: patch_kind
        :type: str

        Mutation mechanism (``node_label`` or ``json_patch``).

    .. attribute :: patch_payload
        :type: JSONObject

        Structured label map or JSON patch operations applied on confirmation.

    .. attribute :: no_op
        :type: bool

        True when the live object already matches the desired state.

    .. attribute :: server_dry_run_ok
        :type: bool

        True when ``--dry-run=server`` validated the planned change.
    """

    action: str
    scope: str
    resource: str
    name: str
    uid: str
    before: JSONObject
    after: JSONObject
    patch_kind: str
    patch_payload: JSONObject
    no_op: bool
    server_dry_run_ok: bool


@dataclass(frozen=True, slots=True)
class GPUOperatorAction(SkillAction):
    """Prepare stable inputs for one NVIDIA GPU Operator command invocation.

    .. attribute :: args
        :type: argparse.Namespace

        Parsed CLI arguments.

    .. attribute :: state
        :type: GPUOperatorState

        Shared mutable execution state passed across callbacks.

    .. attribute :: target
        :type: Target

        Resolved Kubernetes target.

    .. attribute :: operation
        :type: str

        Selected CLI verb (``health-check``, ``pause``, or ``resume``).

    .. attribute :: namespace
        :type: str

        NVIDIA GPU Operator namespace.

    .. attribute :: node
        :type: str

        Target node name or ``*`` for all GPU nodes.

    .. attribute :: scope
        :type: Optional[str]

        Selected pause/resume scope when running ``pause`` or ``resume``.

    .. attribute :: nvidia_driver
        :type: Optional[str]

        Optional ``NVIDIADriver`` CR name for ``auto-upgrade`` pause/resume.

    .. attribute :: mcp
        :type: Optional[str]

        Optional OpenShift ``MachineConfigPool`` name for ``mcp`` pause/resume.
    """

    args: argparse.Namespace
    state: GPUOperatorState
    target: Target
    operation: str
    namespace: str
    node: str = "*"
    scope: Optional[str] = None
    nvidia_driver: Optional[str] = None
    mcp: Optional[str] = None


@dataclass
class GPUOperatorState(DeliveryState):
    """Mutable typed state shared by ``Nvidia*`` callbacks in one workflow.

    .. attribute :: namespace
        :type: str

        NVIDIA GPU Operator namespace for the active invocation.

    .. attribute :: health
        :type: Optional[JSONObject]

        Latest structured health snapshot produced by ``NvidiaHealthCheck``.

    .. attribute :: pause_plan
        :type: Optional[PausePlan]

        Resolved pause or resume plan produced by ``NvidiaPauseOperator``.

    .. attribute :: needs
        :type: list[GPUNeed]

        Caller-suppliable needs or blockers discovered during resolution.

    .. attribute :: mutated
        :type: bool

        Whether the invocation mutated cluster state.
    """

    namespace: str = DEFAULT_GPU_NAMESPACE
    health: Optional[JSONObject] = None
    pause_plan: Optional[PausePlan] = None
    needs: list[GPUNeed] = field(default_factory=list)
    mutated: bool = False
