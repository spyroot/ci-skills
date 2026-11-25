"""Read-only Kubernetes and OpenShift discovery for NVIDIA GPU Operator (CI17).

Every read uses qualified resource names (DEC-CI17-11), handles absent optional
CRDs gracefully (such as ``GPUCluster``, ``NVIDIADriver``, DRA APIs, or
OpenShift MCO on non-OCP clusters), and surfaces non-standard/precompiled
driver and Secure Boot MOK signals (DEC-CI17-06).

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Optional

from lib.python.core.json_types import JSONObject, copy_json_object
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.kubernetes.nvidia_gpu.models import (
    ALLOCATION_CONFLICT,
    ALLOCATION_DEVICE_PLUGIN,
    ALLOCATION_DRA,
    ALLOCATION_NONE,
    CLUSTER_POLICIES,
    COMPLETED_VALIDATOR_PREFIXES,
    DAEMONSETS,
    DEVICE_CLASSES,
    GPU_CLUSTERS,
    LABEL_CUDA_DRIVER_FULL,
    LABEL_CUDA_RUNTIME_FULL,
    LABEL_DEPLOY_DRIVER,
    LABEL_DEPLOY_OPERANDS,
    LABEL_GPU_PRESENT,
    LABEL_GPU_PRODUCT,
    LABEL_KERNEL_FULL,
    LABEL_MIG_CONFIG,
    LABEL_OSTREE_VERSION,
    LABEL_PCI_NVIDIA,
    LABEL_UPGRADE_SKIP,
    LABEL_UPGRADE_STATE,
    MACHINE_CONFIG_POOLS,
    MACHINE_CONFIGS,
    NEED_ALLOCATION_CONFLICT,
    NODES,
    NVIDIA_DRA_DEVICE_CLASS,
    NVIDIA_DRIVERS,
    NVIDIA_GPU_RESOURCE,
    OPERATOR_CONTROLLER_PREFIX,
    PODS,
    RESOURCE_SLICES,
    RUNNING_VALIDATOR_PREFIXES,
    STOCK_DRIVER_REPOSITORIES,
    DaemonSetHealth,
    DriverPoolInfo,
    GPUNeed,
    GPUNodeInfo,
    MachineConfigPoolInfo,
    PodHealth,
)

NVIDIA_MC_KEYWORDS: tuple[str, ...] = ("nvidia", "gpu", "mok", "cdi", "iommu", "vfio")
DRIVER_ERROR_TOKENS: tuple[str, ...] = (
    "crashloopbackoff",
    "imagepullbackoff",
    "errimagepull",
    "drivernotready",
    "key was rejected",
    "module verification failed",
)


def _optional_list(
    service: KubernetesService,
    resource: str,
    namespace: Optional[str] = None,
) -> list[JSONObject]:
    """List one Kubernetes resource, returning empty when the API/CRD is absent.

    :param service: Verified Kubernetes service.
    :param resource: Qualified Kubernetes resource name.
    :param namespace: Optional namespace scope.
    :returns: List of object mappings, or empty when the CRD/API is absent.
    :raises KubernetesError: If an unexpected API or transport error occurs.
    """
    argv = ["get", resource, "-o", "json"]
    if namespace is not None:
        argv = ["get", resource, "-n", namespace, "-o", "json"]
    result = service.command(*argv)
    if result.returncode:
        detail = f"{result.stdout}\n{result.stderr}".casefold()
        if (
            "the server doesn't have a resource type" in detail
            or "not found" in detail
            or "404" in detail
        ):
            return []
        raise KubernetesError(f"k8s_list_failed:{resource}")
    try:
        document = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        raise KubernetesError(f"k8s_list_parse_failed:{resource}") from exc
    items = document.get("items", []) if isinstance(document, dict) else []
    return [item for item in items if isinstance(item, dict)]


def _list_items(
    service: KubernetesService,
    resource: str,
    namespace: Optional[str] = None,
) -> list[JSONObject]:
    """List one core Kubernetes resource required for GPU health inspection.

    :param service: Verified Kubernetes service.
    :param resource: Qualified Kubernetes resource name.
    :param namespace: Optional namespace scope.
    :returns: List of object mappings.
    """
    argv = (
        ["get", resource, "-n", namespace, "-o", "json"]
        if namespace is not None
        else ["get", resource, "-o", "json"]
    )
    document = service.json(*argv)
    items = document.get("items", []) if isinstance(document, dict) else []
    return [item for item in items if isinstance(item, dict)]


def _is_non_standard_driver(
    repository: Optional[str], use_precompiled: Optional[bool]
) -> bool:
    """Report whether driver settings use a non-stock repo or precompiled image.

    :param repository: Configured driver container repository.
    :param use_precompiled: Whether precompiled driver containers are enabled.
    :returns: True when repository is outside official stock repos or precompiled.
    """
    if use_precompiled is True:
        return True
    return bool(repository and repository.rstrip("/") not in STOCK_DRIVER_REPOSITORIES)


def _selector_matches(selector: Mapping[str, str], labels: Mapping[str, str]) -> bool:
    """Return whether every ``nodeSelector`` entry matches a node's labels.

    :param selector: ``NVIDIADriver.spec.nodeSelector`` key/value mapping.
    :param labels: Kubernetes Node labels.
    :returns: True when all selector entries match; an empty selector selects
        every node, matching the NVIDIA GPU Operator's documented behavior.
    """
    return all(labels.get(key) == value for key, value in selector.items())


def _parse_driver_pool(item: JSONObject) -> DriverPoolInfo:
    """Extract typed pool metadata from one ``NVIDIADriver`` custom resource.

    :param item: Raw ``NVIDIADriver`` Kubernetes object.
    :returns: Structured pool summary.
    """
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    raw_selector = spec.get("nodeSelector") or {}
    selector = {
        str(k): str(v)
        for k, v in raw_selector.items()
        if isinstance(raw_selector, dict)
    }
    repository = str(spec["repository"]) if spec.get("repository") else None
    use_precompiled = (
        bool(spec["usePrecompiled"])
        if isinstance(spec.get("usePrecompiled"), bool)
        else None
    )
    return DriverPoolInfo(
        name=str(metadata.get("name", "")),
        uid=str(metadata.get("uid", "")),
        version=str(spec["version"]) if spec.get("version") else None,
        repository=repository,
        image=str(spec["image"]) if spec.get("image") else None,
        kernel_module_type=(
            str(spec["kernelModuleType"]) if spec.get("kernelModuleType") else None
        ),
        use_precompiled=use_precompiled,
        node_selector=selector,
        state=str(status["state"]) if status.get("state") else None,
        non_standard_driver=_is_non_standard_driver(repository, use_precompiled),
    )


def _parse_cluster_policy(item: JSONObject) -> JSONObject:
    """Extract safe structured summary from one ``ClusterPolicy`` object.

    :param item: Raw ``ClusterPolicy`` Kubernetes object.
    :returns: JSON-safe policy summary.
    """
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    driver = spec.get("driver") if isinstance(spec.get("driver"), dict) else {}
    operator = spec.get("operator") if isinstance(spec.get("operator"), dict) else {}
    cdi = spec.get("cdi") if isinstance(spec.get("cdi"), dict) else {}
    toolkit = spec.get("toolkit") if isinstance(spec.get("toolkit"), dict) else {}
    gfd = spec.get("gfd") if isinstance(spec.get("gfd"), dict) else {}
    dcgm_exp = (
        spec.get("dcgmExporter") if isinstance(spec.get("dcgmExporter"), dict) else {}
    )
    repository = str(driver["repository"]) if driver.get("repository") else None
    use_precompiled = (
        bool(driver["usePrecompiled"])
        if isinstance(driver.get("usePrecompiled"), bool)
        else None
    )
    return {
        "name": str(metadata.get("name", "")),
        "uid": str(metadata.get("uid", "")),
        "state": str(status["state"]) if status.get("state") else None,
        "use_nvidia_driver_crd": bool(driver.get("useNvidiaDriverCRD", False)),
        "use_ocp_driver_toolkit": bool(operator.get("use_ocp_driver_toolkit", False)),
        "runtime_class": operator.get("runtimeClass"),
        "driver": {
            "enabled": bool(driver.get("enabled", False)),
            "repository": repository,
            "image": driver.get("image"),
            "version": driver.get("version"),
            "kernel_module_type": driver.get("kernelModuleType"),
            "use_precompiled": use_precompiled,
            "non_standard_driver": _is_non_standard_driver(repository, use_precompiled),
            "upgrade_policy": copy_json_object(driver.get("upgradePolicy") or {}),
        },
        "toolkit": {
            "enabled": bool(toolkit.get("enabled", False)),
            "version": toolkit.get("version"),
        },
        "cdi": {
            "enabled": bool(cdi.get("enabled", False)),
            "default": bool(cdi.get("default", False)),
        },
        "gfd": {
            "enabled": bool(gfd.get("enabled", False)),
            "version": gfd.get("version"),
        },
        "dcgm_exporter_service_monitor": bool(
            (dcgm_exp.get("serviceMonitor") or {}).get("enabled", False)
            if isinstance(dcgm_exp.get("serviceMonitor"), dict)
            else False
        ),
    }


def _parse_gpu_cluster(item: JSONObject) -> JSONObject:
    """Extract safe structured summary from one DRA ``GPUCluster`` object.

    :param item: Raw ``GPUCluster`` Kubernetes object.
    :returns: JSON-safe DRA cluster summary.
    """
    metadata = item.get("metadata") or {}
    status = item.get("status") or {}
    return {
        "name": str(metadata.get("name", "")),
        "uid": str(metadata.get("uid", "")),
        "state": str(status["state"]) if status.get("state") else None,
    }


def _parse_daemonsets(items: Sequence[JSONObject]) -> list[DaemonSetHealth]:
    """Summarize all DaemonSets in the GPU Operator namespace.

    :param items: Raw Kubernetes DaemonSet objects.
    :returns: Sorted DaemonSet health summaries.
    """
    results: list[DaemonSetHealth] = []
    for item in items:
        metadata = item.get("metadata") or {}
        status = item.get("status") or {}
        desired = int(status.get("desiredNumberScheduled", 0) or 0)
        current = int(status.get("currentNumberScheduled", 0) or 0)
        ready = int(status.get("numberReady", 0) or 0)
        updated = int(status.get("updatedNumberScheduled", 0) or 0)
        available = int(status.get("numberAvailable", 0) or 0)
        healthy = desired == current == ready == updated == available
        results.append(
            DaemonSetHealth(
                name=str(metadata.get("name", "")),
                desired=desired,
                current=current,
                ready=ready,
                updated=updated,
                available=available,
                healthy=healthy,
            )
        )
    return sorted(results, key=lambda entry: entry.name)


def _pod_failure_reason(pod: JSONObject) -> Optional[str]:
    """Extract the first waiting or terminated error reason from pod statuses.

    :param pod: Raw Kubernetes Pod object.
    :returns: Waiting/terminated reason string when unhealthy, else ``None``.
    """
    status = pod.get("status") or {}
    for bucket in ("initContainerStatuses", "containerStatuses"):
        for container in status.get(bucket) or []:
            if not isinstance(container, dict):
                continue
            state = container.get("state") or {}
            waiting = state.get("waiting")
            if isinstance(waiting, dict) and waiting.get("reason"):
                return str(waiting["reason"])
            terminated = state.get("terminated")
            if (
                isinstance(terminated, dict)
                and int(terminated.get("exitCode", 0) or 0) != 0
            ):
                return str(terminated.get("reason") or f"exit:{terminated['exitCode']}")
    return None


def _parse_pods(items: Sequence[JSONObject]) -> tuple[list[PodHealth], list[str]]:
    """Classify controller, validator, and unhealthy driver/operand pods.

    :param items: Raw Kubernetes Pod objects in the GPU Operator namespace.
    :returns: Tuple of summarized pods and detected non-standard/driver error findings.
    """
    summaries: list[PodHealth] = []
    driver_errors: list[str] = []
    for pod in items:
        metadata = pod.get("metadata") or {}
        status = pod.get("status") or {}
        name = str(metadata.get("name", ""))
        phase = str(status.get("phase", ""))
        reason = _pod_failure_reason(pod)
        containers = status.get("containerStatuses") or []
        all_ready = bool(containers) and all(
            bool(c.get("ready")) for c in containers if isinstance(c, dict)
        )
        if name.startswith(COMPLETED_VALIDATOR_PREFIXES):
            role = "validator"
            ready = phase == "Succeeded"
        elif name.startswith(RUNNING_VALIDATOR_PREFIXES):
            role = "validator"
            ready = phase == "Running" and all_ready
        elif name.startswith(OPERATOR_CONTROLLER_PREFIX):
            role = "controller"
            ready = phase == "Running" and all_ready
        elif "driver" in name:
            role = "driver"
            ready = phase == "Running" and all_ready
            if not ready:
                detail = reason or phase or "not_ready"
                driver_errors.append(f"{name}:{detail}")
        else:
            role = "operand"
            ready = (phase == "Running" and all_ready) or phase == "Succeeded"
        if role in {"controller", "validator"} or not ready:
            summaries.append(
                PodHealth(
                    name=name,
                    role=role,
                    phase=phase,
                    ready=ready,
                    reason=reason,
                )
            )
    return sorted(summaries, key=lambda entry: entry.name), sorted(driver_errors)


def _parse_mcps(items: Sequence[JSONObject]) -> list[MachineConfigPoolInfo]:
    """Summarize OpenShift ``MachineConfigPool`` pause and convergence state.

    :param items: Raw ``MachineConfigPool`` objects.
    :returns: Sorted MCP status summaries.
    """
    pools: list[MachineConfigPoolInfo] = []
    for item in items:
        metadata = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        conditions = {
            str(cond.get("type")): str(cond.get("status"))
            for cond in (status.get("conditions") or [])
            if isinstance(cond, dict)
        }
        pools.append(
            MachineConfigPoolInfo(
                name=str(metadata.get("name", "")),
                uid=str(metadata.get("uid", "")),
                paused=bool(spec.get("paused", False)),
                updated=conditions.get("Updated") == "True",
                updating=conditions.get("Updating") == "True",
                degraded=conditions.get("Degraded") == "True",
                machine_count=int(status.get("machineCount", 0) or 0),
                ready_machine_count=int(status.get("readyMachineCount", 0) or 0),
            )
        )
    return sorted(pools, key=lambda entry: entry.name)


def _node_allocatable_gpus(item: JSONObject) -> int:
    """Count allocatable ``nvidia.com/gpu`` resources on one Node object.

    :param item: Raw Kubernetes Node object.
    :returns: Integer allocatable GPU count, or zero when absent or invalid.
    """
    allocatable = (item.get("status") or {}).get("allocatable") or {}
    try:
        return int(str(allocatable.get(NVIDIA_GPU_RESOURCE, "0")))
    except ValueError:
        return 0


def _node_gpu_present(item: JSONObject) -> bool:
    """Report whether one Node object presents an NVIDIA GPU.

    :param item: Raw Kubernetes Node object.
    :returns: True when PCI/GPU presence labels or allocatable GPUs are observed.
    """
    metadata = item.get("metadata") or {}
    raw_labels = metadata.get("labels") or {}
    labels = {
        str(k): str(v) for k, v in raw_labels.items() if isinstance(raw_labels, dict)
    }
    return (
        labels.get(LABEL_PCI_NVIDIA) == "true"
        or labels.get(LABEL_GPU_PRESENT) == "true"
        or _node_allocatable_gpus(item) > 0
    )


def list_gpu_node_objects(service: KubernetesService) -> list[JSONObject]:
    """List raw Node objects that present NVIDIA GPUs.

    :param service: Verified Kubernetes service.
    :returns: Raw Node objects carrying NVIDIA GPU presence evidence.
    """
    return [item for item in _list_items(service, NODES) if _node_gpu_present(item)]


def _parse_gpu_nodes(
    items: Sequence[JSONObject],
    node_filter: str,
    cluster_policy: Optional[JSONObject],
    driver_pools: Sequence[DriverPoolInfo],
) -> list[GPUNodeInfo]:
    """Extract GPU, CUDA, drift, pause, and RHCOS kernel data for matching nodes.

    :param items: Raw Kubernetes Node objects.
    :param node_filter: ``*`` for all GPU nodes or an exact Node name.
    :param cluster_policy: Parsed ``ClusterPolicy`` summary, if present.
    :param driver_pools: Parsed ``NVIDIADriver`` pool summaries.
    :returns: Sorted list of GPU node summaries.
    """
    use_driver_crd = bool(
        cluster_policy.get("use_nvidia_driver_crd") if cluster_policy else False
    )
    policy_driver_version = (
        str((cluster_policy.get("driver") or {}).get("version"))
        if cluster_policy and (cluster_policy.get("driver") or {}).get("version")
        else None
    )
    pool_versions = {pool.name: pool.version for pool in driver_pools}
    nodes: list[GPUNodeInfo] = []
    for item in items:
        metadata = item.get("metadata") or {}
        name = str(metadata.get("name", ""))
        if node_filter != "*" and name != node_filter:
            continue
        raw_labels = metadata.get("labels") or {}
        labels = {
            str(k): str(v)
            for k, v in raw_labels.items()
            if isinstance(raw_labels, dict)
        }
        gpu_count = _node_allocatable_gpus(item)
        gpu_present = _node_gpu_present(item)
        if not gpu_present and node_filter == "*":
            continue
        matched = tuple(
            sorted(
                pool.name
                for pool in driver_pools
                if _selector_matches(pool.node_selector, labels)
            )
        )
        if use_driver_crd or (cluster_policy is None and driver_pools):
            desired = pool_versions.get(matched[0]) if len(matched) == 1 else None
        else:
            desired = policy_driver_version
        observed = labels.get(LABEL_CUDA_DRIVER_FULL)
        drift = bool(desired and observed and desired != observed)
        operands_paused = (
            labels.get(LABEL_DEPLOY_OPERANDS) == "false"
            or labels.get(LABEL_DEPLOY_DRIVER) == "false"
        )
        nodes.append(
            GPUNodeInfo(
                name=name,
                gpu_present=gpu_present,
                gpu_product=labels.get(LABEL_GPU_PRODUCT),
                allocatable_gpus=gpu_count,
                cuda_driver_version=observed,
                cuda_runtime_version=labels.get(LABEL_CUDA_RUNTIME_FULL),
                upgrade_state=labels.get(LABEL_UPGRADE_STATE),
                upgrade_skip=labels.get(LABEL_UPGRADE_SKIP) == "true",
                operands_paused=operands_paused,
                rhcos_kernel=labels.get(LABEL_KERNEL_FULL),
                ostree_version=labels.get(LABEL_OSTREE_VERSION),
                mig_config=labels.get(LABEL_MIG_CONFIG),
                matched_driver_crs=matched,
                desired_driver_version=desired,
                driver_drift=drift,
            )
        )
    return sorted(nodes, key=lambda entry: entry.name)


def collect_health_snapshot(
    service: KubernetesService,
    namespace: str,
    node_filter: str = "*",
) -> tuple[JSONObject, list[GPUNeed]]:
    """Collect a full read-only NVIDIA GPU Operator health snapshot on OpenShift.

    :param service: Verified Kubernetes service.
    :param namespace: NVIDIA GPU Operator namespace.
    :param node_filter: ``*`` for all GPU nodes or one exact Node name.
    :returns: Tuple of JSON-safe health snapshot and discovered ``GPUNeed`` items.
    """
    needs: list[GPUNeed] = []
    policies = _optional_list(service, CLUSTER_POLICIES)
    gpu_clusters = _optional_list(service, GPU_CLUSTERS)
    driver_items = _optional_list(service, NVIDIA_DRIVERS)
    driver_pools = sorted(
        (_parse_driver_pool(item) for item in driver_items),
        key=lambda pool: pool.name,
    )
    cluster_policy = _parse_cluster_policy(policies[0]) if policies else None
    gpu_cluster = _parse_gpu_cluster(gpu_clusters[0]) if gpu_clusters else None

    if cluster_policy is not None and gpu_cluster is not None:
        allocation_mode = ALLOCATION_CONFLICT
        needs.append(
            GPUNeed(
                need=NEED_ALLOCATION_CONFLICT,
                evidence="both ClusterPolicy and GPUCluster exist on the cluster",
                supply="remove either ClusterPolicy or GPUCluster; they are mutually exclusive",
            )
        )
    elif gpu_cluster is not None:
        allocation_mode = ALLOCATION_DRA
    elif cluster_policy is not None:
        allocation_mode = ALLOCATION_DEVICE_PLUGIN
    else:
        allocation_mode = ALLOCATION_NONE

    # DRA resources (DeviceClass and ResourceSlices) when available.
    device_classes = _optional_list(service, DEVICE_CLASSES)
    has_nvidia_device_class = any(
        (dc.get("metadata") or {}).get("name") == NVIDIA_DRA_DEVICE_CLASS
        for dc in device_classes
    )
    slices = _optional_list(service, RESOURCE_SLICES)
    dra_nodes = sorted(
        {
            str((sl.get("spec") or {}).get("nodeName"))
            for sl in slices
            if str((sl.get("spec") or {}).get("driver", "")).startswith("gpu.nvidia")
            and (sl.get("spec") or {}).get("nodeName")
        }
    )

    # OpenShift MachineConfigs and MachineConfigPools.
    raw_mcs = _optional_list(service, MACHINE_CONFIGS)
    nvidia_mcs = sorted(
        str((mc.get("metadata") or {}).get("name", ""))
        for mc in raw_mcs
        if any(
            kw in str((mc.get("metadata") or {}).get("name", "")).casefold()
            for kw in NVIDIA_MC_KEYWORDS
        )
    )
    mcps = _parse_mcps(_optional_list(service, MACHINE_CONFIG_POOLS))

    # Workloads in GPU Operator namespace.
    daemonsets = _parse_daemonsets(_optional_list(service, DAEMONSETS, namespace))
    pods, driver_pod_errors = _parse_pods(_optional_list(service, PODS, namespace))

    # GPU nodes + driver drift + overlap detection.
    node_items = _list_items(service, NODES)
    gpu_nodes = _parse_gpu_nodes(node_items, node_filter, cluster_policy, driver_pools)

    findings: list[str] = []
    if cluster_policy and (cluster_policy.get("driver") or {}).get(
        "non_standard_driver"
    ):
        findings.append("cluster_policy_non_standard_driver")
    for pool in driver_pools:
        if pool.non_standard_driver:
            findings.append(f"non_standard_driver_pool:{pool.name}")
    for mc_name in nvidia_mcs:
        if "mok" in mc_name.casefold():
            findings.append(f"secure_boot_mok_machine_config:{mc_name}")
    for pool in mcps:
        if pool.paused:
            findings.append(f"mcp_paused:{pool.name}")
        if pool.degraded:
            findings.append(f"mcp_degraded:{pool.name}")
        if pool.updating:
            findings.append(f"mcp_updating:{pool.name}")
    for ds in daemonsets:
        if not ds.healthy:
            findings.append(f"daemonset_unhealthy:{ds.name}")
    for err in driver_pod_errors:
        findings.append(f"driver_pod_unhealthy:{err}")
    for node in gpu_nodes:
        if node.driver_drift:
            findings.append(
                f"driver_drift:{node.name}:desired={node.desired_driver_version}:observed={node.cuda_driver_version}"
            )
        if node.upgrade_state == "upgrade-failed":
            findings.append(f"upgrade_failed:{node.name}")
        if node.upgrade_skip:
            findings.append(f"upgrade_skip:{node.name}")
        if node.operands_paused:
            findings.append(f"operands_paused:{node.name}")
        if len(node.matched_driver_crs) > 1:
            findings.append(
                f"driver_selector_overlap:{node.name}:{','.join(node.matched_driver_crs)}"
            )
        if (
            cluster_policy
            and cluster_policy.get("use_nvidia_driver_crd")
            and node.gpu_present
            and not node.matched_driver_crs
        ):
            findings.append(f"driver_cr_unmatched:{node.name}")

    snapshot: JSONObject = {
        "namespace": namespace,
        "node_filter": node_filter,
        "allocation_mode": allocation_mode,
        "cluster_policy": cluster_policy,
        "gpu_cluster": gpu_cluster,
        "nvidia_drivers": [
            {
                "name": pool.name,
                "uid": pool.uid,
                "version": pool.version,
                "repository": pool.repository,
                "image": pool.image,
                "kernel_module_type": pool.kernel_module_type,
                "use_precompiled": pool.use_precompiled,
                "node_selector": pool.node_selector,
                "state": pool.state,
                "non_standard_driver": pool.non_standard_driver,
            }
            for pool in driver_pools
        ],
        "dra": {
            "device_class_present": has_nvidia_device_class,
            "resource_slice_nodes": dra_nodes,
        },
        "machine_configs": nvidia_mcs,
        "machine_config_pools": [
            {
                "name": pool.name,
                "uid": pool.uid,
                "paused": pool.paused,
                "updated": pool.updated,
                "updating": pool.updating,
                "degraded": pool.degraded,
                "machine_count": pool.machine_count,
                "ready_machine_count": pool.ready_machine_count,
            }
            for pool in mcps
        ],
        "daemonsets": [
            {
                "name": ds.name,
                "desired": ds.desired,
                "current": ds.current,
                "ready": ds.ready,
                "updated": ds.updated,
                "available": ds.available,
                "healthy": ds.healthy,
            }
            for ds in daemonsets
        ],
        "pods": [
            {
                "name": pod.name,
                "role": pod.role,
                "phase": pod.phase,
                "ready": pod.ready,
                "reason": pod.reason,
            }
            for pod in pods
        ],
        "nodes": [
            {
                "name": node.name,
                "gpu_present": node.gpu_present,
                "gpu_product": node.gpu_product,
                "allocatable_gpus": node.allocatable_gpus,
                "cuda_driver_version": node.cuda_driver_version,
                "cuda_runtime_version": node.cuda_runtime_version,
                "upgrade_state": node.upgrade_state,
                "upgrade_skip": node.upgrade_skip,
                "operands_paused": node.operands_paused,
                "rhcos_kernel": node.rhcos_kernel,
                "ostree_version": node.ostree_version,
                "mig_config": node.mig_config,
                "matched_driver_crs": list(node.matched_driver_crs),
                "desired_driver_version": node.desired_driver_version,
                "driver_drift": node.driver_drift,
            }
            for node in gpu_nodes
        ],
        "findings": sorted(findings),
    }
    return snapshot, needs
