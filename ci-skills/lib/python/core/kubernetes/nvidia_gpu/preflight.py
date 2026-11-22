"""Preflight, server-side dry-run, digest confirmation, and write helpers (CI17).

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from typing import Final, Optional

from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.kubernetes.nvidia_gpu.models import (
    CLUSTER_POLICIES,
    LABEL_DEPLOY_DRIVER,
    LABEL_DEPLOY_OPERANDS,
    LABEL_UPGRADE_SKIP,
    MACHINE_CONFIG_POOLS,
    NEED_DRIVER_CR_REQUIRED,
    NEED_MCP_DEGRADED,
    NEED_MCP_REQUIRED,
    NEED_NODE_REQUIRED,
    NEED_SCOPE_REQUIRED,
    NEED_TARGET_MISSING,
    NODES,
    NVIDIA_DRIVERS,
    PAUSE_SCOPE_AUTO_UPGRADE,
    PAUSE_SCOPE_DRIVER,
    PAUSE_SCOPE_MCP,
    PAUSE_SCOPE_OPERANDS,
    PAUSE_SCOPE_UPGRADE_SKIP,
    PAUSE_SCOPES,
    GPUNeed,
    GPUOperatorAction,
    PausePlan,
)
from lib.python.core.report import plan_digest

CONFIRM_MISMATCH: Final = "confirm_plan_mismatch_rerun_live_plan"
DRY_RUN_FAILED: Final = "server_dry_run_failed"
PATCH_KIND_LABEL: Final = "node_label"
PATCH_KIND_JSON: Final = "json_patch"


def _get_object(
    service: KubernetesService,
    resource: str,
    name: str,
) -> Optional[JSONObject]:
    """Fetch one cluster-scoped Kubernetes object by qualified resource and name.

    :param service: Verified Kubernetes service.
    :param resource: Qualified Kubernetes resource name.
    :param name: Exact object name.
    :returns: Parsed object mapping, or ``None`` when absent.
    :raises KubernetesError: If the API call fails with an unexpected error.
    """
    result = service.command("get", resource, name, "-o", "json")
    if result.returncode:
        detail = f"{result.stdout}\n{result.stderr}".casefold()
        if "not found" in detail or "notfound" in detail or "404" in detail:
            return None
        raise KubernetesError(f"k8s_get_failed:{resource}/{name}")
    document = service.json("get", resource, name, "-o", "json")
    return document if isinstance(document, dict) else None


def _get_first_cluster_policy(service: KubernetesService) -> Optional[JSONObject]:
    """Return the first ``ClusterPolicy`` object on the cluster when present.

    :param service: Verified Kubernetes service.
    :returns: First ``ClusterPolicy`` object, or ``None`` when absent.
    """
    result = service.command("get", CLUSTER_POLICIES, "-o", "json")
    if result.returncode:
        return None
    document = service.json("get", CLUSTER_POLICIES, "-o", "json")
    items = document.get("items", []) if isinstance(document, dict) else []
    for item in items:
        if isinstance(item, dict):
            return item
    return None


def _dry_run_label(
    service: KubernetesService,
    node_name: str,
    label_arg: str,
) -> bool:
    """Validate one Node label mutation with Kubernetes server-side dry-run.

    :param service: Verified Kubernetes service.
    :param node_name: Exact Kubernetes Node name.
    :param label_arg: Label expression such as ``key=value`` or ``key-``.
    :returns: True when server-side dry-run succeeds.
    :raises KubernetesError: If server-side dry-run rejects the change.
    """
    result = service.command(
        "label",
        NODES,
        node_name,
        label_arg,
        "--overwrite",
        "--dry-run=server",
        "-o",
        "json",
    )
    if result.returncode:
        raise KubernetesError(f"{DRY_RUN_FAILED}:{NODES}/{node_name}")
    return True


def _dry_run_json_patch(
    service: KubernetesService,
    resource: str,
    name: str,
    ops: list[JSONObject],
) -> bool:
    """Validate one RFC 6902 JSON patch with Kubernetes server-side dry-run.

    :param service: Verified Kubernetes service.
    :param resource: Qualified Kubernetes resource name.
    :param name: Exact object name.
    :param ops: JSON patch operations list.
    :returns: True when server-side dry-run succeeds.
    :raises KubernetesError: If server-side dry-run rejects the patch.
    """
    result = service.command(
        "patch",
        resource,
        name,
        "--type=json",
        "--patch",
        json.dumps(ops, sort_keys=True),
        "--dry-run=server",
        "-o",
        "json",
    )
    if result.returncode:
        raise KubernetesError(f"{DRY_RUN_FAILED}:{resource}/{name}")
    return True


def _node_label_plan(
    action: GPUOperatorAction,
    service: KubernetesService,
    label_key: str,
    pause_value: str,
    resume_value: Optional[str],
) -> tuple[Optional[PausePlan], list[GPUNeed]]:
    """Build a server-validated Node label pause or resume plan.

    :param action: Prepared GPU operator action.
    :param service: Verified Kubernetes service.
    :param label_key: Documented NVIDIA Node label key.
    :param pause_value: Value written when pausing.
    :param resume_value: Value written when resuming, or ``None`` to remove label.
    :returns: Tuple of resolved ``PausePlan`` (if valid) and ``GPUNeed`` items.
    """
    if not action.node or action.node == "*":
        return None, [
            GPUNeed(
                need=NEED_NODE_REQUIRED,
                evidence=f"pause scope {action.scope!r} requires one explicit --node name",
                supply="pass --node <node-name> to target one GPU node explicitly",
            )
        ]
    node = _get_object(service, NODES, action.node)
    if node is None:
        return None, [
            GPUNeed(
                need=NEED_TARGET_MISSING,
                evidence=f"node {action.node!r} was not found on the selected cluster",
                supply="pass --node with an existing GPU node name",
            )
        ]
    metadata = node.get("metadata") or {}
    uid = str(metadata.get("uid", ""))
    labels = metadata.get("labels") or {}
    current = labels.get(label_key) if isinstance(labels, dict) else None
    desired = pause_value if action.operation == "pause" else resume_value
    no_op = current == desired
    label_arg = f"{label_key}-" if desired is None else f"{label_key}={desired}"
    dry_run_ok = True if no_op else _dry_run_label(service, action.node, label_arg)
    return (
        PausePlan(
            action=action.operation,
            scope=str(action.scope),
            resource=NODES,
            name=action.node,
            uid=uid,
            before={label_key: current},
            after={label_key: desired},
            patch_kind=PATCH_KIND_LABEL,
            patch_payload={"label_arg": label_arg, "label_key": label_key},
            no_op=no_op,
            server_dry_run_ok=dry_run_ok,
        ),
        [],
    )


def _auto_upgrade_plan(
    action: GPUOperatorAction,
    service: KubernetesService,
) -> tuple[Optional[PausePlan], list[GPUNeed]]:
    """Build a server-validated ``upgradePolicy.autoUpgrade`` pause/resume plan.

    :param action: Prepared GPU operator action.
    :param service: Verified Kubernetes service.
    :returns: Tuple of resolved ``PausePlan`` (if valid) and ``GPUNeed`` items.
    """
    desired = action.operation == "resume"
    if action.nvidia_driver:
        resource = NVIDIA_DRIVERS
        obj = _get_object(service, resource, action.nvidia_driver)
        if obj is None:
            return None, [
                GPUNeed(
                    need=NEED_TARGET_MISSING,
                    evidence=f"NVIDIADriver {action.nvidia_driver!r} was not found",
                    supply="pass --nvidia-driver with an existing NVIDIADriver CR name",
                )
            ]
        spec = obj.get("spec") or {}
        upgrade = (
            spec.get("upgradePolicy")
            if isinstance(spec.get("upgradePolicy"), dict)
            else {}
        )
        path_exists = isinstance(spec.get("upgradePolicy"), dict)
        patch_ops: list[JSONObject] = (
            [
                {
                    "op": "replace" if "autoUpgrade" in upgrade else "add",
                    "path": "/spec/upgradePolicy/autoUpgrade",
                    "value": desired,
                }
            ]
            if path_exists
            else [
                {
                    "op": "add",
                    "path": "/spec/upgradePolicy",
                    "value": {"autoUpgrade": desired},
                }
            ]
        )
    else:
        resource = CLUSTER_POLICIES
        obj = _get_first_cluster_policy(service)
        if obj is None:
            return None, [
                GPUNeed(
                    need=NEED_TARGET_MISSING,
                    evidence="no ClusterPolicy exists on the selected cluster",
                    supply="create ClusterPolicy first or pass --nvidia-driver <name>",
                )
            ]
        spec = obj.get("spec") or {}
        driver = spec.get("driver") if isinstance(spec.get("driver"), dict) else {}
        if bool(driver.get("useNvidiaDriverCRD", False)):
            return None, [
                GPUNeed(
                    need=NEED_DRIVER_CR_REQUIRED,
                    evidence="ClusterPolicy sets spec.driver.useNvidiaDriverCRD=true",
                    supply="pass --nvidia-driver <name> to target the governing NVIDIADriver CR",
                )
            ]
        upgrade = (
            driver.get("upgradePolicy")
            if isinstance(driver.get("upgradePolicy"), dict)
            else {}
        )
        path_exists = isinstance(driver.get("upgradePolicy"), dict)
        patch_ops = (
            [
                {
                    "op": "replace" if "autoUpgrade" in upgrade else "add",
                    "path": "/spec/driver/upgradePolicy/autoUpgrade",
                    "value": desired,
                }
            ]
            if path_exists
            else [
                {
                    "op": "add",
                    "path": "/spec/driver/upgradePolicy",
                    "value": {"autoUpgrade": desired},
                }
            ]
        )

    metadata = obj.get("metadata") or {}
    name = str(metadata.get("name", ""))
    uid = str(metadata.get("uid", ""))
    current = upgrade.get("autoUpgrade")
    no_op = current is desired
    dry_run_ok = (
        True if no_op else _dry_run_json_patch(service, resource, name, patch_ops)
    )
    return (
        PausePlan(
            action=action.operation,
            scope=PAUSE_SCOPE_AUTO_UPGRADE,
            resource=resource,
            name=name,
            uid=uid,
            before={"autoUpgrade": current},
            after={"autoUpgrade": desired},
            patch_kind=PATCH_KIND_JSON,
            patch_payload={"ops": patch_ops},
            no_op=no_op,
            server_dry_run_ok=dry_run_ok,
        ),
        [],
    )


def _mcp_pause_plan(
    action: GPUOperatorAction,
    service: KubernetesService,
) -> tuple[Optional[PausePlan], list[GPUNeed]]:
    """Build a server-validated OpenShift ``MachineConfigPool`` pause/resume plan.

    :param action: Prepared GPU operator action.
    :param service: Verified Kubernetes service.
    :returns: Tuple of resolved ``PausePlan`` (if valid) and ``GPUNeed`` items.
    """
    if not action.mcp:
        return None, [
            GPUNeed(
                need=NEED_MCP_REQUIRED,
                evidence="pause scope 'mcp' requires an explicit --mcp pool name",
                supply="pass --mcp <pool-name>, for example --mcp worker",
            )
        ]
    pool = _get_object(service, MACHINE_CONFIG_POOLS, action.mcp)
    if pool is None:
        return None, [
            GPUNeed(
                need=NEED_TARGET_MISSING,
                evidence=f"MachineConfigPool {action.mcp!r} was not found",
                supply="pass --mcp with an existing OpenShift MachineConfigPool name",
            )
        ]
    status = pool.get("status") or {}
    conditions = {
        str(c.get("type")): str(c.get("status"))
        for c in (status.get("conditions") or [])
        if isinstance(c, dict)
    }
    if conditions.get("Degraded") == "True":
        return None, [
            GPUNeed(
                need=NEED_MCP_DEGRADED,
                evidence=f"MachineConfigPool {action.mcp!r} reports Degraded=True",
                supply="resolve the degraded MachineConfigPool before pausing or resuming it",
            )
        ]
    metadata = pool.get("metadata") or {}
    spec = pool.get("spec") or {}
    uid = str(metadata.get("uid", ""))
    current = bool(spec.get("paused", False))
    desired = action.operation == "pause"
    no_op = current is desired
    patch_ops: list[JSONObject] = [
        {
            "op": "replace" if "paused" in spec else "add",
            "path": "/spec/paused",
            "value": desired,
        }
    ]
    dry_run_ok = (
        True
        if no_op
        else _dry_run_json_patch(service, MACHINE_CONFIG_POOLS, action.mcp, patch_ops)
    )
    return (
        PausePlan(
            action=action.operation,
            scope=PAUSE_SCOPE_MCP,
            resource=MACHINE_CONFIG_POOLS,
            name=action.mcp,
            uid=uid,
            before={"paused": current},
            after={"paused": desired},
            patch_kind=PATCH_KIND_JSON,
            patch_payload={"ops": patch_ops},
            no_op=no_op,
            server_dry_run_ok=dry_run_ok,
        ),
        [],
    )


def build_pause_plan(
    action: GPUOperatorAction,
    service: KubernetesService,
) -> tuple[Optional[PausePlan], list[GPUNeed]]:
    """Resolve and server-dry-run one pause or resume plan for ``NvidiaPauseOperator``.

    :param action: Prepared GPU operator action.
    :param service: Verified Kubernetes service.
    :returns: Tuple of resolved ``PausePlan`` (or ``None`` when blocked) and needs.
    """
    scope = action.scope
    if scope not in PAUSE_SCOPES:
        return None, [
            GPUNeed(
                need=NEED_SCOPE_REQUIRED,
                evidence=f"pause scope {scope!r} is missing or unsupported",
                supply=f"pass --scope with one of: {', '.join(PAUSE_SCOPES)}",
            )
        ]
    if scope == PAUSE_SCOPE_UPGRADE_SKIP:
        return _node_label_plan(action, service, LABEL_UPGRADE_SKIP, "true", None)
    if scope == PAUSE_SCOPE_OPERANDS:
        return _node_label_plan(action, service, LABEL_DEPLOY_OPERANDS, "false", None)
    if scope == PAUSE_SCOPE_DRIVER:
        return _node_label_plan(action, service, LABEL_DEPLOY_DRIVER, "false", "true")
    if scope == PAUSE_SCOPE_AUTO_UPGRADE:
        return _auto_upgrade_plan(action, service)
    return _mcp_pause_plan(action, service)


def pause_plan_evidence(plan: PausePlan) -> JSONObject:
    """Build the canonical JSON mapping hashed into ``plan_digest``.

    :param plan: Resolved pause or resume plan.
    :returns: Digest-bound plan evidence dictionary.
    """
    return {
        "action": plan.action,
        "scope": plan.scope,
        "resource": plan.resource,
        "name": plan.name,
        "uid": plan.uid,
        "before": plan.before,
        "after": plan.after,
        "no_op": plan.no_op,
    }


def verify_pause_confirmation(plan: PausePlan, confirm_plan: Optional[str]) -> str:
    """Recompute and verify ``plan_digest`` against the caller's confirmation token.

    :param plan: Recomputed live pause plan.
    :param confirm_plan: Caller-supplied ``--confirm-plan`` SHA-256 digest.
    :returns: Verified SHA-256 plan digest.
    :raises ValueError: If confirmation is missing or does not match the live plan.
    """
    digest = plan_digest(pause_plan_evidence(plan))
    if not confirm_plan or confirm_plan != digest:
        raise ValueError(CONFIRM_MISMATCH)
    return digest


def apply_pause_plan(plan: PausePlan, service: KubernetesService) -> JSONObject:
    """Execute one confirmed pause/resume mutation and independently read it back.

    :param plan: Verified pause/resume plan.
    :param service: Verified Kubernetes service.
    :returns: Read-back evidence showing observed state and whether mutation occurred.
    :raises KubernetesError: If the write or independent read-back fails.
    """
    if plan.no_op:
        return {
            "action": "NO_OP",
            "mutated": False,
            "resource": plan.resource,
            "name": plan.name,
            "uid": plan.uid,
            "observed": plan.after,
            "verified": True,
        }
    if plan.patch_kind == PATCH_KIND_LABEL:
        label_arg = str(plan.patch_payload["label_arg"])
        result = service.command(
            "label", NODES, plan.name, label_arg, "--overwrite", "-o", "json"
        )
        if result.returncode:
            raise KubernetesError(f"k8s_label_failed:{NODES}/{plan.name}")
    else:
        ops = plan.patch_payload["ops"]
        result = service.command(
            "patch",
            plan.resource,
            plan.name,
            "--type=json",
            "--patch",
            json.dumps(ops, sort_keys=True),
            "-o",
            "json",
        )
        if result.returncode:
            raise KubernetesError(f"k8s_patch_failed:{plan.resource}/{plan.name}")

    # Independent read-back verification.
    fresh = _get_object(service, plan.resource, plan.name)
    if fresh is None:
        raise KubernetesError(f"k8s_readback_missing:{plan.resource}/{plan.name}")
    observed = _extract_observed(plan, fresh)
    if observed != plan.after:
        raise KubernetesError(f"k8s_readback_mismatch:{plan.resource}/{plan.name}")
    return {
        "action": "APPLIED",
        "mutated": True,
        "resource": plan.resource,
        "name": plan.name,
        "uid": str((fresh.get("metadata") or {}).get("uid", "")),
        "observed": observed,
        "verified": True,
    }


def _extract_observed(plan: PausePlan, obj: JSONObject) -> JSONObject:
    """Read back the exact fields mutated by one pause/resume plan.

    :param plan: Applied pause/resume plan.
    :param obj: Independently fetched Kubernetes object.
    :returns: Observed field mapping matching the keys in ``plan.after``.
    """
    if plan.patch_kind == PATCH_KIND_LABEL:
        key = str(plan.patch_payload["label_key"])
        labels = (obj.get("metadata") or {}).get("labels") or {}
        return {key: labels.get(key) if isinstance(labels, dict) else None}
    spec = obj.get("spec") or {}
    if plan.scope == PAUSE_SCOPE_MCP:
        return {"paused": bool(spec.get("paused", False))}
    if plan.resource == NVIDIA_DRIVERS:
        upgrade = (
            spec.get("upgradePolicy")
            if isinstance(spec.get("upgradePolicy"), dict)
            else {}
        )
        return {"autoUpgrade": upgrade.get("autoUpgrade")}
    driver = spec.get("driver") if isinstance(spec.get("driver"), dict) else {}
    upgrade = (
        driver.get("upgradePolicy")
        if isinstance(driver.get("upgradePolicy"), dict)
        else {}
    )
    return {"autoUpgrade": upgrade.get("autoUpgrade")}
