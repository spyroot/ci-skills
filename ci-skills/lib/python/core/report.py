"""Versioned evidence reports and paired human/machine rendering."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from argparse import Namespace
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from lib.python.core.runtime import redact_tree, sanitize
from lib.python.sys.status import PARTIAL, PASS


def _nested(value: object, search: str | None, *, limit: int = 32) -> Sequence[str]:
    """Render bounded evidence lines, preserving lines matched by a search filter."""
    lines = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True).splitlines()
    if search:
        matched = [line for line in lines if search.casefold() in line.casefold()]
        lines = matched or lines
    return [sanitize(line, 240) for line in lines[:limit]]


def _ceph_tree_lines(
    roots: Sequence[Mapping[str, object]], *, limit: int = 128
) -> Sequence[str]:
    """Render the validated CRUSH hierarchy without flooding a terminal.

    :param roots:
    :param limit:
    :return:
    """
    lines = []
    pending: list[tuple[Mapping[str, object], int]] = [
        (node, 0) for node in reversed(roots)
    ]
    while pending and len(lines) < limit:
        node, depth = pending.pop()
        label = f"{node['name']} ({node['type']}, id={node['id']}"
        if node.get("status") is not None:
            label += f", status={node['status']}"
        lines.append("  " + "  " * depth + sanitize(label + ")", 240))
        pending.extend(
            (child, depth + 1) for child in reversed(_rows(node["children"]))
        )
    if pending:
        lines.append("  ... additional hierarchy nodes omitted")

    return lines


def report(
    kind: str,
    target: str,
    filters: Mapping[str, object],
    records: Sequence[Mapping[str, object]],
    errors: Sequence[Mapping[str, str]],
) -> Mapping[str, object]:
    return {
        "schema_version": "1.0",
        "kind": kind,
        "captured_at": datetime.now(UTC).isoformat(),
        "target": target,
        "filters": filters,
        "records": records,
        "errors": errors,
        "status": PARTIAL if errors else PASS,
        "summary": {"record_count": len(records), "error_count": len(errors)},
    }


RECORD_FIELDS = (
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
RECORD_LIMIT = 128


def _mapping(value: object) -> Mapping[str, object]:
    """Narrow a mapping field from an already structured report."""
    return cast(Mapping[str, object], value)


def _rows(value: object) -> Sequence[Mapping[str, object]]:
    """Narrow a record sequence from an already structured report."""
    return cast(Sequence[Mapping[str, object]], value)


def _strings(value: object) -> Sequence[str]:
    """Narrow a string sequence from an already structured report."""
    return cast(Sequence[str], value)


def _record_summary(item: Mapping[str, object]) -> list[str]:
    """Render the common selected fields of one diagnostic record."""
    parts = (f"{key}={item[key]}" for key in RECORD_FIELDS if item.get(key) is not None)
    return ["  " + "  ".join(parts)[:240]]


def _runner_header(data: Mapping[str, object]) -> list[str]:
    """Render runner operation identity and the optional related job."""
    lines = [
        f"Operation: {data.get('operation', 'unknown')}",
        f"Plan: {data.get('plan_digest', 'unavailable')}",
    ]
    related = data.get("related_job")
    if isinstance(related, dict):
        lines.append(
            f"Related job: id={related.get('id')} status={related.get('status')} "
            f"runner_id={related.get('runner_id')}"
        )
        if related.get("failure_reason"):
            lines.append("  failure_reason=" + sanitize(related["failure_reason"], 120))
    requested = _mapping(data.get("plan") or {}).get("requested_tags")
    if requested is not None:
        lines.append("Requested tags: " + ",".join(_strings(requested)))
    return lines


def _runner_record(item: Mapping[str, object], operation: str) -> list[str]:
    """Render one runner read or mutation without a discarded generic summary."""
    if operation in {"get", "list"}:
        lines = [
            (
                f"  runner_id={item.get('id', 'unknown')}"
                f"  status={item.get('status', 'unknown')}"
                f"  online={item.get('online', 'unknown')}"
                f"  access={item.get('access_level', 'unknown')}"
                f"  shared={item.get('is_shared', 'unknown')}"
                f"  tags={','.join(_strings(item.get('tag_list', [])))}"
            ),
            f"    projects={item.get('projects', [])}",
        ]
    else:
        lines = [
            (
                f"  runner_id={item.get('id', 'unknown')}"
                f"  action={item.get('action', 'unknown')}"
                f"  verified={str(item.get('verified', False)).lower()}"
            )
        ]
        if operation == "delete":
            lines.append(f"    absence={item.get('after', {})}")
    for label in ("before_tags", "after_tags"):
        if label in item:
            lines.append(f"    {label}=" + ",".join(_strings(item[label])))
    return lines


def _job_list_record(item: Mapping[str, object]) -> list[str]:
    """Render the bounded fields of one listed job."""
    lines = [
        (
            f"  id={item['id']}  name={sanitize(str(item['name']), 80)}"
            f"  stage={sanitize(str(item['stage']), 40)}"
            f"  status={sanitize(str(item['status']), 24)}"
            f"  ref={sanitize(str(item['ref']), 48)}"
        )
    ]
    if item.get("failure_reason"):
        lines.append(f"    failure_reason={sanitize(str(item['failure_reason']), 120)}")
    if item.get("stuck"):
        lines.append("    stuck=true")
    return lines


def _job_details(item: Mapping[str, object], search: str | None) -> list[str]:
    """Render job context and the matching tail of its trace."""
    lines = [f"    failure_reason={item.get('failure_reason')}"]
    for label in ("pipeline", "runner"):
        if item.get(label) is not None:
            lines.append(f"    {label}:")
            lines.extend(
                "      " + line for line in _nested(item[label], search, limit=12)
            )
    trace = str(item.get("trace_tail") or "").splitlines()
    if search:
        query = search.casefold()
        trace = [line for line in trace if query in line.casefold()]
    lines.append("    trace_tail:")
    lines.extend("      " + sanitize(line, 240) for line in trace[-40:])
    return lines


def _pipeline_details(item: Mapping[str, object]) -> list[str]:
    """Render total job progress and stage progress from one pipeline record."""
    progress = _mapping(item.get("progress") or {})
    lines = [
        f"    jobs: {progress.get('terminal', 0)}/{progress.get('total', 0)} terminal"
    ]
    for stage in _rows(item.get("stages", [])):
        lines.append(
            "    stage="
            + sanitize(str(stage.get("name", "")), 120)
            + f" terminal={stage.get('terminal', 0)}/{stage.get('total', 0)}"
        )
    return lines


def _storage_details(item: Mapping[str, object], search: str | None) -> list[str]:
    """Render related storage objects using the shared nested formatter."""
    lines = []
    for label in ("pods", "attachments", "controllers"):
        if item.get(label):
            lines.append(f"    {label}:")
            for nested in _rows(item[label]):
                lines.extend(
                    "      " + line for line in _nested(nested, search, limit=12)
                )
    return lines


def _cilium_details(
    item: Mapping[str, object], kind: str, search: str | None
) -> list[str]:
    """Render Cilium health or bounded node findings."""
    if kind == "cilium_status" and item.get("health") is not None:
        return ["    health:"] + [
            "      " + line for line in _nested(item["health"], search, limit=32)
        ]
    if kind == "cilium_node" and item.get("findings"):
        lines = ["    findings:"]
        for finding in _rows(item["findings"])[:32]:
            lines.extend("      " + line for line in _nested(finding, search, limit=8))
        return lines
    return []


def _record_renderer(
    data: Mapping[str, object],
) -> Callable[[Mapping[str, object]], list[str]]:
    """Select record formatting once before traversing the report's records."""
    kind = str(data.get("kind", ""))
    operation = str(data.get("operation", ""))
    search = cast(str | None, _mapping(data.get("filters") or {}).get("search"))
    if kind == "gitlab_runner":
        return lambda item: _runner_record(item, operation)
    if kind == "gitlab_job" and operation == "list":
        return _job_list_record
    details: Callable[[Mapping[str, object]], list[str]]
    if kind == "gitlab_job":
        details = lambda item: _job_details(item, search)
    elif kind == "gitlab_pipeline":
        details = _pipeline_details
    elif kind == "storage_report":
        details = lambda item: _storage_details(item, search)
    elif kind in {"cilium_status", "cilium_node"}:
        details = lambda item: _cilium_details(item, kind, search)
    else:
        return _record_summary
    return lambda item: _record_summary(item) + details(item)


