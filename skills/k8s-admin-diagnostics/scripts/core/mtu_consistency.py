"""Plan and verify physical uplink MTUs through temporary OpenShift debug Pods."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import socket
import stat
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .access import check_access, kubectl_argv, kubernetes_env, oc_argv
from .cli import _failure, output_mode
from .credentials import Sources, bind_sources
from .project_binding import resolve_target
from .report import emit, report
from .runtime import CommandResult, error_class, run_command_tail, sanitize
from .status import BLOCKED, DRY_RUN, PARTIAL, PASS, PROFILE_BASE, exit_code
from .target import Target, TargetError, kubernetes_label

KIND = "k8s_verify_mtu_consistency"
MAX_OUTPUT_BYTES = 1_048_576
MAX_OUTPUT_LINES = 20_000
MAX_WORKERS = 4
MARKER = "CI_SKILLS_MTU_RUN_ID"
DNS_LABEL = re.compile(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?\Z")
DNS_NAME = re.compile(
    r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*\Z"
)
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class Interrupted(RuntimeError):
    """A signal interrupted a bounded debug run; cleanup still must execute."""


@dataclass(frozen=True)
class MtuPlan:
    """The exact target and node inventory authorized by one plan digest."""

    namespace: str
    nodes: tuple[str, ...]
    digest: str
    inputs: dict[str, Any]


class EventLogger:
    """Emit bounded, credential-free diagnostics without changing report stdout."""

    def __init__(self, args: Any, run_id: str):
        self.args = args
        self.run_id = run_id
        self.fd: int | None = None
        if args.log_file:
            path = Path(args.log_file).expanduser()
            self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            if stat.S_IMODE(os.fstat(self.fd).st_mode) & 0o077:
                os.close(self.fd)
                self.fd = None
                raise ValueError("log_file_not_private")

    def event(self, level: str, action: str, result: str) -> None:
        levels = ("debug", "info", "warning", "error")
        if levels.index(level) < levels.index(self.args.log_level):
            return
        item = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "run_id": self.run_id,
            "component": KIND,
            "operation": action,
            "result": result,
        }
        if self.args.log_format == "json":
            line = json.dumps(item, sort_keys=True) + "\n"
        else:
            line = f"{item['timestamp']} {level} {action}: {result}\n"
        sys.stderr.write(line)
        if self.fd is not None:
            os.write(self.fd, line.encode("utf-8"))

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)


def _response(result: CommandResult, source: str) -> Any:
    """Reject failed, truncated, or malformed external responses."""
    if result.returncode:
        raise ValueError(f"{source}:{error_class(result)}")
    if len(result.stdout.encode("utf-8")) >= MAX_OUTPUT_BYTES - 4:
        raise ValueError(f"{source}:output_limit")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source}:invalid_json") from exc


def physical_uplinks(payload: Any) -> list[dict[str, Any]]:
    """Select PCI Ethernet links and retain optional IPv4 address metadata."""
    if not isinstance(payload, list):
        raise TypeError("links:invalid_list_response")
    selected: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise TypeError("links:invalid_item")
        if item.get("link_type") != "ether" or item.get("parentbus") != "pci":
            continue
        if not isinstance(item.get("parentdev"), str) or not item["parentdev"]:
            continue
        linkinfo = item.get("linkinfo", {})
        if not isinstance(linkinfo, dict):
            raise TypeError("links:invalid_linkinfo")
        if linkinfo.get("info_kind") not in (None, ""):
            continue
        addresses = item.get("addr_info", [])
        if not isinstance(addresses, list):
            raise TypeError("links:invalid_addresses")
        ipv4 = []
        for address in addresses:
            if not isinstance(address, dict):
                raise TypeError("links:invalid_address")
            if address.get("family") == "inet":
                local = address.get("local")
                if not isinstance(local, str) or not local:
                    raise ValueError("links:invalid_ipv4_address")
                ipv4.append(local)
        name, mtu, state = item.get("ifname"), item.get("mtu"), item.get("operstate")
        if (
            not isinstance(name, str)
            or not name
            or type(mtu) is not int
            or mtu <= 0
            or not isinstance(state, str)
            or not state
        ):
            raise ValueError("links:invalid_physical_uplink")
        selected.append(
            {
                "interface": name,
                "mtu": mtu,
                "oper_state": state,
                "parent_bus": "pci",
                "pci_device": item["parentdev"],
                "ipv4_addresses": sorted(set(ipv4)),
            }
        )
    selected.sort(key=lambda link: link["interface"])
    if len({link["interface"] for link in selected}) != len(selected):
        raise ValueError("links:duplicate_interface")
    return selected


def _read_nodes(target: Target, selected: str | None) -> tuple[str, ...]:
    command = kubectl_argv(target, "get", "nodes", "-o", "json")
    payload = _response(
        run_command_tail(
            command,
            timeout=30,
            max_bytes=MAX_OUTPUT_BYTES,
            max_lines=MAX_OUTPUT_LINES,
            env=kubernetes_env(target),
        ),
        "nodes",
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise TypeError("nodes:invalid_list_response")
    names = []
    for item in payload["items"]:
        metadata = item.get("metadata") if isinstance(item, dict) else None
        name = metadata.get("name") if isinstance(metadata, dict) else None
        if not isinstance(name, str) or not DNS_NAME.fullmatch(name) or len(name) > 253:
            raise ValueError("nodes:invalid_item")
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError("nodes:duplicate_name")
    if selected:
        if not DNS_NAME.fullmatch(selected) or len(selected) > 253:
            raise ValueError("node:invalid_name")
        if selected not in names:
            raise ValueError("node:not_found_on_selected_target")
        return (selected,)
    if not names:
        raise ValueError("nodes:empty_inventory")
    return tuple(sorted(names))


def _verify_openshift(target: Target) -> None:
    """Block before debug Pods if this target lacks the OpenShift API or CLI."""
    if shutil.which("oc") is None:
        raise ValueError("oc:missing_tool")
    command = kubectl_argv(target, "get", "--raw=/apis/config.openshift.io/v1")
    response = run_command_tail(
        command,
        timeout=20,
        max_bytes=MAX_OUTPUT_BYTES,
        max_lines=MAX_OUTPUT_LINES,
        env=kubernetes_env(target),
    )
    if response.returncode:
        detail = response.stderr.casefold()
        if "404" in detail or "not found" in detail:
            raise ValueError("cluster:openshift_api_absent")
        raise ValueError(f"cluster:openshift_api_{error_class(response)}")
    payload = _response(response, "openshift_api")
    if (
        not isinstance(payload, dict)
        or payload.get("kind") != "APIResourceList"
        or payload.get("groupVersion") != "config.openshift.io/v1"
    ):
        raise ValueError("cluster:openshift_api_invalid")


def _read_namespace(target: Target) -> str:
    """Use the selected context's namespace, or Kubernetes' default namespace."""
    command = kubectl_argv(
        target, "config", "view", "--minify", "-o", "jsonpath={..namespace}"
    )
    result = run_command_tail(
        command, timeout=15, max_bytes=4096, max_lines=20, env=kubernetes_env(target)
    )
    if result.returncode:
        raise ValueError(f"namespace:{error_class(result)}")
    namespace = result.stdout.strip() or "default"
    if not DNS_LABEL.fullmatch(namespace) or len(namespace) > 63:
        raise ValueError("namespace:invalid_context_namespace")
    return namespace


def _source_digest(target: Target) -> str:
    """Bind the plan to the selected target file and optional project binding."""
    if target.source_file is None:
        raise TargetError("target_file_unresolved")
    parts = [target.source_file.resolve()]
    reference = target.target_reference or ""
    if reference.startswith("binding:"):
        parts.append(Path(reference.removeprefix("binding:")).resolve())
    digest = hashlib.sha256()
    for path in parts:
        digest.update(str(path).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


def build_plan(target: Target, args: Any) -> MtuPlan:
    """Read an exact node inventory without creating a debug Pod."""
    _verify_openshift(target)
    nodes = _read_nodes(target, args.node)
    namespace = _read_namespace(target)
    sources = target.sources if isinstance(target.sources, Sources) else None
    inputs = {
        "schema_version": "1.0",
        "operation": KIND,
        "target_reference": target.target_reference,
        "target_source": target.source_kind,
        "target_file_sha256": _source_digest(target),
        "skill_digest": target.skill.get("digest") if target.skill else None,
        "tested_revision": target.tested_revision,
        "kubeconfig_sha256": list(sources.kubeconfig_digests) if sources else [],
        "context": target.kubernetes.context,
        "server": target.kubernetes.server,
        "namespace": namespace,
        "nodes": list(nodes),
        "timeout_seconds": args.timeout,
        "selection": "PCI Ethernet with nonempty parentdev and no virtual kind",
        "host_command": ["chroot", "/host", "ip", "-d", "-j", "addr", "show"],
    }
    encoded = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    return MtuPlan(namespace, nodes, hashlib.sha256(encoded).hexdigest(), inputs)


def _marked_pods(target: Target, namespace: str, marker: str) -> list[str]:
    """Read back only Pods bearing this invocation's unique debug marker."""
    command = kubectl_argv(target, "-n", namespace, "get", "pods", "-o", "json")
    payload = _response(
        run_command_tail(
            command,
            timeout=30,
            max_bytes=MAX_OUTPUT_BYTES,
            max_lines=MAX_OUTPUT_LINES,
            env=kubernetes_env(target),
        ),
        "cleanup_pods",
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise TypeError("cleanup_pods:invalid_list_response")
    found = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            raise TypeError("cleanup_pods:invalid_item")
        spec = item.get("spec")
        containers = spec.get("containers") if isinstance(spec, dict) else None
        if not isinstance(containers, list):
            raise TypeError("cleanup_pods:invalid_item")
        marked = any(
            isinstance(container, dict)
            and isinstance(container.get("env"), list)
            and any(
                isinstance(entry, dict)
                and entry.get("name") == MARKER
                and entry.get("value") == marker
                for entry in container["env"]
            )
            for container in containers
        )
        if marked:
            metadata = item.get("metadata")
            name = metadata.get("name") if isinstance(metadata, dict) else None
            if not isinstance(name, str) or not DNS_NAME.fullmatch(name):
                raise ValueError("cleanup_pods:invalid_marked_name")
            found.append(name)
    return sorted(set(found))


def _cleanup(target: Target, namespace: str, marker: str) -> dict[str, Any]:
    """Delete only this run's surviving debug Pods and independently verify absence."""
    try:
        observed = _marked_pods(target, namespace, marker)
        failed = []
        for name in observed:
            command = kubectl_argv(
                target,
                "-n",
                namespace,
                "delete",
                "pod",
                name,
                "--ignore-not-found=true",
                "--wait=true",
                "--timeout=30s",
            )
            result = run_command_tail(
                command,
                timeout=35,
                max_bytes=4096,
                max_lines=20,
                env=kubernetes_env(target),
            )
            if result.returncode:
                failed.append({"pod": name, "reason": error_class(result)})
        remaining = _marked_pods(target, namespace, marker)
        return {
            "status": PASS if not failed and not remaining else BLOCKED,
            "observed_pods": observed,
            "delete_failures": failed,
            "remaining_pods": remaining,
            "verified": not failed and not remaining,
        }
    except (TargetError, ValueError, TypeError, OSError) as exc:
        return {
            "status": BLOCKED,
            "verified": False,
            "reason": sanitize(str(exc), 160),
            "remaining_pods": "UNKNOWN",
        }


def _read_one(
    target: Target, plan: MtuPlan, node: str, marker: str, timeout: int
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    command = oc_argv(
        target,
        "debug",
        f"node/{node}",
        f"{MARKER}={marker}",
        "--quiet",
        f"--to-namespace={plan.namespace}",
        "--",
        "chroot",
        "/host",
        "ip",
        "-d",
        "-j",
        "addr",
        "show",
    )
    result = run_command_tail(
        command,
        timeout=timeout,
        max_bytes=MAX_OUTPUT_BYTES,
        max_lines=MAX_OUTPUT_LINES,
        env=kubernetes_env(target),
    )
    try:
        links = physical_uplinks(_response(result, f"node/{node}"))
        if not links:
            raise ValueError(f"node/{node}:no_pci_ethernet_uplink")
        return [{"node": node, **link} for link in links], None
    except (ValueError, TypeError) as exc:
        return [], {"source": f"node/{node}", "reason": sanitize(str(exc), 160)}


def collect_mtu_consistency(
    target: Target, args: Any, plan: MtuPlan, marker: str
) -> dict[str, Any]:
    """Read all planned nodes, clean temporary Pods, and classify consistency."""
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    executor = ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(plan.nodes)))
    try:
        futures = {
            executor.submit(_read_one, target, plan, node, marker, args.timeout): node
            for node in plan.nodes
        }
        for future in as_completed(futures):
            node = futures[future]
            try:
                links, error = future.result()
                records.extend(links)
                if error:
                    errors.append(error)
            except (
                TargetError,
                ValueError,
                TypeError,
                KeyError,
                IndexError,
                OSError,
            ) as exc:
                errors.append(
                    {"source": f"node/{node}", "reason": sanitize(str(exc), 160)}
                )
    except (KeyboardInterrupt, Interrupted):
        errors.append({"source": "run", "reason": "interrupted"})
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        cleanup = _cleanup(target, plan.namespace, marker)

    records.sort(key=lambda row: (row["node"], row["interface"]))
    mtu_values = sorted({row["mtu"] for row in records})
    nodes_read = sorted({row["node"] for row in records})
    result = report(
        KIND,
        kubernetes_label(target),
        {"node": args.node, "selection": plan.inputs["selection"]},
        records,
        errors,
    )
    result["plan_digest"] = plan.digest
    result["planned_nodes"] = list(plan.nodes)
    result["namespace"] = plan.namespace
    result["side_effects"] = ["temporary_oc_debug_pods"]
    result["debug_marker"] = marker
    result["access_proven"] = True
    result["cleanup"] = cleanup
    result["mtu_values"] = mtu_values
    result["summary"] = {
        "record_count": len(records),
        "error_count": len(errors),
        "nodes_selected": len(plan.nodes),
        "nodes_read": len(nodes_read),
        "mtu_values": mtu_values,
        "consistent": not errors and cleanup["verified"] and len(mtu_values) == 1,
    }
    findings = []
    if len(mtu_values) > 1:
        findings.append(
            {
                "code": "physical_mtu_mismatch",
                "action": "inspect_node_physical_nic_mtu",
                "mtu_values": mtu_values,
            }
        )
    result["findings"] = findings
    result["condition"] = (
        "UNKNOWN"
        if errors or not cleanup["verified"]
        else "INCONSISTENT"
        if findings
        else "CONSISTENT"
    )
    result["status"] = (
        BLOCKED
        if not records or not cleanup["verified"]
        else PARTIAL
        if errors or findings
        else PASS
    )
    if errors or not cleanup["verified"]:
        result["safe_next_step"] = (
            "Review failed node reads or temporary Pod cleanup on the selected target."
        )
    return result


