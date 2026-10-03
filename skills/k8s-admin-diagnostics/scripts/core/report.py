"""Versioned evidence reports and paired human/machine rendering."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runtime import redact_tree, sanitize
from .status import PARTIAL, PASS


def _nested(value: Any, search: str | None, *, limit: int = 32) -> list[str]:
    """Render bounded evidence lines, preserving lines matched by a search filter."""
    lines = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True).splitlines()
    if search:
        matched = [line for line in lines if search.casefold() in line.casefold()]
        lines = matched or lines
    return [sanitize(line, 240) for line in lines[:limit]]


def _ceph_tree_lines(roots: list[dict[str, Any]], *, limit: int = 128) -> list[str]:
    """Render the validated CRUSH hierarchy without flooding a terminal."""
    lines = []
    pending = [(node, 0) for node in reversed(roots)]
    while pending and len(lines) < limit:
        node, depth = pending.pop()
        label = f"{node['name']} ({node['type']}, id={node['id']}"
        if node.get("status") is not None:
            label += f", status={node['status']}"
        lines.append("  " + "  " * depth + sanitize(label + ")", 240))
        pending.extend((child, depth + 1) for child in reversed(node["children"]))
    if pending:
        lines.append("  ... additional hierarchy nodes omitted")
    return lines


def report(
    kind: str,
    target: str,
    filters: dict[str, Any],
    records: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": kind,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "target": target,
        "filters": filters,
        "records": records,
        "errors": errors,
        "status": PARTIAL if errors else PASS,
        "summary": {"record_count": len(records), "error_count": len(errors)},
    }


def human(data: dict[str, Any]) -> str:
    if data.get("kind") == "k8s_verify_mtu_consistency":
        records = data.get("records", [])
        lines = [
            f"Physical NIC MTU: {data.get('status', 'UNKNOWN')}",
            f"Target: {data.get('target', 'unknown')}",
            f"Plan: {data.get('plan_digest', 'unavailable')}",
            f"Nodes: {len(data.get('planned_nodes', []))} selected",
            "Selection: PCI Ethernet interfaces with an IPv4 address",
            "Apply creates temporary oc debug Pods and verifies cleanup.",
        ]
        if records:
            lines.append(
                "NODE                      INTERFACE     PCI DEVICE       MTU   STATE   IPv4"
            )
            for row in records[:128]:
                lines.append(
                    f"{sanitize(row['node'], 25):25} "
                    f"{sanitize(row['interface'], 13):13} "
                    f"{sanitize(row['pci_device'], 16):16} "
                    f"{row['mtu']:5} "
                    f"{sanitize(row['oper_state'], 7):7} "
                    f"{sanitize(','.join(row['ipv4_addresses']), 48)}"
                )
            if len(records) > 128:
                lines.append(f"... {len(records) - 128} more interfaces in JSON")
        for finding in data.get("findings", []):
            lines.append(f"Finding: {finding['code']} MTUs={finding['mtu_values']}")
        for error in data.get("errors", []):
            lines.append(f"Error: {error.get('source')}: {error.get('reason')}")
        if cleanup := data.get("cleanup"):
            lines.append(f"Cleanup: {cleanup.get('status', 'UNKNOWN')}")
        lines.extend(_report_file_lines(data))
        return "\n".join(lines) + "\n"
    lines = [
        f"{data.get('kind', 'diagnostic')}: {data.get('status', 'UNKNOWN')}",
        f"Target: {data.get('target', 'unknown')}",
        f"Records: {len(data.get('records', []))}",
    ]
    for item in data.get("records", []):
        fields = (
            "timestamp",
            "first_timestamp",
            "namespace",
            "kind",
            "name",
            "node",
            "nodes",
            "phase",
            "role",
            "osd_id",
            "ready",
            "status",
            "storage_class",
            "volume",
            "reason",
            "message",
            "classification",
            "action",
            "pod_uid",
        )
        parts = [f"{key}={item[key]}" for key in fields if item.get(key) is not None]
        lines.append("  " + "  ".join(parts)[:240])
        kind = data.get("kind")
        search = (data.get("filters") or {}).get("search")
        if kind == "gitlab_job":
            lines.append(f"    failure_reason={item.get('failure_reason')}")
            for label in ("pipeline", "runner"):
                if item.get(label) is not None:
                    lines.append(f"    {label}:")
                    lines.extend(
                        "      " + line
                        for line in _nested(item[label], search, limit=12)
                    )
            trace_lines = (item.get("trace_tail") or "").splitlines()
            if search:
                trace_lines = [
                    line for line in trace_lines if search.casefold() in line.casefold()
                ]
            lines.append("    trace_tail:")
            lines.extend("      " + sanitize(line, 240) for line in trace_lines[-40:])
        elif kind == "gitlab_pipeline":
            progress = item.get("progress") or {}
            lines.append(
                "    jobs: "
                f"{progress.get('terminal', 0)}/{progress.get('total', 0)} terminal"
            )
            for stage in item.get("stages", []):
                lines.append(
                    "    stage="
                    + sanitize(str(stage.get("name", "")), 120)
                    + f" terminal={stage.get('terminal', 0)}/{stage.get('total', 0)}"
                )
        elif kind == "storage_report":
            for label in ("pods", "attachments", "controllers"):
                if item.get(label):
                    lines.append(f"    {label}:")
                    for nested_item in item[label]:
                        lines.extend(
                            "      " + line
                            for line in _nested(nested_item, search, limit=12)
                        )
        elif kind == "cilium_status" and item.get("health") is not None:
            lines.append("    health:")
            lines.extend(
                "      " + line for line in _nested(item["health"], search, limit=32)
            )
        elif kind == "cilium_node" and item.get("findings"):
            lines.append("    findings:")
            for finding in item["findings"][:32]:
                lines.extend(
                    "      " + line for line in _nested(finding, search, limit=8)
                )
    if data.get("inventory"):
        lines.append(
            "Inventory: "
            + ", ".join(
                f"{key}={value}" for key, value in sorted(data["inventory"].items())
            )
        )
    if data.get("kind") == "ceph_cluster":
        lines.append(f"Ceph health: {data.get('health', 'UNKNOWN')}")
        lines.append(f"Inactive PGs: {len(data.get('inactive_pgs', []))}")
        lines.append(
            f"OSDs down: {sum(item.get('status') == 'down' for item in data.get('osds', []))}"
        )
        if "osd_tree" in data:
            lines.append("Ceph hierarchy:")
            lines.extend(_ceph_tree_lines(data["osd_tree"]))
        for action in data.get("actions", []):
            lines.append(f"Action: {action}")
    for error in data.get("errors", []):
        lines.append(f"Error: {error.get('source')}: {error.get('reason')}")
    if data.get("kind") == "access_check":
        lines = [f"Access: {data.get('status', 'UNKNOWN')}"]
        for name, surface in data.get("surfaces", {}).items():
            lines.append(
                f"  {name}: {surface.get('status', 'DRY_RUN')} {surface.get('target', '')}"
            )
            if surface.get("reason"):
                lines.append(f"    reason: {surface['reason']}")
            if surface.get("next_step"):
                lines.append(f"    next: {surface['next_step']}")
    lines.extend(_report_file_lines(data))
    return "\n".join(lines) + "\n"


def _report_file_lines(data: dict[str, Any]) -> list[str]:
    files = data.get("report_files") or {}
    if not files:
        return []
    return [
        f"Report JSON: {sanitize(files['json'], 1024)}",
        f"Report text: {sanitize(files['text'], 1024)}",
    ]


def emit(data: dict[str, Any], mode: str, output_dir: str | None = None) -> str:
    """Render one in-memory collection and optionally persist paired reports.

    Redaction happens HERE, once, before anything is serialized or written, so
    every output path is covered: the returned JSON, YAML and human text, and
    both files under an output directory. Redacting inside one renderer would
    leave the others raw.
    """
    data = redact_tree(data)
    if mode == "yaml":
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is required for --yaml") from exc
    if output_dir:
        destination = Path(output_dir).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9_-]", "_", str(data.get("kind") or "report"))
        run_directory = destination / f"{stem}-{uuid.uuid4().hex}"
        data["report_files"] = {
            "json": str(run_directory / f"{stem}.json"),
            "text": str(run_directory / f"{stem}.txt"),
        }
    pretty_json = json.dumps(data, indent=2, sort_keys=True) + "\n"
    human_text = human(data)
    if output_dir:
        temporary = Path(tempfile.mkdtemp(prefix=f".{stem}-", dir=destination))
        try:
            (temporary / f"{stem}.json").write_text(pretty_json, encoding="utf-8")
            (temporary / f"{stem}.txt").write_text(human_text, encoding="utf-8")
            os.replace(temporary, run_directory)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    if mode == "json":
        return pretty_json
    if mode == "yaml":
        return yaml.safe_dump(data, sort_keys=True, allow_unicode=True)
    return human_text