def _mtu_lines(data: Mapping[str, object]) -> list[str]:
    """Render selected physical interfaces and MTU findings."""
    records = _rows(data.get("records", []))
    lines = [
        f"Physical NIC MTU: {data.get('status', 'UNKNOWN')}",
        f"Target: {data.get('target', 'unknown')}",
        f"Plan: {data.get('plan_digest', 'unavailable')}",
        f"Nodes: {len(_strings(data.get('planned_nodes', [])))} selected",
        "Selection: PCI Ethernet interfaces with an IPv4 address",
        "Apply creates temporary oc debug Pods and verifies cleanup.",
    ]
    if records:
        lines.append(
            "NODE                      INTERFACE     PCI DEVICE       MTU   STATE   IPv4"
        )
    for row in records[:RECORD_LIMIT]:
        lines.append(
            f"{sanitize(str(row['node']), 25):25} "
            f"{sanitize(str(row['interface']), 13):13} "
            f"{sanitize(str(row['pci_device']), 16):16} "
            f"{row['mtu']:5} {sanitize(str(row['oper_state']), 7):7} "
            f"{sanitize(','.join(_strings(row['ipv4_addresses'])), 48)}"
        )
    if len(records) > RECORD_LIMIT:
        lines.append(f"... {len(records) - RECORD_LIMIT} more interfaces in JSON")
    for finding in _rows(data.get("findings", [])):
        lines.append(f"Finding: {finding['code']} MTUs={finding['mtu_values']}")
    return lines