def _base_result(
    target: Target, plan: MtuPlan, gate: dict[str, Any], status: str
) -> dict[str, Any]:
    data = report(
        KIND,
        kubernetes_label(target),
        {"node": plan.nodes[0] if len(plan.nodes) == 1 else None},
        [],
        [],
    )
    data.update(
        {
            "status": status,
            "execution_host": socket.getfqdn(),
            "tested_revision": target.tested_revision,
            "target_file": str(target.source_file),
            "target_source": target.source_kind,
            "namespace": plan.namespace,
            "planned_nodes": list(plan.nodes),
            "plan_digest": plan.digest,
            "plan": {
                "target": kubernetes_label(target),
                "node_count": len(plan.nodes),
                "nodes": list(plan.nodes),
                "namespace": plan.namespace,
                "host_command": plan.inputs["host_command"],
                "side_effects_on_apply": ["temporary_oc_debug_pods"],
                "cleanup": "delete marked debug Pods and verify none remain",
            },
            "access": gate,
        }
    )
    return data


def _signal_interrupt(_signum: int, _frame: Any) -> None:
    raise Interrupted("interrupted")


def run(args: Any) -> int:
    """Plan by default; apply only the exact confirmed fingerprint."""
    if args.describe:
        from .catalog import describe

        sys.stdout.write(
            json.dumps(
                describe("k8s_verify_mtu_consistency.py"), indent=2, sort_keys=True
            )
            + "\n"
        )
        return 0
    if args.apply and args.dry_run:
        return _failure(args, KIND, "arguments", "apply_dry_run_conflict")
    if args.apply and not args.confirm_plan:
        return _failure(args, KIND, "arguments", "confirm_plan_required")
    if not args.apply and args.confirm_plan:
        return _failure(args, KIND, "arguments", "confirm_plan_requires_apply")
    if args.timeout < 10 or args.timeout > 300:
        return _failure(args, KIND, "arguments", "timeout_must_be_10_to_300_seconds")
    if args.run_id and not RUN_ID.fullmatch(args.run_id):
        return _failure(args, KIND, "arguments", "run_id_invalid")
    run_id = args.run_id or uuid.uuid4().hex
    marker = f"{run_id}-{uuid.uuid4().hex[:12]}"
    try:
        logger = EventLogger(args, run_id)
    except (OSError, ValueError) as exc:
        return _failure(args, KIND, "logging", str(exc))
    stage = "target"
    try:
        target = resolve_target(
            args.target, args.binding, required_surfaces=("kubernetes",)
        )
        stage = "credential_source"
        target = bind_sources(target, revision=args.revision)
        stage = "access_check"
        gate = check_access(target, cilium=False)
        gate["profile"] = PROFILE_BASE
        if gate["status"] != PASS:
            data = report(
                KIND,
                kubernetes_label(target),
                {},
                [],
                [{"source": "access", "reason": "kubernetes_access_not_pass"}],
            )
            data["status"] = BLOCKED
            data["access"] = gate
            logger.event("error", "access", BLOCKED)
            sys.stdout.write(emit(data, output_mode(args), args.output_dir))
            return 2
        stage = "plan"
        plan = build_plan(target, args)
        if not args.apply:
            data = _base_result(target, plan, gate, DRY_RUN)
            data["summary"] = {
                "record_count": 0,
                "error_count": 0,
                "nodes_selected": len(plan.nodes),
            }
            logger.event("info", "plan", DRY_RUN)
        elif args.confirm_plan != plan.digest:
            data = _base_result(target, plan, gate, BLOCKED)
            data["errors"] = [{"source": "plan", "reason": "plan_digest_mismatch"}]
            data["summary"] = {"record_count": 0, "error_count": 1}
            logger.event("error", "plan", BLOCKED)
        else:
            logger.event("info", "apply", "STARTED")
            stage = "collect_mtu_consistency"
            old_term = signal.signal(signal.SIGTERM, _signal_interrupt)
            old_int = signal.signal(signal.SIGINT, _signal_interrupt)
            try:
                data = collect_mtu_consistency(target, args, plan, marker)
            finally:
                signal.signal(signal.SIGTERM, old_term)
                signal.signal(signal.SIGINT, old_int)
            data.update(
                {
                    "execution_host": socket.getfqdn(),
                    "tested_revision": target.tested_revision,
                    "target_file": str(target.source_file),
                    "target_source": target.source_kind,
                    "access": gate,
                    "run_id": run_id,
                }
            )
            logger.event(
                "info" if data["status"] == PASS else "warning", "apply", data["status"]
            )
        sys.stdout.write(emit(data, output_mode(args), args.output_dir))
        return exit_code(data["status"])
    except (TargetError, ValueError, OSError, RuntimeError, TypeError, KeyError) as exc:
        logger.event("error", "run", BLOCKED)
        return _failure(args, KIND, stage, str(exc))
    except Exception:  # noqa: BLE001 - malformed provider data needs a JSON failure
        logger.event("error", "run", BLOCKED)
        return _failure(args, KIND, stage, "unexpected_runtime_failure")
    finally:
        logger.close()
