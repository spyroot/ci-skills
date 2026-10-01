"""Thin CLI adapter for read-only node-local diagnostics."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import datetime, timezone

from .argument_parser import StructuredParser
from .node_local import JOURNAL_SINCE, collect_ceph_kernel, collect_cilium_node
from .report import emit
from .status import BLOCKED, DRY_RUN, exit_code


def parser(kind: str) -> StructuredParser:
    result = StructuredParser(
        prog=f"{kind}.py",
        description=f"Collect {kind} evidence on the current Linux node. Audience: human and agent.",
        epilog="Example: %(prog)s --json (run on the selected node)",
    )
    modes = result.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", help="print versioned JSON")
    modes.add_argument("--yaml", action="store_true", help="print versioned YAML")
    result.add_argument(
        "--dry-run", action="store_true", help="show commands without executing them"
    )
    return result


def run(kind: str, args: argparse.Namespace) -> int:
    """Return structured evidence and a stable exit status for each mode."""
    if kind not in {"cilium_node", "ceph_kernel"}:
        raise ValueError("unsupported_node_diagnostic")
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
    mode = "json" if args.json else "yaml" if args.yaml else "human"
    print(emit(data, mode), end="")
    return exit_code(data["status"])