def _access_lines(data: Mapping[str, object]) -> list[str]:
    """Render one access surface per line with its actionable explanation."""
    lines = [f"Access: {data.get('status', 'UNKNOWN')}"]
    for name, value in _mapping(data.get("surfaces", {})).items():
        surface = _mapping(value)
        lines.append(
            f"  {name}: {surface.get('status', 'DRY_RUN')} {surface.get('target', '')}"
        )
        if surface.get("reason"):
            lines.append(f"    reason: {surface['reason']}")
        if surface.get("next_step"):
            lines.append(f"    next: {surface['next_step']}")
    return lines


def _ceph_lines(data: Mapping[str, object]) -> list[str]:
    """Render Ceph health, down OSD count, hierarchy, and suggested actions."""
    lines = [
        f"Ceph health: {data.get('health', 'UNKNOWN')}",
        f"Inactive PGs: {len(_rows(data.get('inactive_pgs', [])))}",
        f"OSDs down: {sum(item.get('status') == 'down' for item in _rows(data.get('osds', [])))}",
    ]
    if "osd_tree" in data:
        lines.append("Ceph hierarchy:")
        lines.extend(_ceph_tree_lines(_rows(data["osd_tree"])))
    lines.extend(f"Action: {action}" for action in _strings(data.get("actions", [])))
    return lines


def _diagnostic_lines(data: Mapping[str, object]) -> list[str]:
    """Render a diagnostic header and traverse its records once."""
    records = _rows(data.get("records", []))
    lines = [
        f"{data.get('kind', 'diagnostic')}: {data.get('status', 'UNKNOWN')}",
        f"Target: {data.get('target', 'unknown')}",
        f"Records: {len(records)}",
    ]
    if data.get("kind") == "gitlab_runner":
        lines.extend(_runner_header(data))
    render_record = _record_renderer(data)
    for item in records:
        lines.extend(render_record(item))
    if data.get("inventory"):
        inventory = _mapping(data["inventory"])
        lines.append(
            "Inventory: "
            + ", ".join(f"{key}={value}" for key, value in sorted(inventory.items()))
        )
    if data.get("kind") == "ceph_cluster":
        lines.extend(_ceph_lines(data))
    return lines


def human(data: Mapping[str, object]) -> str:
    """Render bounded operator output from one structured result.

    :param data: Versioned command result or operation plan.
    :returns: Human-readable status and selected evidence fields.
    """
    kind = data.get("kind")
    if kind == "access_check":
        lines = _access_lines(data)
    else:
        lines = (
            _mtu_lines(data)
            if kind == "k8s_verify_mtu_consistency"
            else _diagnostic_lines(data)
        )
        for error in _rows(data.get("errors", [])):
            lines.append(f"Error: {error.get('source')}: {error.get('reason')}")
        if kind == "k8s_verify_mtu_consistency" and data.get("cleanup"):
            lines.append(
                f"Cleanup: {_mapping(data['cleanup']).get('status', 'UNKNOWN')}"
            )
    lines.extend(_report_file_lines(data))
    if kind == "gitlab_job" and data.get("operation") == "list":
        lines.append(f"Truncated: {str(data.get('truncated', False)).lower()}")
    return "\n".join(lines) + "\n"


def _report_file_lines(data: Mapping[str, object]) -> list[str]:
    files = _mapping(data.get("report_files") or {})
    if not files:
        return []
    return [
        f"Report JSON: {sanitize(str(files['json']), 1024)}",
        f"Report text: {sanitize(str(files['text']), 1024)}",
    ]


def emit(data: Mapping[str, object], mode: str, output_dir: str | None = None) -> str:
    """Redact and render a report, optionally persisting paired files.

    :param data: In-memory collection to redact before serialization.
    :param mode: Selected JSON, YAML, or human output mode.
    :param output_dir: Directory for paired reports; None leaves the report in memory.
    :returns: Redacted report text in the selected format.
    :raises RuntimeError: If YAML output is selected without PyYAML.
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


def emit_result(result: Mapping[str, object], args: Namespace) -> int:
    """Emit the workflow report through the existing output facilities.

    :param result: State-owned operation report from the executor.
    :param args: Calling workflow's output, logging, and receipt arguments.
    :returns: Canonical report exit code.
    :raises ValueError: If the workflow did not produce a report.
    """
    import sys

    from .cli import log_event, output_mode
    from .portable import write_portable_receipt
    from lib.python.sys.status import exit_code

    data = result
    if not data:
        raise ValueError("workflow_report_missing")
    if getattr(args, "describe", False):
        sys.stdout.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        return 0
    if getattr(args, "receipt_out", None):
        write_portable_receipt(data, args.receipt_out)
    rendered = emit(data, output_mode(args), getattr(args, "output_dir", None))
    log_event(args, data["kind"], "result", data["status"])
    sys.stdout.write(rendered)
    return exit_code(data["status"])


def plan_digest(document: Mapping[str, object]) -> str:
    """Fingerprint normalized nonsecret operation inputs.

    :param document: Inputs and target identities bound by the owning action.
    :returns: SHA-256 of canonical JSON, shared by provider workflows.
    """
    encoded = json.dumps(dict(document), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
