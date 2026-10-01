"""Versioned evidence reports and paired human/machine rendering."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runtime import sanitize
from .status import PARTIAL, PASS


def _nested(value: Any, search: str | None, *, limit: int = 32) -> list[str]:
    """Render bounded evidence lines, preserving lines matched by a search filter."""
    lines = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True).splitlines()
    if search:
        matched = [line for line in lines if search.casefold() in line.casefold()]
        lines = matched or lines
    return [sanitize(line, 240) for line in lines[:limit]]


def report(kind: str, target: str, filters: dict[str, Any], records: list[dict[str, Any]], errors: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "schema_version": "1.0", "kind": kind,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "target": target, "filters": filters, "records": records,
        "errors": errors, "status": PARTIAL if errors else PASS,
        "summary": {"record_count": len(records), "error_count": len(errors)},
    }


def human(data: dict[str, Any]) -> str:
    lines = [
        f"{data.get('kind', 'diagnostic')}: {data.get('status', 'UNKNOWN')}",
        f"Target: {data.get('target', 'unknown')}",
        f"Records: {len(data.get('records', []))}",
    ]
    for item in data.get("records", []):
        fields = ("timestamp", "first_timestamp", "namespace", "kind", "name", "node", "nodes", "phase",
                  "status", "storage_class", "volume", "reason", "message", "pod_uid")
        parts = [f"{key}={item[key]}" for key in fields if item.get(key) is not None]
        lines.append("  " + "  ".join(parts)[:240])
        kind = data.get("kind")
        search = (data.get("filters") or {}).get("search")
        if kind == "gitlab_job":
            lines.append(f"    failure_reason={item.get('failure_reason')}")
            for label in ("pipeline", "runner"):
                if item.get(label) is not None:
                    lines.append(f"    {label}:")
                    lines.extend("      " + line for line in _nested(item[label], search, limit=12))
            trace_lines = (item.get("trace_tail") or "").splitlines()
            if search:
                trace_lines = [line for line in trace_lines if search.casefold() in line.casefold()]
            lines.append("    trace_tail:")
            lines.extend("      " + sanitize(line, 240) for line in trace_lines[-40:])
        elif kind == "storage_report":
            for label in ("pods", "attachments", "controllers"):
                if item.get(label):
                    lines.append(f"    {label}:")
                    for nested_item in item[label]:
                        lines.extend("      " + line for line in _nested(nested_item, search, limit=12))
        elif kind == "cilium_status" and item.get("health") is not None:
            lines.append("    health:")
            lines.extend("      " + line for line in _nested(item["health"], search, limit=32))
    if data.get("inventory"):
        lines.append("Inventory: " + ", ".join(
            f"{key}={value}" for key, value in sorted(data["inventory"].items())
        ))
    for error in data.get("errors", []):
        lines.append(f"Error: {error.get('source')}: {error.get('reason')}")
    if data.get("kind") == "access_check":
        lines = [f"Access: {data.get('status', 'UNKNOWN')}"]
        for name, surface in data.get("surfaces", {}).items():
            lines.append(f"  {name}: {surface.get('status', 'DRY_RUN')} {surface.get('target', '')}")
            if surface.get("reason"):
                lines.append(f"    reason: {surface['reason']}")
            if surface.get("next_step"):
                lines.append(f"    next: {surface['next_step']}")
    if data.get("kind") == "access_receipt":
        lines = _receipt_lines(data)
    return "\n".join(lines) + "\n"


def _receipt_lines(data: dict[str, Any]) -> list[str]:
    """Render the receipt: host, sources, identities, then each live check."""
    host = data.get("execution_host") or {}
    revision = data.get("tested_revision") or {}
    lines = [
        f"Access receipt: {data.get('status', 'UNKNOWN')}",
        f"Host: {host.get('hostname', 'unknown')} ({host.get('system')} {host.get('machine')})",
        f"Observed: {data.get('observed_at', 'unknown')}",
        f"Revision: {revision.get('commit') or revision.get('detail') or 'unknown'}"
        + (" (dirty)" if revision.get("dirty") else ""),
    ]
    for name, source in sorted((data.get("credential_sources") or {}).items()):
        lines.append(f"  source {name}: {source.get('kind')} -> {source.get('reference')}")
        if source.get("shadowed"):
            lines.append("    shadowed: " + ", ".join(source["shadowed"]))
        mechanism = (source.get("detail") or {}).get("authentication_mechanism")
        if mechanism:
            detail = source["detail"]
            lines.append(
                f"    context={detail.get('context')} user={detail.get('user_entry')}"
                f" mechanism={mechanism} selected_by={detail.get('selected_by')}"
            )
    for name, identity in sorted((data.get("identities") or {}).items()):
        lines.append(f"  identity {name}: {identity}")
    for check in data.get("live_checks", []):
        lines.append(f"  {check.get('status')} {check.get('name')}: {check.get('detail')}")
    blocking = data.get("blocking") or {}
    for label, items in sorted(blocking.items()):
        if items:
            lines.append(f"Blocking {label}: " + ", ".join(items))
    return lines


def emit(data: dict[str, Any], mode: str, output_dir: str | None = None) -> str:
    """Render one in-memory collection and optionally persist paired reports."""
    pretty_json = json.dumps(data, indent=2, sort_keys=True) + "\n"
    human_text = human(data)
    if output_dir:
        destination = Path(output_dir).expanduser()
        destination.mkdir(parents=True, exist_ok=True)
        stem = data.get("kind", "report")
        (destination / f"{stem}.json").write_text(pretty_json, encoding="utf-8")
        (destination / f"{stem}.txt").write_text(human_text, encoding="utf-8")
    if mode == "json":
        return pretty_json
    if mode == "yaml":
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is required for --yaml") from exc
        return yaml.safe_dump(data, sort_keys=True, allow_unicode=True)
    return human_text
