"""Versioned evidence reports and paired human/machine rendering.

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
import tempfile
import uuid
from argparse import Namespace
from collections.abc import Callable, Mapping, MutableMapping, MutableSequence, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json,
    json_object,
    json_objects,
    json_string,
    json_strings,
)
from lib.python.core.runtime import redact_tree, sanitize
from lib.python.sys.status import PARTIAL, PASS


def _nested(
    value: JSONValue, search: Optional[str], *, limit: int = 32
) -> Sequence[str]:
    """Render a bounded selection of sanitized JSON evidence lines.

    :param value: JSON-compatible evidence to render.
    :param search: Case-insensitive line filter; unmatched filters retain all lines.
    :param limit: Maximum number of rendered lines.
    :returns: Sanitized lines, each limited to 240 characters.
    :raises TypeError: If the evidence contains a nonserializable value.
    :raises ValueError: If caller-supplied containers form a JSON cycle.
    :raises RecursionError: If supplied evidence exceeds serializer nesting limits.
    """  # noqa: DOC502 - json.dumps rejects unsupported values and cycles.
    lines = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True).splitlines()
    if search:
        matched = [line for line in lines if search.casefold() in line.casefold()]
        lines = matched or lines
    return [sanitize(line, 240) for line in lines[:limit]]


def _ceph_tree_lines(roots: Sequence[JSONObject], *, limit: int = 128) -> Sequence[str]:
    """Render bounded CRUSH hierarchy lines for a human report.

    :param roots: Validated tree roots with names, IDs, types, and child nodes.
    :param limit: Maximum number of hierarchy nodes to render.
    :returns: Indented node lines and an omission marker when nodes remain.
    :raises KeyError: If a hierarchy node lacks its name, type, ID, or children.
    :raises TypeError: If hierarchy children are not an array of object records.
    """  # noqa: DOC502 - Required field indexing and shape readers can fail.
    lines: MutableSequence[str] = []
    pending: MutableSequence[tuple[JSONObject, int]] = [
        (node, 0) for node in reversed(roots)
    ]
    while pending and len(lines) < limit:
        node, depth = pending.pop()
        label = f"{node['name']} ({node['type']}, id={node['id']}"
        if node.get("status") is not None:
            label += f", status={node['status']}"
        lines.append("  " + "  " * depth + sanitize(label + ")", 240))
        pending.extend(
            (child, depth + 1) for child in reversed(json_objects(node["children"]))
        )
    if pending:
        lines.append("  ... additional hierarchy nodes omitted")

    return lines


def report(
    kind: str,
    target: str,
    filters: JSONObject,
    records: Sequence[JSONObject],
    errors: Sequence[JSONObject],
) -> MutableMapping[str, JSONValue]:
    """Construct a versioned diagnostic result from collected evidence.

    :param kind: Diagnostic result kind.
    :param target: Selected authority or resource identifier.
    :param filters: Filters used by the collection operation.
    :param records: Collected evidence records.
    :param errors: Classified collection failures.
    :returns: Mutable report with a capture timestamp, records, errors, summary
        counts, and PASS status when no errors exist, otherwise PARTIAL.
    """
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


def _record_summary(item: JSONObject) -> Sequence[str]:
    """Render the common selected fields of one diagnostic record.

    :param item: Record containing any of the names declared in RECORD_FIELDS.
    :returns: One bounded line of present, non-null field names and values.
    """
    parts = (f"{key}={item[key]}" for key in RECORD_FIELDS if item.get(key) is not None)
    return ["  " + "  ".join(parts)[:240]]


def _runner_header(data: JSONObject) -> Sequence[str]:
    """Render runner operation identity and related-job context.

    :param data: Runner report with operation, plan, and optional related job.
    :returns: Header lines including requested tags and available job failure detail.
    :raises TypeError: If the plan is not an object or requested tags are not strings.
    """  # noqa: DOC502 - Shared JSON shape readers reject malformed fields.
    lines = [
        f"Operation: {data.get('operation', 'unknown')}",
        f"Plan: {data.get('plan_digest', 'unavailable')}",
    ]
    related = data.get("related_job")
    if isinstance(related, Mapping):
        lines.append(
            f"Related job: id={related.get('id')} status={related.get('status')} "
            f"runner_id={related.get('runner_id')}"
        )
        if related.get("failure_reason"):
            lines.append(
                "  failure_reason=" + sanitize(str(related["failure_reason"]), 120)
            )
    requested = json_object(data.get("plan") or {}).get("requested_tags")
    if requested is not None:
        lines.append("Requested tags: " + ",".join(json_strings(requested)))
    return lines


def _runner_record(item: JSONObject, operation: str) -> Sequence[str]:
    """Render one runner read or mutation record.

    :param item: Runner read-back fields or mutation evidence.
    :param operation: GitLab runner verb that determines the record format.
    :returns: Lines describing runner state, mutation verification, or deletion
        absence, followed by before/after tags when present.
    :raises TypeError: If runner tags or before/after tags are not arrays of strings.
    """  # noqa: DOC502 - Shared JSON shape readers reject malformed fields.
    if operation in {"get", "list"}:
        lines = [
            (
                f"  runner_id={item.get('id', 'unknown')}"
                f"  status={item.get('status', 'unknown')}"
                f"  online={item.get('online', 'unknown')}"
                f"  access={item.get('access_level', 'unknown')}"
                f"  shared={item.get('is_shared', 'unknown')}"
                f"  tags={','.join(json_strings(item.get('tag_list', [])))}"
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
            lines.append(f"    {label}=" + ",".join(json_strings(item[label])))
    return lines


def _job_list_record(item: JSONObject) -> Sequence[str]:
    """Render the selected fields of one listed GitLab job.

    :param item: Job record with ID, name, stage, status, and ref.
    :returns: Bounded job summary and available failure or stuck-state lines.
    :raises KeyError: A required job identity or status field is absent.
    """  # noqa: DOC502 - Required field indexing and shape readers can fail.
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


def _job_details(item: JSONObject, search: Optional[str]) -> Sequence[str]:
    """Render related pipeline/runner evidence and a bounded job trace tail.

    :param item: Job record with failure, related objects, and retained trace text.
    :param search: Case-insensitive text filter, or ``None`` for the full tail.
    :returns: Detail lines with up to forty matching trace lines.
    :raises TypeError: If related pipeline or runner evidence cannot be serialized.
    :raises ValueError: If related evidence contains a JSON cycle.
    :raises RecursionError: If related evidence exceeds serializer nesting limits.
    """  # noqa: DOC502 - Nested record serialization and shape readers can fail.
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


def _pipeline_details(item: JSONObject) -> Sequence[str]:
    """Render total job progress and each pipeline stage's progress.

    :param item: Pipeline record containing progress totals and stage records.
    :returns: Lines showing terminal versus total jobs for the pipeline and stages.
    :raises TypeError: If progress is not an object or stages are not object records.
    """  # noqa: DOC502 - Shared JSON shape readers reject malformed fields.
    progress = json_object(item.get("progress") or {})
    lines = [
        f"    jobs: {progress.get('terminal', 0)}/{progress.get('total', 0)} terminal"
    ]
    for stage in json_objects(item.get("stages", [])):
        lines.append(
            "    stage="
            + sanitize(str(stage.get("name", "")), 120)
            + f" terminal={stage.get('terminal', 0)}/{stage.get('total', 0)}"
        )
    return lines


def _storage_details(item: JSONObject, search: Optional[str]) -> Sequence[str]:
    """Render the pods, attachments, and controllers linked to a volume.

    :param item: Storage record containing the related resource lists.
    :param search: Evidence-line filter, or ``None`` to retain each bounded detail.
    :returns: Nested evidence lines for nonempty related resource lists.
    :raises TypeError: If related resources are not object records or are not serializable.
    :raises ValueError: If related resource evidence contains a JSON cycle.
    :raises RecursionError: If related resources exceed serializer nesting limits.
    """  # noqa: DOC502 - Nested record serialization and shape readers can fail.
    lines = []
    for label in ("pods", "attachments", "controllers"):
        if item.get(label):
            lines.append(f"    {label}:")
            for nested in json_objects(item[label]):
                lines.extend(
                    "      " + line for line in _nested(nested, search, limit=12)
                )
    return lines


def _cilium_details(
    item: JSONObject, kind: str, search: Optional[str]
) -> Sequence[str]:
    """Render agent health or bounded Cilium node findings.

    :param item: Cilium agent or node diagnostic record.
    :param kind: ``cilium_status`` for health or ``cilium_node`` for findings.
    :param search: Evidence-line filter, or ``None`` for unfiltered bounded details.
    :returns: Health or finding lines, or an empty list when no detail applies.
    :raises TypeError: If findings are not object records or evidence is not serializable.
    :raises ValueError: If nested Cilium evidence contains a JSON cycle.
    :raises RecursionError: If nested evidence exceeds serializer nesting limits.
    """  # noqa: DOC502 - Nested record serialization and shape readers can fail.
    if kind == "cilium_status" and item.get("health") is not None:
        return ["    health:"] + [
            "      " + line for line in _nested(item["health"], search, limit=32)
        ]
    if kind == "cilium_node" and item.get("findings"):
        lines = ["    findings:"]
        for finding in json_objects(item["findings"])[:32]:
            lines.extend("      " + line for line in _nested(finding, search, limit=8))
        return lines
    return []


def _record_renderer(
    data: JSONObject,
) -> Callable[[JSONObject], Sequence[str]]:
    """Select a record formatter for one report before traversing its records.

    :param data: Report kind, operation, and filters used to select formatting.
    :returns: Callable that renders one record into human-readable lines.
    :raises TypeError: If filters are not an object or search is not a string.
    """
    kind = str(data.get("kind", ""))
    operation = str(data.get("operation", ""))
    search = json_object(data.get("filters") or {}).get("search")
    if search is not None and not isinstance(search, str):
        raise TypeError("report search filter must be a string")

    if kind == "gitlab_runner":
        return lambda item: _runner_record(item, operation)
    if kind == "gitlab_job" and operation == "list":
        return _job_list_record

    details: Callable[[JSONObject], Sequence[str]]
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
    return lambda item: [*_record_summary(item), *details(item)]


def _mtu_lines(data: JSONObject) -> MutableSequence[str]:
    """Render physical interfaces, their MTUs, and consistency findings.

    :param data: MTU report containing selection, interface records, and findings.
    :returns: Bounded interface table, omitted-record count, and finding lines.
    :raises KeyError: An interface or finding lacks a required report field.
    :raises TypeError: If interfaces, findings, node names, or IP addresses have invalid shapes.
    """  # noqa: DOC502 - Required field indexing and shape readers can fail.
    records = json_objects(data.get("records", []))
    lines = [
        f"Physical NIC MTU: {data.get('status', 'UNKNOWN')}",
        f"Target: {data.get('target', 'unknown')}",
        f"Plan: {data.get('plan_digest', 'unavailable')}",
        f"Nodes: {len(json_strings(data.get('planned_nodes', [])))} selected",
        "Selection: PCI Ethernet interfaces with an IPv4 address",
        "Apply creates temporary oc debug Pods and verifies cleanup.",
    ]
    if records:
        lines.append(
            "NODE                      INTERFACE     PCI DEVICE       MTU   STATE   IPv4"
        )
    for row in records[:RECORD_LIMIT]:
        lines.append(
            f"{sanitize(str(row['node']), 25):25}"
            f"{sanitize(str(row['interface']), 13):13} "
            f"{sanitize(str(row['pci_device']), 16):16} "
            f"{row['mtu']:5} {sanitize(str(row['oper_state']), 7):7} "
            f"{sanitize(','.join(json_strings(row['ipv4_addresses'])), 48)}"
        )
    if len(records) > RECORD_LIMIT:
        lines.append(f"... {len(records) - RECORD_LIMIT} more interfaces in JSON")
    for finding in json_objects(data.get("findings", [])):
        lines.append(f"Finding: {finding['code']} MTUs={finding['mtu_values']}")
    return lines


def _access_lines(data: JSONObject) -> MutableSequence[str]:
    """Render each authority's observed access and recovery guidance.

    :param data: Access report containing status and named surface results.
    :returns: Overall status and per-surface target, reason, and next-step lines.
    :raises TypeError: If surfaces or an individual surface are not objects.
    """  # noqa: DOC502 - Shared JSON shape readers reject malformed fields.
    lines = [f"Access: {data.get('status', 'UNKNOWN')}"]
    for name, value in json_object(data.get("surfaces", {})).items():
        surface = json_object(value)
        lines.append(
            f"  {name}: {surface.get('status', 'DRY_RUN')} {surface.get('target', '')}"
        )
        if surface.get("reason"):
            lines.append(f"    reason: {surface['reason']}")
        if surface.get("next_step"):
            lines.append(f"    next: {surface['next_step']}")
    return lines


def _ceph_lines(data: JSONObject) -> Sequence[str]:
    """Render Ceph health, placement groups, OSD state, and recovery guidance.

    :param data: Ceph report containing health, inactive PGs, OSDs, and actions.
    :returns: Summary lines with a bounded CRUSH hierarchy when one is present.
    :raises TypeError: If Ceph records or action labels have invalid shapes.
    :raises KeyError: If a hierarchy node lacks its required identity or children.
    """  # noqa: DOC502 - Specialized record renderers propagate field errors.
    lines = [
        f"Ceph health: {data.get('health', 'UNKNOWN')}",
        f"Inactive PGs: {len(json_objects(data.get('inactive_pgs', [])))}",
        f"OSDs down: {sum(item.get('status') == 'down' for item in json_objects(data.get('osds', [])))}",
    ]
    if "osd_tree" in data:
        lines.append("Ceph hierarchy:")
        lines.extend(_ceph_tree_lines(json_objects(data["osd_tree"])))
    lines.extend(
        f"Action: {action}" for action in json_strings(data.get("actions", []))
    )
    return lines


def _diagnostic_lines(data: JSONObject) -> MutableSequence[str]:
    """Render a diagnostic summary and its specialized record details.

    :param data: Report with kind, target, records, and optional resource inventories.
    :returns: Header, record evidence, and available inventory summary lines.
    :raises TypeError: If record, filter, inventory, or specialized report fields are malformed.
    :raises KeyError: If a specialized record lacks a required identity field.
    """  # noqa: DOC502 - Specialized record renderers propagate field errors.
    records = json_objects(data.get("records", []))
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
        inventory = json_object(data["inventory"])
        lines.append(
            "Inventory: "
            + ", ".join(f"{key}={value}" for key, value in sorted(inventory.items()))
        )
    if data.get("kind") == "ceph_cluster":
        lines.extend(_ceph_lines(data))
    return lines


def human(data: JSONObject) -> str:
    """Render bounded operator output from one structured result.

    :param data: Versioned command result or operation plan.
    :returns: Human-readable status and selected evidence fields.
    :raises TypeError: If report fields do not have the declared object or array shapes.
    :raises KeyError: If a specialized record or saved-file mapping lacks required fields.
    """  # noqa: DOC502 - record renderers and JSON shape readers validate fields.
    kind = data.get("kind")
    if kind == "access_check":
        lines = _access_lines(data)
    else:
        lines = (
            _mtu_lines(data)
            if kind == "k8s_verify_mtu_consistency"
            else _diagnostic_lines(data)
        )
        for error in json_objects(data.get("errors", [])):
            lines.append(f"Error: {error.get('source')}: {error.get('reason')}")
        if kind == "k8s_verify_mtu_consistency" and data.get("cleanup"):
            lines.append(
                f"Cleanup: {json_object(data['cleanup']).get('status', 'UNKNOWN')}"
            )
    lines.extend(_report_file_lines(data))
    if kind == "gitlab_job" and data.get("operation") == "list":
        lines.append(f"Truncated: {str(data.get('truncated', False)).lower()}")
    return "\n".join(lines) + "\n"


def _report_file_lines(data: JSONObject) -> Sequence[str]:
    """Describe saved report files in human-readable output.

    :param data: Report optionally containing a report-files mapping.
    :returns: Output lines naming saved JSON and text reports, or an empty list.
    :raises TypeError: If saved-file information is not an object.
    :raises KeyError: If a nonempty saved-file mapping lacks JSON or text paths.
    """  # noqa: DOC502 - Required field indexing and shape readers can fail.
    files = json_object(data.get("report_files") or {})
    if not files:
        return []
    return [
        f"Report JSON: {sanitize(str(files['json']), 1024)}",
        f"Report text: {sanitize(str(files['text']), 1024)}",
    ]


def render_value(data: JSONValue, mode: str, compact: bool = False) -> str:
    """Serialize selected provider data or a validated report for the caller.

    :param data: JSON-compatible value after the caller's validation or redaction.
    :param mode: Selected json, yaml, or text representation.
    :param compact: Whether JSON uses compact separators instead of indentation.
    :returns: One serialized value ending in a newline.
    :raises RuntimeError: If YAML output is requested without PyYAML.
    :raises ValueError: If the mode is unsupported, JSON contains nonfinite numbers,
        or the supplied containers contain a cycle.
    :raises TypeError: If data cannot be serialized or text output is not a string.
    :raises yaml.YAMLError: If YAML cannot represent the selected value.
    :raises RecursionError: If copying encounters a cycle or excessive nesting.
    """  # noqa: DOC503 - JSON copying and serializers propagate documented errors.
    if mode == "text":
        if not isinstance(data, str):
            raise TypeError("text output requires a string")
        return data + "\n"
    if mode == "yaml":
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is required for --yaml") from exc
        return yaml.safe_dump(copy_json(data), sort_keys=True, allow_unicode=True)
    if mode != "json":
        raise ValueError("unsupported output mode")
    return (
        json.dumps(
            copy_json(data),
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )


def emit(data: JSONObject, mode: str, output_dir: Optional[str] = None) -> str:
    """Redact and render a report, optionally persisting paired files.

    :param data: Report mapping to redact before serialization.
    :param mode: Selected ``json``, ``yaml``, or ``human`` output mode.
    :param output_dir: Parent directory for paired files, or ``None`` for text only.
    :returns: Redacted report text in the selected format.
    :raises RuntimeError: YAML output is requested without PyYAML.
    :raises OSError: The output directory or report files cannot be written.
    :raises TypeError: A report value cannot be serialized as JSON.
    :raises RecursionError: Report mappings or sequences contain a cycle.
    :raises ValueError: If the report contains a nonfinite number or JSON container cycle.
    :raises KeyError: If human rendering encounters a missing required report field.
    :raises yaml.YAMLError: If YAML cannot represent the report.
    """  # noqa: DOC502 - serializers, field readers, and file I/O propagate errors.
    data = redact_tree(data)
    if output_dir:
        destination = Path(output_dir).expanduser().resolve()
        stem = re.sub(r"[^A-Za-z0-9_-]", "_", str(data.get("kind") or "report"))
        run_directory = destination / f"{stem}-{uuid.uuid4().hex}"
        data["report_files"] = {
            "json": str(run_directory / f"{stem}.json"),
            "text": str(run_directory / f"{stem}.txt"),
        }
    pretty_json = render_value(data, "json")
    human_text = human(data)
    rendered = (
        pretty_json
        if mode == "json"
        else render_value(data, "yaml")
        if mode == "yaml"
        else human_text
    )
    if output_dir:
        destination.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{stem}-", dir=destination))
        try:
            (temporary / f"{stem}.json").write_text(pretty_json, encoding="utf-8")
            (temporary / f"{stem}.txt").write_text(human_text, encoding="utf-8")
            os.replace(temporary, run_directory)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return rendered


def emit_result(result: JSONObject, args: Namespace) -> int:
    """Render the workflow result and write requested reports, receipts, and diagnostics.

    :param result: State-owned report with kind, status, and operation evidence.
    :param args: Command output, logging, discovery, and receipt options.
    :returns: Zero for discovery or the canonical exit code for the report status.
    :raises ValueError: No report was produced, or report values contain a JSON cycle.
    :raises KeyError: A non-discovery report lacks its kind or status.
    :raises RuntimeError: YAML output is selected without PyYAML.
    :raises OSError: A receipt, report, log, or output stream cannot be written.
    :raises TypeError: A report value cannot be encoded as JSON.
    :raises RecursionError: Nested report data contains a cycle during redaction.
    """  # noqa: DOC503 - emit, receipt writing, and logging propagate errors.
    import sys

    from lib.python.core.cli import output_mode
    from lib.python.core.logging import log_event
    from lib.python.core.portable import write_portable_receipt
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
    log_event(args, json_string(data["kind"]), "result", json_string(data["status"]))
    sys.stdout.write(rendered)
    return exit_code(json_string(data["status"]))


def plan_digest(document: JSONObject, ensure_ascii: bool = True) -> str:
    """Fingerprint normalized nonsecret operation inputs.

    :param document: Inputs and target identities bound by the owning action.
    :param ensure_ascii: Whether non-ASCII text is escaped; false preserves UTF-8
        for the existing binary-build fingerprint format.
    :returns: SHA-256 digest of the mapping's canonical JSON encoding.
    :raises TypeError: A document value cannot be encoded as JSON.
    :raises ValueError: The document contains a circular reference.
    :raises RecursionError: If the document contains cyclic or excessively nested containers.
    """  # noqa: DOC502 - copy_json and json.dumps propagate invalid-value errors.
    encoded = json.dumps(
        copy_json(document),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=ensure_ascii,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
