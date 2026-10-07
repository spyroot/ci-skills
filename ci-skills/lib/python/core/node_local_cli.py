"""Thin CLI adapter for read-only node-local diagnostics."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import datetime, timezone
from typing import Any

from .access import access_evidence, check_access
from .catalog import NODE_LOCAL_COMMANDS, describe_node
from .credentials import bind_sources
from .node_local import (
    CEPH_CLASSIFICATIONS,
    JOURNAL_SINCE,
    collect_ceph_kernel,
    collect_cilium_node,
)
from .node_pod import NodePodError, select_node_pod
from .project_binding import resolve_target as resolve_project_target
from .report import emit
from .runtime import sanitize
from .status import BLOCKED, DRY_RUN, PASS, exit_code
from .target import TargetError


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
        description=f"Collect {kind} evidence through a selected Kubernetes target. Audience: human and agent.",
        epilog=(
            "Target order: --target or --binding, CI_SKILLS_TARGET or "
            "K8S_ADMIN_DIAGNOSTICS_BINDING, ./.ci-skills/target.toml, "
            "~/.ci-skills/target.toml. Example: %(prog)s --json"
        ),
    )
    selection = result.add_mutually_exclusive_group()
    selection.add_argument(
        "--target", metavar="PATH", help="nonsecret TOML target file"
    )
    selection.add_argument(
        "--binding",
        metavar="PATH",
        help="project binding for target and ordered kubeconfig sources",
    )
    result.add_argument(
        "--revision", metavar="SHA", help="exact installed skill revision"
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
    data: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": kind,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "execution_host": socket.getfqdn(),
        "target": None,
        "records": [],
        "errors": [],
        "summary": {"record_count": 0, "error_count": 0},
    }
    if getattr(args, "yaml", False):
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
    source = "target"
    try:
        target = resolve_project_target(
            getattr(args, "target", None),
            getattr(args, "binding", None),
            dry_run=getattr(args, "dry_run", False),
            required_surfaces=NODE_LOCAL_COMMANDS[f"{kind}.py"]["requires"],
        )
        if target.source_file is None or target.source_kind is None:
            raise TargetError("target_source_unresolved")
        target_path = target.source_file
        target_source = target.source_kind
        selected = target.kubernetes.node_diagnostics
        if selected is None:
            raise TargetError("kubernetes.node_diagnostics is required for node tools")
        route = selected.cilium if kind == "cilium_node" else selected.journal
        if route is None:
            raise TargetError(f"kubernetes.node_diagnostics.{kind} route is missing")
        pod_route = route if kind == "cilium_node" else route.pod
        data["target"] = selected.node
        data["target_file"] = str(target_path.resolve())
        data["target_source"] = target_source
        data["kubernetes"] = {
            "context": target.kubernetes.context,
            "server": target.kubernetes.server,
        }
        data["pod_selection"] = {
            "node": selected.node,
            "namespace": pod_route.namespace,
            "selector": pod_route.selector,
            "container": pod_route.container,
        }
        if getattr(args, "dry_run", False):
            data["status"] = DRY_RUN
            data["probes"] = [
                "verify selected Kubernetes credential and identity",
                "kubectl get one existing Running Pod on the declared node",
                "kubectl exec without TTY to read Cilium status and health"
                if kind == "cilium_node"
                else f"kubectl exec without TTY to read kernel journal since {JOURNAL_SINCE}",
            ]
            if kind == "ceph_kernel":
                data["probes"].insert(2, "verify the declared host journal mount")
        else:
            target = bind_sources(target, revision=getattr(args, "revision", None))
            source = "access_check"
            gate = check_access(target, cilium=kind == "cilium_node")
            data["access"] = access_evidence(gate)
            if gate["status"] != PASS:
                data["access_failures"] = [
                    {
                        "surface": name,
                        "reason": surface.get("reason"),
                        "safe_next_step": surface.get("next_step"),
                    }
                    for name, surface in (gate.get("surfaces") or {}).items()
                    if isinstance(surface, dict) and surface.get("status") != PASS
                ]
                raise NodePodError("required_live_access_failed")
            source = "pod_selection"
            reader = select_node_pod(
                target,
                pod_route,
                journal=route if kind == "ceph_kernel" else None,
            )
            source = kind
            data = (
                collect_cilium_node(reader)
                if kind == "cilium_node"
                else collect_ceph_kernel(reader, route.directory)
            )
            data["access"] = access_evidence(gate)
            data["target_file"] = str(target_path.resolve())
            data["target_source"] = target_source
            data["kubernetes"] = {
                "context": target.kubernetes.context,
                "server": target.kubernetes.server,
            }
    except (
        TargetError,
        NodePodError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        data["status"] = BLOCKED
        data["errors"] = [{"source": source, "reason": sanitize(str(exc), 240)}]
        data["summary"]["error_count"] = 1
        data["safe_next_step"] = (
            "Resolve the failed credential or authority shown in access_failures."
            if source == "access_check"
            else "Correct the selected target or existing Pod route and retry."
        )
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
