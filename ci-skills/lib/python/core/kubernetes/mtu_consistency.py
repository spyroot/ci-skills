"""Plan and verify physical uplink MTUs through temporary OpenShift debug Pods.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

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
from argparse import Namespace
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Optional

from lib.python.access.access import check_access, kubectl_argv, kubernetes_env, oc_argv
from lib.python.access.credentials import Sources, bind_sources
from lib.python.core.cli import _failure, output_mode
from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.project_binding import resolve_target
from lib.python.core.report import emit, report
from lib.python.core.runtime import (
    CommandResult,
    error_class,
    run_command_tail,
    sanitize,
)
from lib.python.core.target import Target, TargetError, kubernetes_label
from lib.python.sys.status import (
    BLOCKED,
    DRY_RUN,
    PARTIAL,
    PASS,
    PROFILE_BASE,
    exit_code,
)

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
    """Bind one MTU verification to selected nodes and runtime inputs.

    :param namespace: Namespace used for temporary debug Pods.
    :param nodes: Exact node names selected from live inventory.
    :param digest: Confirmation fingerprint for this plan.
    :param inputs: Nonsecret inputs included in the plan fingerprint.

    .. attribute :: namespace
        :type: str

        Namespace used for temporary debug Pods.

    .. attribute :: nodes
        :type: tuple[str, ...]

        Exact node names selected from live inventory.

    .. attribute :: digest
        :type: str

        Confirmation fingerprint for this plan.

    .. attribute :: inputs
        :type: JSONObject

        Nonsecret inputs included in the plan fingerprint.
    """

    namespace: str
    nodes: tuple[str, ...]
    digest: str
    inputs: JSONObject


class EventLogger:
    """Emit bounded, credential-free diagnostics without changing report stdout."""

    def __init__(self, args: Namespace, run_id: str):
        """Open optional private append-only logging for one MTU run.

        :param args: Parsed log file, level, and format settings.
        :param run_id: Identifier attached to every emitted event.
        :raises ValueError: Existing log file grants access beyond its owner.
        :raises OSError: Log file cannot be opened or inspected.
        """  # noqa: DOC503 - os.open and os.fstat propagate filesystem errors.
        self.args = args
        self.run_id = run_id
        self.fd: Optional[int] = None
        if args.log_file:
            path = Path(args.log_file).expanduser()
            self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            if stat.S_IMODE(os.fstat(self.fd).st_mode) & 0o077:
                os.close(self.fd)
                self.fd = None
                raise ValueError("log_file_not_private")

    def event(self, level: str, action: str, result: str) -> None:
        """Write one diagnostic event to stderr and optional private file.

        :param level: Debug, info, warning, or error severity.
        :param action: Operation name recorded in the event.
        :param result: Bounded result text selected by the caller.
        :raises ValueError: A supplied level is outside the configured level order.
        :raises OSError: Writing the optional log file fails.
        """  # noqa: DOC502 - Level lookup and output writes can fail.
        levels = ("debug", "info", "warning", "error")
        if levels.index(level) < levels.index(self.args.log_level):
            return
        item = {
            "timestamp": datetime.now(UTC).isoformat(),
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
        """Close the optional private log descriptor after the run.

        :raises OSError: Closing the descriptor fails.
        """  # noqa: DOC502 - os.close can propagate descriptor errors.
        if self.fd is not None:
            os.close(self.fd)


def _response(result: CommandResult, source: str) -> JSONValue:
    """Decode a successful bounded native response as JSON.

    :param result: Command status and captured output.
    :param source: Query name included in validation errors.
    :returns: Decoded JSON value.
    :raises ValueError: Command failed, output approached its bound, or JSON is
        malformed, nonfinite, or exceeds decoder nesting limits.
    """
    if result.returncode:
        raise ValueError(f"{source}:{error_class(result)}")
    if len(result.stdout.encode("utf-8")) >= MAX_OUTPUT_BYTES - 4:
        raise ValueError(f"{source}:output_limit")
    try:
        return response_decoder().decode(result.stdout)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"{source}:invalid_json") from exc


def physical_uplinks(payload: JSONValue) -> Sequence[JSONObject]:
    """Select physical PCI Ethernet links from host ``ip`` JSON.

    :param payload: Decoded list from ``ip -d -j addr show``.
    :returns: Interface, MTU, state, PCI device, and IPv4 address records sorted
        by interface name.
    :raises TypeError: Link, metadata, or address entries have malformed shapes.
    :raises ValueError: Selected link fields, IPv4 values, or interface names are
        invalid or duplicated.
    """
    if not isinstance(payload, list):
        raise TypeError("links:invalid_list_response")
    selected: MutableSequence[JSONObject] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise TypeError("links:invalid_item")
        if item.get("link_type") != "ether" or item.get("parentbus") != "pci":
            continue
        if not isinstance(item.get("parentdev"), str) or not item["parentdev"]:
            continue
        linkinfo = item.get("linkinfo", {})
        if not isinstance(linkinfo, Mapping):
            raise TypeError("links:invalid_linkinfo")
        if linkinfo.get("info_kind") not in (None, ""):
            continue
        addresses = item.get("addr_info", [])
        if not isinstance(addresses, list):
            raise TypeError("links:invalid_addresses")
        ipv4 = []
        for address in addresses:
            if not isinstance(address, Mapping):
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
    if len({link["interface"] for link in selected}) != len(selected):
        raise ValueError("links:duplicate_interface")
    return sorted(selected, key=lambda link: str(link["interface"]))


def _read_nodes(target: Target, selected: Optional[str]) -> tuple[str, ...]:
    """Read node inventory and optionally require one exact member.

    :param target: Bound Kubernetes target used for the node list query.
    :param selected: Optional exact node name requested by the caller.
    :returns: Selected one-node tuple or all node names sorted.
    :raises ValueError: Command fails, inventory is empty, or a node name is
        invalid, duplicated, or absent from the selected cluster.
    :raises TypeError: Node list response has an invalid envelope.
    :raises TargetError: Bound kubeconfig changes before query construction.
    """  # noqa: DOC503 - Bound-kubeconfig validation runs before native reads.
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
    if not isinstance(payload, Mapping) or not isinstance(payload.get("items"), list):
        raise TypeError("nodes:invalid_list_response")
    names = []
    for item in payload["items"]:
        metadata = item.get("metadata") if isinstance(item, Mapping) else None
        name = metadata.get("name") if isinstance(metadata, Mapping) else None
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
    """Confirm the selected cluster exposes the OpenShift config API.

    :param target: Bound Kubernetes target for the API discovery read.
    :raises ValueError: ``oc`` is unavailable, discovery fails, or the response
        does not identify the expected API resource list.
    :raises TargetError: Bound kubeconfig changes before discovery.
    """  # noqa: DOC503 - Bound-kubeconfig validation runs before native reads.
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
        not isinstance(payload, Mapping)
        or payload.get("kind") != "APIResourceList"
        or payload.get("groupVersion") != "config.openshift.io/v1"
    ):
        raise ValueError("cluster:openshift_api_invalid")


def _read_namespace(target: Target) -> str:
    """Read the selected context namespace for temporary debug Pods.

    :param target: Bound Kubernetes target whose context is queried.
    :returns: Valid DNS-label namespace, using ``default`` if the context omits it.
    :raises ValueError: Context read fails or the namespace is invalid.
    :raises TargetError: Bound kubeconfig changes before query construction.
    """  # noqa: DOC503 - Bound-kubeconfig validation runs before native reads.
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
    """Bind a plan to selected target and binding file bytes.

    :param target: Resolved target carrying source path and optional binding path.
    :returns: SHA-256 digest over the canonical paths and contents in order.
    :raises TargetError: Target source file was not recorded.
    :raises OSError: A selected source file cannot be read.
    """  # noqa: DOC503 - Path.read_bytes propagates selected-source I/O errors.
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


def build_plan(target: Target, args: Namespace) -> MtuPlan:
    """Plan exact OpenShift node reads without creating debug Pods.

    :param target: Bound Kubernetes target with selected context and source files.
    :param args: Optional node filter and requested command timeout.
    :returns: Namespace, selected node names, source-bound inputs, and plan digest.
    :raises ValueError: OpenShift discovery, namespace, or node inventory is
        unavailable or invalid.
    :raises TypeError: Node inventory response is malformed.
    :raises OSError: If a source file is unreadable or a native read cannot start.
    :raises TargetError: Bound source changes or source file is unresolved.
    """  # noqa: DOC503 - Discovery and source fingerprint helpers can fail.
    if target.kubernetes is None:
        raise TargetError("kubernetes_target_missing")
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


def _marked_pods(target: Target, namespace: str, marker: str) -> Sequence[str]:
    """Find temporary debug Pods bearing this invocation's unique marker.

    :param target: Bound Kubernetes target for a namespace Pod list.
    :param namespace: Namespace selected by the MTU plan.
    :param marker: Exact environment marker value assigned to this invocation.
    :returns: Sorted unique names of marked Pods only.
    :raises ValueError: Pod read fails, output is malformed, or a marked name is
        invalid.
    :raises TypeError: Pod list or an item has an invalid shape.
    :raises TargetError: Bound kubeconfig changes before listing.
    """  # noqa: DOC503 - Bound-kubeconfig validation runs before native reads.
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
    if not isinstance(payload, Mapping) or not isinstance(payload.get("items"), list):
        raise TypeError("cleanup_pods:invalid_list_response")
    found = []
    for item in payload["items"]:
        if not isinstance(item, Mapping):
            raise TypeError("cleanup_pods:invalid_item")
        spec = item.get("spec")
        containers = spec.get("containers") if isinstance(spec, Mapping) else None
        if not isinstance(containers, list):
            raise TypeError("cleanup_pods:invalid_item")
        marked = any(
            isinstance(container, Mapping)
            and isinstance(container.get("env"), list)
            and any(
                isinstance(entry, Mapping)
                and entry.get("name") == MARKER
                and entry.get("value") == marker
                for entry in container["env"]
            )
            for container in containers
        )
        if marked:
            metadata = item.get("metadata")
            name = metadata.get("name") if isinstance(metadata, Mapping) else None
            if not isinstance(name, str) or not DNS_NAME.fullmatch(name):
                raise ValueError("cleanup_pods:invalid_marked_name")
            found.append(name)
    return sorted(set(found))


def _cleanup(
    target: Target, namespace: str, marker: str
) -> MutableMapping[str, JSONValue]:
    """Delete only this run's marked debug Pods and verify absence.

    :param target: Bound Kubernetes target for list/delete/read-back operations.
    :param namespace: Namespace used by the MTU plan.
    :param marker: Unique invocation marker identifying candidate Pods.
    :returns: Cleanup status, observed and remaining Pod names, and per-delete
        failures; an exception becomes a blocked result with unknown remainder.
    """
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
    except (TargetError, ValueError, TypeError, OSError, Interrupted) as exc:
        return {
            "status": BLOCKED,
            "verified": False,
            "reason": sanitize(str(exc), 160),
            "remaining_pods": "UNKNOWN",
        }


@dataclass(frozen=True)
class MtuRead:
    """Hold physical-uplink observations and independent native read errors.

    :param records: Node-qualified interface observations from successful reads.
    :param errors: Classified errors for nodes whose observations are unavailable.

    .. attribute :: records
        :type: Sequence[JSONObject]

        Node-qualified interface observations from successful reads.

    .. attribute :: errors
        :type: Sequence[Mapping[str, str]]

        Classified errors for nodes whose observations are unavailable.
    """

    records: Sequence[JSONObject]
    errors: Sequence[Mapping[str, str]]

    def report(
        self,
        target: Target,
        node: Optional[str],
        plan: MtuPlan,
        marker: str,
        cleanup: JSONObject,
    ) -> MutableMapping[str, JSONValue]:
        """Compare observed MTUs per interface and attach cleanup evidence.

        :param target: Bound Kubernetes authority named in the report.
        :param node: Optional exact node filter supplied by the caller.
        :param plan: Namespace and exact selected-node plan used for reads.
        :param marker: Invocation marker identifying temporary debug Pods.
        :param cleanup: Required verified flag and observed cleanup result.
        :returns: Records, per-interface MTU comparisons, actions, and final status.
        :raises ValueError: If called with a record lacking a string node/interface
            or an integer MTU, cleanup lacks a Boolean verified field, or the target has no Kubernetes authority.
        :raises KeyError: If required plan selection fields are absent.
        """  # noqa: DOC503 - Required plan fields are accessed by key.
        verified = cleanup.get("verified")
        if not isinstance(verified, bool):
            # Invalid cleanup evidence uses the report validation error contract.
            raise ValueError("mtu:invalid_cleanup")  # noqa: TRY004
        records = sorted(
            self.records, key=lambda row: (str(row["node"]), str(row["interface"]))
        )
        errors = self.errors
        by_interface: MutableMapping[str, set[int]] = {}
        nodes: set[str] = set()
        for row in records:
            interface, mtu, node_name = (
                row.get("interface"),
                row.get("mtu"),
                row.get("node"),
            )
            if (
                not isinstance(interface, str)
                or not isinstance(node_name, str)
                or type(mtu) is not int
            ):
                raise ValueError("mtu:invalid_observation")
            by_interface.setdefault(interface, set()).add(mtu)
            nodes.add(node_name)
        mtu_values = sorted({mtu for values in by_interface.values() for mtu in values})
        mtu_by_interface = {
            name: sorted(values) for name, values in sorted(by_interface.items())
        }
        nodes_read = sorted(nodes)
        result = report(
            KIND,
            kubernetes_label(target),
            {"node": node, "selection": plan.inputs["selection"]},
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
            "mtu_by_interface": mtu_by_interface,
            "consistent": bool(records)
            and not errors
            and verified
            and not any(len(values) > 1 for values in mtu_by_interface.values()),
        }
        findings = [
            {
                "code": "physical_mtu_mismatch",
                "action": "inspect_node_physical_nic_mtu",
                "interface": name,
                "mtu_values": values,
            }
            for name, values in mtu_by_interface.items()
            if len(values) > 1
        ]
        result["findings"] = findings
        result["condition"] = (
            "UNKNOWN"
            if errors or not verified
            else "INCONSISTENT"
            if findings
            else "CONSISTENT"
        )
        result["status"] = (
            BLOCKED
            if not records or not verified
            else PARTIAL
            if errors or findings
            else PASS
        )
        if errors or not verified:
            result["safe_next_step"] = (
                "Review failed node reads or temporary Pod cleanup on the selected target."
            )
        return result


def _read_one(
    target: Target, plan: MtuPlan, node: str, marker: str, timeout: int
) -> MtuRead:
    """Run one OpenShift node debug read for physical uplinks.

    :param target: Bound Kubernetes target for ``oc debug``.
    :param plan: Confirmed namespace and node inventory.
    :param node: Exact node selected by the plan.
    :param marker: Unique run marker placed in the debug Pod environment.
    :param timeout: Maximum debug command runtime in seconds.
    :returns: Fixed records/errors observation; a failed read has no records and
        one classified error. Successful empty interface inventory is an error.
    :raises TargetError: Bound kubeconfig changes before command construction.
    :raises OSError: If the native debug process cannot be started.
    """  # noqa: DOC503 - Local read errors become evidence; setup can fail.
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
        return MtuRead([{"node": node, **link} for link in links], [])
    except (ValueError, TypeError) as exc:
        return MtuRead(
            [], [{"source": f"node/{node}", "reason": sanitize(str(exc), 160)}]
        )


def collect_mtu_consistency(
    target: Target, args: Namespace, plan: MtuPlan, marker: str
) -> MutableMapping[str, JSONValue]:
    """Read planned node MTUs and clean every marked temporary debug Pod.

    :param target: Bound Kubernetes target used by reads and cleanup.
    :param args: Node filter and per-node command timeout.
    :param plan: Confirmed namespace, nodes, and digest.
    :param marker: Unique invocation marker used to identify cleanup candidates.
    :returns: MTU records, mismatch findings, cleanup read-back, and ``PASS``,
        ``PARTIAL``, or ``BLOCKED`` status.
    :raises ValueError: If the plan has no nodes, observations are malformed, or
        signal handling is attempted outside the main thread.
    """  # noqa: DOC502 - Pool sizing and final observation validation can fail.
    records: MutableSequence[JSONObject] = []
    errors: MutableSequence[Mapping[str, str]] = []
    executor = ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(plan.nodes)))
    try:
        futures = {
            executor.submit(_read_one, target, plan, node, marker, args.timeout): node
            for node in plan.nodes
        }
        for future in as_completed(futures):
            node = futures[future]
            try:
                observation = future.result()
                records.extend(observation.records)
                errors.extend(observation.errors)
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
        # A second interrupt must not leave this run's privileged debug Pods.
        previous_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
        previous_term = signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            executor.shutdown(wait=True, cancel_futures=True)
            cleanup = _cleanup(target, plan.namespace, marker)
        finally:
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)

    return MtuRead(records, errors).report(target, args.node, plan, marker, cleanup)


def _base_result(
    target: Target, plan: MtuPlan, gate: JSONObject, status: str
) -> MutableMapping[str, JSONValue]:
    """Build a plan-stage MTU report without node mutations.

    :param target: Bound Kubernetes target selected for the run.
    :param plan: Exact node and namespace plan.
    :param gate: Access-check evidence attached to the report.
    :param status: Planned or blocked report status.
    :returns: Report with plan digest, node selection, side-effect declaration,
        cleanup intent, and access evidence.
    """
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


def _signal_interrupt(_signum: int, _frame: Optional[FrameType]) -> None:
    """Convert a process signal into the MTU cleanup exception.

    :param _signum: Signal number supplied by Python.
    :param _frame: Interrupted frame supplied by Python.
    :raises Interrupted: Always, so the active run enters cleanup.
    """
    raise Interrupted("interrupted")


def run(args: Namespace) -> int:
    """Plan an MTU read or apply an exact confirmed plan.

    :param args: Parsed target, output, logging, selection, timeout, and
        confirmation arguments.
    :returns: Zero for a valid plan or complete pass; blocked or partial runs
        return the shared nonzero exit code.
    """
    if args.describe:
        from lib.python.core.catalog import describe

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
                "info" if data["status"] == PASS else "warning",
                "apply",
                str(data["status"]),
            )
        sys.stdout.write(emit(data, output_mode(args), args.output_dir))
        return exit_code(str(data["status"]))
    except (TargetError, ValueError, OSError, RuntimeError, TypeError, KeyError) as exc:
        logger.event("error", "run", BLOCKED)
        return _failure(args, KIND, stage, str(exc))
    except Exception:  # noqa: BLE001 - malformed provider data needs a JSON failure
        logger.event("error", "run", BLOCKED)
        return _failure(args, KIND, stage, "unexpected_runtime_failure")
    finally:
        logger.close()
