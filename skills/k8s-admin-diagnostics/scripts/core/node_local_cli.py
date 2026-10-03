"""Thin CLI adapter for read-only node-local diagnostics."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import datetime, timezone
from typing import Any

from .catalog import describe_node
from .node_local import (
    CEPH_CLASSIFICATIONS,
    JOURNAL_SINCE,
    collect_ceph_kernel,
    collect_cilium_node,
)
from .report import emit
from .status import BLOCKED, DRY_RUN, exit_code


class NodeParser(argparse.ArgumentParser):
    """Keep controlled argument failures in the requested output format."""

    requested: list[str]

    def parse_args(self, args=None, namespace=None):
        self.requested = list(sys.argv[1:] if args is None else args)
        return super().parse_args(args, namespace)

    def error(self, message: str) -> None:
        data = {
            "schema_version": "1.0",
            "kind": self.prog.removesuffix(".py"),
            "status": BLOCKED,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "execution_host": socket.getfqdn(),
            "target": socket.getfqdn(),
            "records": [],
            "errors": [{"source": "arguments", "reason": "invalid_arguments"}],
            "summary": {"record_count": 0, "error_count": 1},
            "safe_next_step": "Check --help and correct the arguments.",
        }
        mode = (
            "json"
            if "--json" in self.requested
            else "yaml"
            if "--yaml" in self.requested
            else "human"
            if "--human" in self.requested or sys.stdout.isatty()
            else "json"
        )
        try:
            print(emit(data, mode), end="")
        except RuntimeError:
            print(emit(data, "json"), end="")
        raise SystemExit(2)


def parser(kind: str) -> argparse.ArgumentParser:
    result = NodeParser(
        prog=f"{kind}.py",
        description=f"Collect {kind} evidence on the current Linux node. Audience: human and agent.",
        epilog="Example: %(prog)s --json (run on the selected node)",
    )
    modes = result.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", help="print versioned JSON")
    modes.add_argument("--yaml", action="store_true", help="print versioned YAML")
    modes.add_argument("--human", action="store_true", help="print a human summary")
    result.add_argument(
        "--dry-run", action="store_true", help="show commands without executing them"
    )
    result.add_argument(
        "--search",
        metavar="TEXT",
        help="case-insensitive text filter over returned records",
    )
    if kind == "ceph_kernel":
        result.add_argument(
            "--classification",
            choices=CEPH_CLASSIFICATIONS,
            help="return only Ceph kernel records with this classification",
        )
    result.add_argument(
        "--describe", action="store_true", help="print the command contract as JSON"
    )
    return result


def _matches_search(record: dict[str, Any], search: str | None) -> bool:
    return (
        not search
        or search.casefold() in json.dumps(record, ensure_ascii=False).casefold()
    )


def _apply_filters(
    data: dict[str, Any], *, search: str | None, classification: str | None
) -> dict[str, Any]:
    if not search and not classification:
        return data
    filters = data.setdefault("filters", {})
    if search:
        filters["search"] = search
    if classification:
        filters["classification"] = classification
    records = [
        row
        for row in data.get("records", [])
        if isinstance(row, dict)
        and _matches_search(row, search)
        and (
            not classification
            or data.get("kind") == "ceph_kernel"
            and row.get("classification") == classification
        )
    ]
    data["records"] = records
    summary = data.setdefault("summary", {})
    summary["record_count"] = len(records)
    if data.get("kind") == "ceph_kernel":
        data["actions"] = sorted(
            {row["action"] for row in records if row.get("action")}
        )
    return data


def run(kind: str, args: argparse.Namespace) -> int:
    """Return structured evidence and a stable exit status for each mode."""
    if kind not in {"cilium_node", "ceph_kernel"}:
        raise ValueError("unsupported_node_diagnostic")
    if getattr(args, "describe", False):
        print(json.dumps(describe_node(f"{kind}.py"), sort_keys=True))
        return 0
    data = {
        "schema_version": "1.0",
        "kind": kind,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "execution_host": socket.getfqdn(),
        "target": socket.getfqdn(),
        "records": [],
        "errors": [],
        "summary": {"record_count": 0, "error_count": 0},
    }
    if args.yaml:
        try:
            import yaml  # noqa: F401 - preflight before node reads
        except ImportError:
            data["status"] = BLOCKED
            data["errors"] = [{"source": "output", "reason": "pyyaml_unavailable"}]
            data["summary"]["error_count"] = 1
            data["safe_next_step"] = (
                "Install PyYAML in the project environment or use --json."
            )
            print(json.dumps(data, sort_keys=True))
            return 2
    if args.dry_run:
        data["status"] = DRY_RUN
        data["probes"] = (
            [
                "sudo -n crictl ps --output json",
                "sudo -n crictl exec <agent-id> cilium-dbg/cilium-health --output json",
            ]
            if kind == "cilium_node"
            else [
                f"sudo -n journalctl -k --utc --since {JOURNAL_SINCE!r} --no-pager --output=json"
            ]
        )
    elif sys.platform != "linux":
        data["status"] = BLOCKED
        data["errors"] = [{"source": "execution_host", "reason": "linux_node_required"}]
        data["summary"]["error_count"] = 1
        data["safe_next_step"] = "Run this command on the selected Linux node."
    else:
        try:
            data = (
                collect_cilium_node()
                if kind == "cilium_node"
                else collect_ceph_kernel()
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            data["status"] = BLOCKED
            data["errors"] = [{"source": kind, "reason": "node_collection_failed"}]
            data["summary"]["error_count"] = 1
            data["safe_next_step"] = "Inspect the node tool output and retry."
    data = _apply_filters(
        data,
        search=getattr(args, "search", None),
        classification=getattr(args, "classification", None),
    )
    mode = (
        "json"
        if args.json
        else "yaml"
        if args.yaml
        else "human"
        if getattr(args, "human", False) or sys.stdout.isatty()
        else "json"
    )
    print(emit(data, mode), end="")
    return exit_code(data["status"])
