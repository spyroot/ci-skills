"""Unit tests for CI17-M1 NVIDIA GPU Operator callbacks (NvidiaHealthCheck, NvidiaPauseOperator).

All Kubernetes reads and writes run against an in-memory fake service; no test
touches a live cluster.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPO_ROOT / "ci-skills"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from lib.python.core.action_modes import ActionMode
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.nvidia_gpu.callbacks import (
    NvidiaHealthCheck,
    NvidiaPauseOperator,
)
from lib.python.core.kubernetes.nvidia_gpu.cli import (
    build_parser,
    resolve_gpu_mode,
)
from lib.python.core.kubernetes.nvidia_gpu.discovery import (
    collect_health_snapshot,
)
from lib.python.core.kubernetes.nvidia_gpu.models import (
    ALLOCATION_CONFLICT,
    ALLOCATION_DEVICE_PLUGIN,
    ALLOCATION_DRA,
    CLUSTER_POLICIES,
    DAEMONSETS,
    DEFAULT_GPU_NAMESPACE,
    DEVICE_CLASSES,
    GPU_CLUSTERS,
    LABEL_CUDA_DRIVER_FULL,
    LABEL_CUDA_RUNTIME_FULL,
    LABEL_DEPLOY_DRIVER,
    LABEL_DEPLOY_OPERANDS,
    LABEL_GPU_PRESENT,
    LABEL_GPU_PRODUCT,
    LABEL_KERNEL_FULL,
    LABEL_OSTREE_VERSION,
    LABEL_PCI_NVIDIA,
    LABEL_UPGRADE_SKIP,
    LABEL_UPGRADE_STATE,
    MACHINE_CONFIG_POOLS,
    MACHINE_CONFIGS,
    NEED_ALLOCATION_CONFLICT,
    NEED_DRIVER_CR_REQUIRED,
    NEED_MCP_DEGRADED,
    NEED_MCP_REQUIRED,
    NEED_NODE_REQUIRED,
    NODES,
    NVIDIA_DRA_DEVICE_CLASS,
    NVIDIA_DRIVERS,
    NVIDIA_GPU_RESOURCE,
    PAUSE_SCOPE_AUTO_UPGRADE,
    PAUSE_SCOPE_DRIVER,
    PAUSE_SCOPE_MCP,
    PAUSE_SCOPE_OPERANDS,
    PAUSE_SCOPE_UPGRADE_SKIP,
    PODS,
    RESOURCE_SLICES,
    GPUOperatorAction,
    GPUOperatorState,
)
from lib.python.core.kubernetes.nvidia_gpu.preflight import (
    CONFIRM_MISMATCH,
    build_pause_plan,
    pause_plan_evidence,
)
from lib.python.core.report import plan_digest


class FakeResult:
    """Minimal command result stand-in."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        """Store exit status and stream text."""
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeK8SService:
    """In-memory Kubernetes service covering qualified reads, dry-runs, and writes."""

    def __init__(self) -> None:
        """Initialize empty cluster resource tables."""
        self.resources: dict[str, dict[str, dict[str, Any]]] = {
            CLUSTER_POLICIES: {},
            GPU_CLUSTERS: {},
            NVIDIA_DRIVERS: {},
            DEVICE_CLASSES: {},
            RESOURCE_SLICES: {},
            MACHINE_CONFIGS: {},
            MACHINE_CONFIG_POOLS: {},
            DAEMONSETS: {},
            PODS: {},
            NODES: {},
        }
        self.missing_apis: set[str] = set()
        self.mutations: list[tuple[str, ...]] = []
        self.dry_run_reject: set[str] = set()

    def seed(self, resource: str, obj: dict[str, Any]) -> None:
        """Insert one object into the in-memory cluster table."""
        name = obj["metadata"]["name"]
        self.resources.setdefault(resource, {})[name] = obj

    def command(self, *args: str, **_: Any) -> FakeResult:
        """Handle bounded kubectl get, label, and patch calls."""
        verb = args[0]
        if verb == "get":
            return self._handle_get(args)
        if verb == "label":
            return self._handle_label(args)
        if verb == "patch":
            return self._handle_patch(args)
        return FakeResult(1, stderr=f"unsupported verb {verb}")

    def json(self, *args: str, **_: Any) -> dict[str, Any]:
        """Return parsed JSON for one ``kubectl get`` call."""
        res = self._handle_get(args)
        if res.returncode:
            raise KubernetesError(res.stderr)
        return json.loads(res.stdout)

    def _handle_get(self, args: tuple[str, ...]) -> FakeResult:
        resource = args[1]
        if resource in self.missing_apis:
            return FakeResult(
                1,
                stderr=f'error: the server doesn\'t have a resource type "{resource}"',
            )
        positional = [
            a
            for a in args[2:]
            if not a.startswith("-") and a not in {"json", DEFAULT_GPU_NAMESPACE}
        ]
        table = self.resources.get(resource, {})
        if positional:
            name = positional[0]
            obj = table.get(name)
            if obj is None:
                return FakeResult(1, stderr=f"{resource} {name!r} not found")
            return FakeResult(0, stdout=json.dumps(obj))
        return FakeResult(0, stdout=json.dumps({"items": list(table.values())}))

    def _handle_label(self, args: tuple[str, ...]) -> FakeResult:
        resource = args[1]
        name = args[2]
        label_expr = args[3]
        dry_run = "--dry-run=server" in args
        if f"{resource}/{name}" in self.dry_run_reject:
            return FakeResult(1, stderr="rejected by server dry-run")
        obj = self.resources.get(resource, {}).get(name)
        if obj is None:
            return FakeResult(1, stderr="not found")
        if dry_run:
            return FakeResult(0, stdout=json.dumps(obj))
        labels = obj.setdefault("metadata", {}).setdefault("labels", {})
        if label_expr.endswith("-"):
            labels.pop(label_expr[:-1], None)
        else:
            key, _, value = label_expr.partition("=")
            labels[key] = value
        self.mutations.append(args)
        return FakeResult(0, stdout=json.dumps(obj))

    def _handle_patch(self, args: tuple[str, ...]) -> FakeResult:
        resource = args[1]
        name = args[2]
        dry_run = "--dry-run=server" in args
        if f"{resource}/{name}" in self.dry_run_reject:
            return FakeResult(1, stderr="rejected by server dry-run")
        obj = self.resources.get(resource, {}).get(name)
        if obj is None:
            return FakeResult(1, stderr="not found")
        patch_idx = args.index("--patch") + 1
        ops = json.loads(args[patch_idx])
        if dry_run:
            return FakeResult(0, stdout=json.dumps(obj))
        for op in ops:
            parts = [p for p in op["path"].split("/") if p]
            cursor = obj
            for part in parts[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[parts[-1]] = op["value"]
        self.mutations.append(args)
        return FakeResult(0, stdout=json.dumps(obj))


def _run_cb(callback_cls: type[Any], action: GPUOperatorAction) -> dict[str, Any]:
    cb = callback_cls(action)
    cb.state = action.state
    return cb.run()


def _make_action(
    operation: str,
    *,
    node: str = "*",
    scope: str | None = None,
    nvidia_driver: str | None = None,
    mcp: str | None = None,
    mode: ActionMode = ActionMode.LIVE_PLAN,
    confirm_plan: str | None = None,
) -> GPUOperatorAction:
    args = Namespace(
        operation=operation,
        namespace=DEFAULT_GPU_NAMESPACE,
        node=node,
        scope=scope,
        nvidia_driver=nvidia_driver,
        mcp=mcp,
        timeout=300,
        confirm_plan=confirm_plan,
        _resolved_mode=mode,
    )
    state = GPUOperatorState(namespace=DEFAULT_GPU_NAMESPACE)
    return GPUOperatorAction(
        args=args,
        state=state,
        target=None,  # type: ignore[arg-type]
        operation=operation,
        namespace=DEFAULT_GPU_NAMESPACE,
        node=node,
        scope=scope,
        nvidia_driver=nvidia_driver,
        mcp=mcp,
    )


def _seed_standard_cluster(service: FakeK8SService) -> None:
    service.seed(
        CLUSTER_POLICIES,
        {
            "metadata": {"name": "gpu-cluster-policy", "uid": "cp-uid-1"},
            "spec": {
                "driver": {
                    "enabled": True,
                    "repository": "nvcr.io/nvidia",
                    "image": "driver",
                    "version": "570.158.01",
                    "useNvidiaDriverCRD": True,
                    "usePrecompiled": False,
                    "kernelModuleType": "auto",
                    "upgradePolicy": {"autoUpgrade": True, "maxParallelUpgrades": 1},
                },
                "operator": {"use_ocp_driver_toolkit": True, "runtimeClass": "nvidia"},
                "toolkit": {"enabled": True},
                "cdi": {"enabled": True, "default": False},
                "gfd": {"enabled": True},
                "dcgmExporter": {"serviceMonitor": {"enabled": True}},
            },
            "status": {"state": "ready"},
        },
    )
    service.seed(
        NVIDIA_DRIVERS,
        {
            "metadata": {"name": "pool-stock", "uid": "drv-uid-1"},
            "spec": {
                "repository": "nvcr.io/nvidia",
                "image": "driver",
                "version": "580.159.03",
                "usePrecompiled": False,
                "kernelModuleType": "auto",
                "nodeSelector": {"pool": "stock"},
                "upgradePolicy": {"autoUpgrade": True},
            },
            "status": {"state": "ready"},
        },
    )
    service.seed(
        NVIDIA_DRIVERS,
        {
            "metadata": {"name": "pool-custom-precompiled", "uid": "drv-uid-2"},
            "spec": {
                "repository": "quay.io/example/custom-driver",
                "image": "gpu-precompiled",
                "version": "570.158.01",
                "usePrecompiled": True,
                "kernelModuleType": "auto",
                "nodeSelector": {"pool": "edge"},
            },
            "status": {"state": "ready"},
        },
    )
    service.seed(
        MACHINE_CONFIGS,
        {"metadata": {"name": "99-worker-nvidia-mok-key"}},
    )
    service.seed(
        MACHINE_CONFIG_POOLS,
        {
            "metadata": {"name": "worker", "uid": "mcp-uid-1"},
            "spec": {"paused": False},
            "status": {
                "machineCount": 2,
                "readyMachineCount": 2,
                "conditions": [
                    {"type": "Updated", "status": "True"},
                    {"type": "Updating", "status": "False"},
                    {"type": "Degraded", "status": "False"},
                ],
            },
        },
    )
    service.seed(
        DAEMONSETS,
        {
            "metadata": {"name": "nvidia-mig-manager"},
            "status": {
                "desiredNumberScheduled": 0,
                "currentNumberScheduled": 0,
                "numberReady": 0,
                "updatedNumberScheduled": 0,
                "numberAvailable": 0,
            },
        },
    )
    service.seed(
        PODS,
        {
            "metadata": {"name": "nvidia-cuda-validator-abc"},
            "status": {"phase": "Succeeded", "containerStatuses": [{"ready": False}]},
        },
    )
    service.seed(
        PODS,
        {
            "metadata": {"name": "nvidia-operator-validator-xyz"},
            "status": {"phase": "Running", "containerStatuses": [{"ready": True}]},
        },
    )
    service.seed(
        NODES,
        {
            "metadata": {
                "name": "gpu-worker-1",
                "uid": "node-uid-1",
                "labels": {
                    LABEL_PCI_NVIDIA: "true",
                    LABEL_GPU_PRESENT: "true",
                    LABEL_GPU_PRODUCT: "NVIDIA-H100-NVL",
                    LABEL_CUDA_DRIVER_FULL: "570.211.01",
                    LABEL_CUDA_RUNTIME_FULL: "12.8",
                    LABEL_UPGRADE_STATE: "upgrade-done",
                    LABEL_KERNEL_FULL: "5.14.0-427.el9.x86_64",
                    LABEL_OSTREE_VERSION: "419.94.20260101",
                    "pool": "stock",
                },
            },
            "status": {"allocatable": {NVIDIA_GPU_RESOURCE: "4"}},
        },
    )
    service.seed(
        NODES,
        {
            "metadata": {
                "name": "gpu-edge-1",
                "uid": "node-uid-2",
                "labels": {
                    LABEL_PCI_NVIDIA: "true",
                    LABEL_GPU_PRESENT: "true",
                    LABEL_GPU_PRODUCT: "NVIDIA-L4",
                    LABEL_CUDA_DRIVER_FULL: "570.158.01",
                    LABEL_CUDA_RUNTIME_FULL: "12.8",
                    LABEL_UPGRADE_STATE: "upgrade-failed",
                    LABEL_UPGRADE_SKIP: "true",
                    "pool": "edge",
                },
            },
            "status": {"allocatable": {NVIDIA_GPU_RESOURCE: "1"}},
        },
    )


def test_health_check_surfaces_drift_non_standard_mok_and_skip() -> None:
    """NvidiaHealthCheck reports drift, non-standard pool, MOK MC, and skip/failed nodes."""
    service = FakeK8SService()
    _seed_standard_cluster(service)

    snapshot, needs = collect_health_snapshot(service, DEFAULT_GPU_NAMESPACE)
    assert needs == []
    assert snapshot["allocation_mode"] == ALLOCATION_DEVICE_PLUGIN
    assert snapshot["daemonsets"][0]["healthy"] is True
    assert all(p["ready"] for p in snapshot["pods"])

    findings = set(snapshot["findings"])
    assert "non_standard_driver_pool:pool-custom-precompiled" in findings
    assert "secure_boot_mok_machine_config:99-worker-nvidia-mok-key" in findings
    assert (
        "driver_drift:gpu-worker-1:desired=580.159.03:observed=570.211.01" in findings
    )
    assert "upgrade_failed:gpu-edge-1" in findings
    assert "upgrade_skip:gpu-edge-1" in findings


def test_health_check_dra_mode_and_conflict_detection() -> None:
    """NvidiaHealthCheck classifies DRA mode and blocks on ClusterPolicy+GPUCluster conflict."""
    service = FakeK8SService()
    service.seed(
        GPU_CLUSTERS,
        {
            "metadata": {"name": "gpu-cluster", "uid": "gc-uid-1"},
            "status": {"state": "ready"},
        },
    )
    service.seed(DEVICE_CLASSES, {"metadata": {"name": NVIDIA_DRA_DEVICE_CLASS}})
    service.seed(
        RESOURCE_SLICES,
        {
            "metadata": {"name": "slice-1"},
            "spec": {"driver": "gpu.nvidia.com", "nodeName": "gpu-worker-1"},
        },
    )
    snapshot, needs = collect_health_snapshot(service, DEFAULT_GPU_NAMESPACE)
    assert snapshot["allocation_mode"] == ALLOCATION_DRA
    assert snapshot["dra"]["device_class_present"] is True
    assert snapshot["dra"]["resource_slice_nodes"] == ["gpu-worker-1"]
    assert needs == []

    # Adding ClusterPolicy alongside GPUCluster triggers mutual-exclusion need.
    service.seed(
        CLUSTER_POLICIES,
        {"metadata": {"name": "gpu-cluster-policy", "uid": "cp-uid-1"}, "spec": {}},
    )
    conflict_snapshot, conflict_needs = collect_health_snapshot(
        service, DEFAULT_GPU_NAMESPACE
    )
    assert conflict_snapshot["allocation_mode"] == ALLOCATION_CONFLICT
    assert [n.need for n in conflict_needs] == [NEED_ALLOCATION_CONFLICT]


def test_health_check_callback_populates_state_and_survives_absent_optional_crds() -> (
    None
):
    """NvidiaHealthCheck handles absent DRA/MCO/GPUCluster APIs cleanly."""
    service = FakeK8SService()
    service.missing_apis.update(
        {GPU_CLUSTERS, DEVICE_CLASSES, RESOURCE_SLICES, MACHINE_CONFIG_POOLS}
    )
    action = _make_action("health-check")
    action.state.kubernetes = service

    result = _run_cb(NvidiaHealthCheck, action)
    assert result["verified"] is True
    assert action.state.health is not None
    assert action.state.health["machine_config_pools"] == []


@pytest.mark.parametrize(
    ("scope", "label_key", "pause_val", "resume_val"),
    [
        (PAUSE_SCOPE_UPGRADE_SKIP, LABEL_UPGRADE_SKIP, "true", None),
        (PAUSE_SCOPE_OPERANDS, LABEL_DEPLOY_OPERANDS, "false", None),
        (PAUSE_SCOPE_DRIVER, LABEL_DEPLOY_DRIVER, "false", "true"),
    ],
)
def test_pause_operator_node_scopes_plan_apply_noop_and_resume(
    scope: str,
    label_key: str,
    pause_val: str,
    resume_val: str | None,
) -> None:
    """NvidiaPauseOperator supports live-plan, digest verify, apply, NO_OP, and resume."""
    service = FakeK8SService()
    _seed_standard_cluster(service)

    # Plan pause on gpu-worker-1
    plan_action = _make_action("pause", node="gpu-worker-1", scope=scope)
    plan_action.state.kubernetes = service
    planned = _run_cb(NvidiaPauseOperator, plan_action)
    assert planned["action"] == "PLANNED"
    assert service.mutations == []
    digest = planned["plan_digest"]

    # Wrong digest must fail closed without writing
    bad_apply = _make_action(
        "pause",
        node="gpu-worker-1",
        scope=scope,
        mode=ActionMode.APPLY,
        confirm_plan="0" * 64,
    )
    bad_apply.state.kubernetes = service
    with pytest.raises(ValueError, match=CONFIRM_MISMATCH):
        _run_cb(NvidiaPauseOperator, bad_apply)
    assert service.mutations == []

    # Confirmed apply writes and reads back
    apply_action = _make_action(
        "pause",
        node="gpu-worker-1",
        scope=scope,
        mode=ActionMode.APPLY,
        confirm_plan=digest,
    )
    apply_action.state.kubernetes = service
    applied = _run_cb(NvidiaPauseOperator, apply_action)
    assert applied["action"] == "APPLIED"
    assert applied["mutated"] is True
    assert applied["observed"] == {label_key: pause_val}

    # Repeat pause is NO_OP
    repeat_action = _make_action("pause", node="gpu-worker-1", scope=scope)
    repeat_action.state.kubernetes = service
    repeat_res = _run_cb(NvidiaPauseOperator, repeat_action)
    assert repeat_res["action"] == "NO_OP"

    # Resume back to desired value
    res_plan_action = _make_action("resume", node="gpu-worker-1", scope=scope)
    res_plan_action.state.kubernetes = service
    res_planned = _run_cb(NvidiaPauseOperator, res_plan_action)
    res_apply = _make_action(
        "resume",
        node="gpu-worker-1",
        scope=scope,
        mode=ActionMode.APPLY,
        confirm_plan=res_planned["plan_digest"],
    )
    res_apply.state.kubernetes = service
    resumed = _run_cb(NvidiaPauseOperator, res_apply)
    assert resumed["action"] == "APPLIED"
    assert resumed["observed"] == {label_key: resume_val}


def test_pause_operator_auto_upgrade_guards_nvidia_driver_crd() -> None:
    """auto-upgrade scope blocks on useNvidiaDriverCRD=true without --nvidia-driver."""
    service = FakeK8SService()
    _seed_standard_cluster(service)

    missing_cr_action = _make_action("pause", scope=PAUSE_SCOPE_AUTO_UPGRADE)
    plan, needs = build_pause_plan(missing_cr_action, service)
    assert plan is None
    assert [n.need for n in needs] == [NEED_DRIVER_CR_REQUIRED]

    # Targeting named NVIDIADriver CR succeeds and applies cleanly
    cr_action = _make_action(
        "pause", scope=PAUSE_SCOPE_AUTO_UPGRADE, nvidia_driver="pool-stock"
    )
    cr_plan, cr_needs = build_pause_plan(cr_action, service)
    assert cr_needs == []
    assert cr_plan is not None
    assert cr_plan.before == {"autoUpgrade": True}
    assert cr_plan.after == {"autoUpgrade": False}

    apply_action = _make_action(
        "pause",
        scope=PAUSE_SCOPE_AUTO_UPGRADE,
        nvidia_driver="pool-stock",
        mode=ActionMode.APPLY,
        confirm_plan=plan_digest(pause_plan_evidence(cr_plan)),
    )
    apply_action.state.kubernetes = service
    res = _run_cb(NvidiaPauseOperator, apply_action)
    assert res["action"] == "APPLIED"
    assert res["observed"] == {"autoUpgrade": False}


def test_pause_operator_mcp_blocks_degraded_and_applies_cleanly() -> None:
    """mcp scope blocks missing --mcp or Degraded=True MCP, and pauses/resumes healthy MCP."""
    service = FakeK8SService()
    _seed_standard_cluster(service)

    no_mcp = _make_action("pause", scope=PAUSE_SCOPE_MCP)
    plan, needs = build_pause_plan(no_mcp, service)
    assert plan is None
    assert [n.need for n in needs] == [NEED_MCP_REQUIRED]

    # Healthy worker MCP pause & resume
    mcp_plan_action = _make_action("pause", scope=PAUSE_SCOPE_MCP, mcp="worker")
    mcp_plan, mcp_needs = build_pause_plan(mcp_plan_action, service)
    assert mcp_needs == []
    assert mcp_plan is not None
    assert mcp_plan.before == {"paused": False}
    assert mcp_plan.after == {"paused": True}

    apply_action = _make_action(
        "pause",
        scope=PAUSE_SCOPE_MCP,
        mcp="worker",
        mode=ActionMode.APPLY,
        confirm_plan=plan_digest(pause_plan_evidence(mcp_plan)),
    )
    apply_action.state.kubernetes = service
    res = _run_cb(NvidiaPauseOperator, apply_action)
    assert res["action"] == "APPLIED"
    assert res["observed"] == {"paused": True}

    # Degraded MCP blocks
    service.resources[MACHINE_CONFIG_POOLS]["worker"]["status"]["conditions"][2][
        "status"
    ] = "True"
    deg_plan, deg_needs = build_pause_plan(
        _make_action("resume", scope=PAUSE_SCOPE_MCP, mcp="worker"), service
    )
    assert deg_plan is None
    assert [n.need for n in deg_needs] == [NEED_MCP_DEGRADED]


def test_pause_operator_node_scope_requires_explicit_node() -> None:
    """Node-level pause scopes block when --node is wildcard '*'."""
    service = FakeK8SService()
    _seed_standard_cluster(service)
    plan, needs = build_pause_plan(
        _make_action("pause", node="*", scope=PAUSE_SCOPE_UPGRADE_SKIP), service
    )
    assert plan is None
    assert [n.need for n in needs] == [NEED_NODE_REQUIRED]


def test_cli_mode_resolution() -> None:
    """CLI defaults health-check to READ and pause/resume to LIVE_PLAN."""
    parser = build_parser()
    assert resolve_gpu_mode(parser.parse_args(["health-check"])) == ActionMode.READ
    assert (
        resolve_gpu_mode(
            parser.parse_args(["pause", "--scope", "mcp", "--mcp", "worker"])
        )
        == ActionMode.LIVE_PLAN
    )
    assert (
        resolve_gpu_mode(parser.parse_args(["pause", "--dry-run"]))
        == ActionMode.OFFLINE
    )
