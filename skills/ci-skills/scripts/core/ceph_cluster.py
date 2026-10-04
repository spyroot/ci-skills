"""Read a selected Rook Ceph cluster and correlate OSD/monitor Pod state."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .access import kubernetes_env, oc_argv
from .report import report
from .runtime import CommandResult, error_class, run_command_tail
from .status import PARTIAL, PASS
from .target import Target, kubernetes_label

MAX_OUTPUT_BYTES = 1_048_576
MAX_OUTPUT_LINES = 20_000
POD_SELECTOR = "app in (rook-ceph-osd,rook-ceph-mon)"
POD_ROLES = {"rook-ceph-osd", "rook-ceph-mon"}
KUBERNETES_NAME = re.compile(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?\Z")


# Summary: bind oc commands to the selected context and Ceph namespace
# Arguments: target and namespace; Environment inputs: target credential source
# Stdout: none; Stderr: none; Exit classes: command prefix or source error
# Side effects: checks kubeconfig integrity; Idempotency: stable for unchanged target
# Cleanup: none
def _oc_prefix(target: Target, namespace: str) -> list[str]:
    """Bind every oc call to the same credential files and context as the gate."""
    return oc_argv(target, "-n", namespace)


# Summary: decode a bounded successful Ceph or Pod JSON response
# Arguments: command result and source label; Environment inputs: command output
# Stdout: none; Stderr: none; Exit classes: JSON value or ValueError
# Side effects: none; Idempotency: same result gives same value; Cleanup: none
def _json_response(result: CommandResult, source: str) -> Any:
    if result.returncode:
        raise ValueError(f"{source}:{error_class(result)}")
    if len(result.stdout.encode("utf-8")) >= MAX_OUTPUT_BYTES - 4:
        raise ValueError(f"{source}:output_limit")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source}:invalid_json") from exc


# Summary: parse an optional Pod condition filter
# Arguments: filter text; Environment inputs: none
# Stdout: none; Stderr: none; Exit classes: pair, None, or ValueError
# Side effects: none; Idempotency: same text gives same result; Cleanup: none
def _condition_filter(value: str | None) -> tuple[str, str] | None:
    if value is None:
        return None
    kind, separator, state = value.partition("=")
    if not separator or not kind or state not in {"True", "False", "Unknown"}:
        raise ValueError("condition must be TYPE=True|False|Unknown")
    return kind, state


# Summary: validate and filter selected Ceph OSD and monitor Pods
# Arguments: payload, namespace, node, ready state, condition
# Environment inputs: none; Stdout: none; Stderr: none
# Exit classes: total and selected Pods, or TypeError; Side effects: none
# Idempotency: same payload and filters give same result; Cleanup: none
def _pods(
    payload: Any,
    *,
    namespace: str,
    node: str | None,
    ready: str,
    condition: tuple[str, str] | None,
) -> tuple[int, list[dict[str, Any]]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise TypeError("pods:invalid_list_response")
    selected = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            raise TypeError("pods:invalid_item")
        metadata = item.get("metadata")
        spec = item.get("spec")
        status = item.get("status")
        if (
            not isinstance(metadata, dict)
            or not isinstance(spec, dict)
            or not isinstance(status, dict)
        ):
            raise TypeError("pods:invalid_item")
        labels = metadata.get("labels")
        conditions = status.get("conditions", [])
        if not isinstance(labels, dict) or not isinstance(conditions, list):
            raise TypeError("pods:invalid_item")
        name = metadata.get("name")
        if not isinstance(name, str) or not name:
            raise TypeError("pods:missing_name")
        role = labels.get("app")
        if role not in POD_ROLES:
            raise TypeError("pods:selector_mismatch")
        states = {}
        for entry in conditions:
            if not isinstance(entry, dict) or not isinstance(entry.get("type"), str):
                raise TypeError("pods:invalid_condition")
            states[entry["type"]] = entry.get("status")
        is_ready = states.get("Ready") == "True"
        pod_node = spec.get("nodeName")
        if node and pod_node != node:
            continue
        if ready != "all" and is_ready != (ready == "true"):
            continue
        if condition and states.get(condition[0]) != condition[1]:
            continue
        selected.append(
            {
                "namespace": metadata.get("namespace", namespace),
                "name": name,
                "role": role,
                "osd_id": labels.get("ceph-osd-id"),
                "node": pod_node,
                "phase": status.get("phase"),
                "ready": is_ready,
                "conditions": states,
            }
        )
    return len(payload["items"]), selected


# Summary: normalize inactive placement groups from Ceph JSON
# Arguments: decoded payload; Environment inputs: none
# Stdout: none; Stderr: none; Exit classes: PG list or TypeError
# Side effects: none; Idempotency: same payload gives same list; Cleanup: none
def _inactive_pgs(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get(
            "stuck_pg_stats", payload.get("pg_stats", payload.get("pgs"))
        )
    if not isinstance(payload, list):
        raise TypeError("inactive_pgs:invalid_response")
    result = []
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("pgid"), str):
            raise TypeError("inactive_pgs:invalid_item")
        result.append({"pgid": item["pgid"], "state": item.get("state")})
    return result


# Summary: validate Ceph health status and preserve named checks
# Arguments: decoded payload; Environment inputs: none
# Stdout: none; Stderr: none; Exit classes: status and findings, or TypeError
# Side effects: none; Idempotency: same payload gives same findings; Cleanup: none
def _health(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    """Keep Ceph's named health checks so an alert has an exact cause."""
    if not isinstance(payload, dict) or payload.get("status") not in {
        "HEALTH_OK",
        "HEALTH_WARN",
        "HEALTH_ERR",
    }:
        raise TypeError("status:invalid_health")
    checks = payload.get("checks", {})
    if not isinstance(checks, dict):
        raise TypeError("status:invalid_health_checks")
    findings = []
    for code, check in sorted(checks.items()):
        if not isinstance(code, str) or not code or not isinstance(check, dict):
            raise TypeError("status:invalid_health_check")
        summary = check.get("summary")
        message = summary.get("message") if isinstance(summary, dict) else summary
        if not isinstance(message, str) or not message:
            raise TypeError("status:invalid_health_summary")
        severity = check.get("severity")
        if severity is not None and not isinstance(severity, str):
            raise TypeError("status:invalid_health_severity")
        detail = check.get("detail", [])
        if not isinstance(detail, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("message"), str)
            for item in detail
        ):
            raise TypeError("status:invalid_health_detail")
        findings.append(
            {
                "code": code,
                "severity": severity,
                "summary": message,
                "detail": [item["message"] for item in detail],
            }
        )
    return payload["status"], findings


# Summary: validate and expand Ceph OSD hierarchy without cycles
# Arguments: decoded tree; Environment inputs: none
# Stdout: none; Stderr: none; Exit classes: roots and OSDs, or TypeError
# Side effects: none; Idempotency: same tree gives same result; Cleanup: none
def _osd_tree(payload: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep Ceph's root/bucket/OSD links while validating every referenced node."""
    nodes = payload.get("nodes") if isinstance(payload, dict) else None
    if not isinstance(nodes, list):
        raise TypeError("osd_tree:invalid_response")
    by_id: dict[int, dict[str, Any]] = {}
    osds: list[dict[str, Any]] = []
    for item in nodes:
        if not isinstance(item, dict):
            raise TypeError("osd_tree:invalid_node")
        node_id, name, kind = item.get("id"), item.get("name"), item.get("type")
        if (
            type(node_id) is not int
            or node_id in by_id
            or not isinstance(name, str)
            or not name
            or not isinstance(kind, str)
            or not kind
        ):
            raise TypeError("osd_tree:invalid_node")
        children = item.get("children", [])
        if not isinstance(children, list) or any(
            type(child) is not int for child in children
        ):
            raise TypeError("osd_tree:invalid_children")
        status = item.get("status")
        if status is not None and (not isinstance(status, str) or not status):
            raise TypeError("osd_tree:invalid_status")
        normalized = {"id": node_id, "name": name, "type": kind, "children": children}
        if status is not None:
            normalized["status"] = status
        by_id[node_id] = normalized
        if kind == "osd":
            osds.append({"id": node_id, "name": name, "status": status})

    parents: set[int] = set()
    for node in by_id.values():
        for child in node["children"]:
            if child not in by_id or child in parents:
                raise TypeError("osd_tree:invalid_child_reference")
            parents.add(child)
    visiting: set[int] = set()
    visited: set[int] = set()

    # Summary: expand one validated OSD tree node and reject revisits
    # Arguments: node identifier; Environment inputs: surrounding tree maps
    # Stdout: none; Stderr: none; Exit classes: node map or TypeError
    # Side effects: updates traversal sets; Idempotency: depends on traversal state
    # Cleanup: visiting entry removed after successful expansion
    def expand(node_id: int) -> dict[str, Any]:
        if node_id in visiting or node_id in visited:
            raise TypeError("osd_tree:cycle")
        visiting.add(node_id)
        node = by_id[node_id]
        result = {key: value for key, value in node.items() if key != "children"}
        result["children"] = [expand(child) for child in node["children"]]
        visiting.remove(node_id)
        visited.add(node_id)
        return result

    roots = [expand(node_id) for node_id in by_id if node_id not in parents]
    if len(visited) != len(by_id):
        raise TypeError("osd_tree:cycle")
    return roots, osds


# Summary: collect Ceph health, OSD tree, inactive PGs, and related Pods
# Arguments: target and command filters; Environment inputs: live cluster API
# Stdout: none; Stderr: none; Exit classes: report or invalid-input ValueError
# Side effects: concurrent read-only oc and Ceph queries
# Idempotency: depends on live cluster; Cleanup: executor joins child queries
def collect_ceph_cluster(target: Target, args: Any) -> dict[str, Any]:
    """Run independent read-only Ceph and Pod queries against one pinned target."""
    namespace = args.namespace
    operator = args.operator
    config = args.conf or f"/var/lib/rook/{namespace}/{namespace}.config"
    if not KUBERNETES_NAME.fullmatch(namespace) or len(namespace) > 63:
        raise ValueError("invalid_ceph_namespace")
    if not KUBERNETES_NAME.fullmatch(operator) or len(operator) > 63:
        raise ValueError("invalid_ceph_operator")
    if not isinstance(config, str) or not config.startswith("/"):
        raise ValueError("invalid_ceph_config_path")
    condition = _condition_filter(args.condition)
    prefix = _oc_prefix(target, namespace)
    ceph = [*prefix, "exec", f"deploy/{operator}", "--", "ceph", f"--conf={config}"]
    commands = {
        "status": [*ceph, "-s", "--format=json"],
        "osd_tree": [*ceph, "osd", "tree", "--format=json"],
        "inactive_pgs": [*ceph, "pg", "dump_stuck", "inactive", "--format=json"],
        "pods": [*prefix, "get", "pods", "-l", POD_SELECTOR, "-o", "json"],
    }
    environment = kubernetes_env(target)
    with ThreadPoolExecutor(max_workers=len(commands)) as pool:
        futures = {
            key: pool.submit(
                run_command_tail,
                command,
                timeout=40,
                max_bytes=MAX_OUTPUT_BYTES,
                max_lines=MAX_OUTPUT_LINES,
                env=environment,
            )
            for key, command in commands.items()
        }
        outputs = {key: future.result() for key, future in futures.items()}

    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    result = report(
        "ceph_cluster",
        kubernetes_label(target),
        {
            "namespace": namespace,
            "node": args.node,
            "ready": args.ready,
            "condition": args.condition,
        },
        records,
        errors,
    )
    result["queries"] = {key: command for key, command in commands.items()}
    decoded = {}
    for key, response in outputs.items():
        try:
            decoded[key] = _json_response(response, key)
        except ValueError as exc:
            errors.append({"source": key, "reason": str(exc)})
    if "status" in decoded:
        health = (
            decoded["status"].get("health")
            if isinstance(decoded["status"], dict)
            else None
        )
        try:
            result["health"], result["health_checks"] = _health(health)
        except TypeError as exc:
            errors.append({"source": "status", "reason": str(exc)})
    if "osd_tree" in decoded:
        try:
            result["osd_tree"], result["osds"] = _osd_tree(decoded["osd_tree"])
        except TypeError as exc:
            errors.append({"source": "osd_tree", "reason": str(exc)})
    if "inactive_pgs" in decoded:
        try:
            result["inactive_pgs"] = _inactive_pgs(decoded["inactive_pgs"])
        except TypeError as exc:
            errors.append({"source": "inactive_pgs", "reason": str(exc)})
    if "pods" in decoded:
        try:
            total, selected = _pods(
                decoded["pods"],
                namespace=namespace,
                node=args.node,
                ready=args.ready,
                condition=condition,
            )
            result["pod_count"] = total
            records.extend(selected)
        except TypeError as exc:
            errors.append({"source": "pods", "reason": str(exc)})

    actions = set()
    if result.get("health") in {"HEALTH_WARN", "HEALTH_ERR"}:
        actions.add("inspect_ceph_health_detail")
    if any(item.get("status") == "down" for item in result.get("osds", [])):
        actions.add("inspect_down_osd_pods_and_nodes")
    if result.get("inactive_pgs"):
        actions.add("inspect_inactive_pg_peering")
    if any(not item["ready"] for item in records):
        actions.add("inspect_unready_ceph_pods")
    result["actions"] = sorted(actions)
    result["condition"] = "UNKNOWN" if errors else "ATTENTION" if actions else "CLEAR"
    result["summary"] = {"record_count": len(records), "error_count": len(errors)}
    result["status"] = PARTIAL if errors else PASS
    if errors:
        result["safe_next_step"] = "Review failed Ceph queries on the selected target."
    return result
