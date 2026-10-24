"""Thin CLI adapter for read-only node-local diagnostics.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn, Optional

from lib.python.access.access import check_access
from lib.python.access.credentials import bind_sources
from lib.python.access.shared import access_evidence
from lib.python.core.argument_parser import TrackedArgumentParser
from lib.python.core.catalog import NODE_LOCAL_COMMANDS, describe_node
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.kubernetes.node_local import (
    CEPH_CLASSIFICATIONS,
    JOURNAL_SINCE,
    collect_ceph_kernel,
    collect_cilium_node,
)
from lib.python.core.kubernetes.node_pod import NodePodError, select_node_pod
from lib.python.core.project_binding import resolve_target as resolve_project_target
from lib.python.core.report import emit
from lib.python.core.runtime import sanitize
from lib.python.core.target import JournalPodRoute, NodePodRoute, Target, TargetError
from lib.python.sys.status import BLOCKED, DRY_RUN, PASS, exit_code


class NodeParser(TrackedArgumentParser):
    """Render node-diagnostic argument failures in the requested output format.

    .. attribute :: requested
        :type: Sequence[str]

        Raw option sequence retained before parsing for mode selection.
    """

    requested: Sequence[str]

    def error(self, message: str) -> NoReturn:
        """Write a structured node-diagnostic argument failure.

        :param message: Argparse diagnostic; raw rejected values are not reproduced.
        :raises SystemExit: With status 2 after writing the failure result.
        """
        data: JSONObject = {
            "schema_version": "1.0",
            "kind": self.prog.removesuffix(".py"),
            "status": BLOCKED,
            "captured_at": datetime.now(UTC).isoformat(),
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
    """Create the parser for one node-local diagnostic command.

    :param kind: Diagnostic kind used for the command name and Ceph-specific filters.
    :returns: Parser with binding selection, provenance, output, discovery,
        and search; ``ceph_kernel`` also accepts a classification filter.
    """
    result = NodeParser(
        prog=f"{kind}.py",
        description=(
            f"Collect {kind} evidence through a selected Kubernetes target. "
            "Audience: human and agent."
        ),
        epilog=(
            "Target order: --binding, CI_SKILLS_TARGET or "
            "K8S_ADMIN_DIAGNOSTICS_BINDING, ./.ci-skills/target.toml, "
            "~/.ci-skills/target.toml. Example: %(prog)s --json"
        ),
    )
    result.set_defaults(target=None)
    result.add_argument(
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


def _matches_search(record: JSONObject, search: Optional[str]) -> bool:
    """Match a case-insensitive search against a record's JSON text.

    :param record: Diagnostic record to inspect.
    :param search: Search text; ``None`` or empty text matches every record.
    :returns: Whether the record matches the requested text.
    :raises TypeError: If a searched record cannot be serialized as JSON.
    """  # noqa: DOC502 - json.dumps rejects unsupported record values.
    return (
        not search
        or search.casefold() in json.dumps(record, ensure_ascii=False).casefold()
    )


def _apply_filters(
    data: MutableMapping[str, JSONValue],
    search: Optional[str],
    classification: Optional[str],
) -> MutableMapping[str, JSONValue]:
    """Filter diagnostic records and update their summary in place.

    :param data: Mutable diagnostic result containing records and summary fields.
    :param search: Optional case-insensitive text filter.
    :param classification: Optional Ceph classification to retain.
    :returns: The same result mapping with filtered records, counts, and actions.
    :raises TypeError: If report filters/summary are not objects, records are not
        an array, or a searched record is not serializable.
    :raises ValueError: If a searched record contains a circular container.
    """  # noqa: DOC503 - Search serialization rejects circular containers.
    if not search and not classification:
        return data
    existing_filters = data.get("filters", {})
    if not isinstance(existing_filters, Mapping):
        raise TypeError("invalid_report_filters")
    filters = dict(existing_filters)
    data["filters"] = filters
    if search:
        filters["search"] = search
    if classification:
        filters["classification"] = classification
    supplied_records = data.get("records", [])
    if not isinstance(supplied_records, Sequence) or isinstance(supplied_records, str):
        raise TypeError("invalid_report_records")
    records = [
        row
        for row in supplied_records
        if isinstance(row, Mapping)
        and _matches_search(row, search)
        and (
            not classification
            or data.get("kind") == "ceph_kernel"
            and row.get("classification") == classification
        )
    ]
    data["records"] = records
    existing_summary = data.get("summary", {})
    if not isinstance(existing_summary, Mapping):
        raise TypeError("invalid_report_summary")
    summary = dict(existing_summary)
    data["summary"] = summary
    summary["record_count"] = len(records)
    if data.get("kind") == "ceph_kernel":
        data["actions"] = sorted(
            {
                action
                for row in records
                if isinstance(action := row.get("action"), str) and action
            }
        )
    return data


@dataclass(frozen=True)
class NodeDiagnosticSelection:
    """Bind a diagnostic to its target and one concrete existing-Pod route.

    :param target: Resolved target before credential-source binding.
    :param pod: Namespace, selector, and container identifying the existing Pod.
    :param journal: Required journal mount declaration for Ceph kernel reads only.

    .. attribute :: target
        :type: Target

        Resolved target before credential-source binding.

    .. attribute :: pod
        :type: NodePodRoute

        Namespace, selector, and container identifying the existing Pod.

    .. attribute :: journal
        :type: Optional[JournalPodRoute]

        Required journal mount declaration for Ceph kernel reads only.
    """

    target: Target
    pod: NodePodRoute
    journal: Optional[JournalPodRoute] = None


class NodeDiagnosticRun:
    """Resolve, authorize, and read one declared node diagnostic in order."""

    def __init__(self, kind: str, args: argparse.Namespace):
        """Retain parsed options and the report that survives a failed read.

        :param kind: Registered cilium_node or ceph_kernel diagnostic name.
        :param args: Parsed binding, provenance, output, dry-run and filter options.
        :raises ValueError: If the diagnostic name is unsupported.
        """
        if kind not in {"cilium_node", "ceph_kernel"}:
            raise ValueError("unsupported_node_diagnostic")
        self.kind = kind
        self.args = args
        self.state = DeliveryState(args=args)
        self.stage = "target"
        self.data: MutableMapping[str, JSONValue] = {
            "schema_version": "1.0",
            "kind": kind,
            "captured_at": datetime.now(UTC).isoformat(),
            "execution_host": socket.getfqdn(),
            "target": None,
            "records": [],
            "errors": [],
            "summary": {"record_count": 0, "error_count": 0},
        }

    def selection(self) -> NodeDiagnosticSelection:
        """Resolve the runtime target and select the diagnostic's concrete route.

        :returns: Target, existing-Pod route, and optional required journal mount.
        :raises TargetError: If resolution fails or node diagnostics, source
            provenance, or the selected diagnostic route is missing.
        :raises OSError: If a selected binding or target file cannot be read.
        """  # noqa: DOC503 - Target/source resolution propagates I/O failures.
        target = resolve_project_target(
            getattr(self.args, "target", None),
            getattr(self.args, "binding", None),
            dry_run=getattr(self.args, "dry_run", False),
            required_surfaces=tuple(NODE_LOCAL_COMMANDS[f"{self.kind}.py"]["requires"]),
        )
        if target.source_file is None or target.source_kind is None:
            raise TargetError("target_source_unresolved")
        if target.kubernetes is None:
            raise TargetError("kubernetes_target_missing")
        selected = target.kubernetes.node_diagnostics
        if selected is None:
            raise TargetError("kubernetes.node_diagnostics is required for node tools")
        journal: Optional[JournalPodRoute] = None
        if self.kind == "cilium_node":
            pod = selected.cilium
        else:
            journal = selected.journal
            pod = journal.pod if journal is not None else None
        if pod is None:
            raise TargetError(
                f"kubernetes.node_diagnostics.{self.kind} route is missing"
            )
        self.data.update(
            {
                "target": selected.node,
                "target_file": str(target.source_file.resolve()),
                "target_source": target.source_kind,
                "kubernetes": {
                    "context": target.kubernetes.context,
                    "server": target.kubernetes.server,
                },
                "pod_selection": {
                    "node": selected.node,
                    "namespace": pod.namespace,
                    "selector": pod.selector,
                    "container": pod.container,
                },
            }
        )
        return NodeDiagnosticSelection(target, pod, journal)

    def plan(self) -> None:
        """Describe the known read sequence without reading credentials or Pods."""
        probes: MutableSequence[str] = [
            "verify selected Kubernetes credential and identity",
            "kubectl get one existing Running Pod on the declared node",
        ]
        if self.kind == "cilium_node":
            probes.append("kubectl exec without TTY to read Cilium status and health")
        else:
            probes.extend(
                [
                    "verify the declared host journal mount",
                    f"kubectl exec without TTY to read kernel journal since {JOURNAL_SINCE}",
                ]
            )
        self.data.update({"status": DRY_RUN, "probes": probes})

    def collect(self, selection: NodeDiagnosticSelection) -> None:
        """Bind credentials, prove access, then read the selected existing Pod.

        :param selection: Runtime target and concrete Pod/journal routes.
        :raises TargetError: If binding fails, sources change, or a Ceph selection
            lacks its required journal route.
        :raises NodePodError: If access fails or Pod identity/route cannot be proven.
        :raises OSError: If a credential file or native command cannot be read.
        :raises ValueError: If required target authority is absent or a read is invalid.
        :raises RuntimeError: If a credential provider cannot serve the selected source.
        """  # noqa: DOC503 - Binding and diagnostic readers propagate failures.
        target = bind_sources(
            selection.target, revision=getattr(self.args, "revision", None)
        )
        self.stage = "access_check"
        gate = check_access(target, cilium=self.kind == "cilium_node", state=self.state)
        self.data["access"] = access_evidence(gate)
        if gate["status"] != PASS:
            surfaces = gate.get("surfaces", {})
            self.data["access_failures"] = (
                [
                    {
                        "surface": name,
                        "reason": surface.get("reason"),
                        "safe_next_step": surface.get("next_step"),
                    }
                    for name, surface in surfaces.items()
                    if isinstance(surface, Mapping) and surface.get("status") != PASS
                ]
                if isinstance(surfaces, Mapping)
                else []
            )
            raise NodePodError("required_live_access_failed")
        self.stage = "pod_selection"
        reader = select_node_pod(
            target, selection.pod, journal=selection.journal, state=self.state
        )
        self.stage = self.kind
        if self.kind == "cilium_node":
            data = collect_cilium_node(reader)
        else:
            if selection.journal is None:
                raise TargetError(
                    "kubernetes.node_diagnostics.ceph_kernel route is missing"
                )
            data = collect_ceph_kernel(reader, selection.journal.directory)
        data.update(
            {
                "access": access_evidence(gate),
                "target_file": self.data["target_file"],
                "target_source": self.data["target_source"],
                "kubernetes": self.data["kubernetes"],
            }
        )
        self.data = data

    def execute(self) -> MutableMapping[str, JSONValue]:
        """Run the selected plan or read path in its declared order.

        :returns: Updated diagnostic report with source provenance and evidence.
        :raises TargetError: If the runtime target or bound credentials are invalid.
        :raises NodePodError: If access or the existing Pod route cannot be proven.
        :raises OSError: If runtime configuration, credentials, or native reads fail.
        :raises ValueError: If target authority or required response data is invalid.
        :raises RuntimeError: If a credential provider cannot serve the selected source.
        """  # noqa: DOC502 - Selection, collection, or final output helpers can fail.
        selection = self.selection()
        if getattr(self.args, "dry_run", False):
            self.plan()
        else:
            self.collect(selection)
        return self.data


def run(kind: str, args: argparse.Namespace) -> int:
    """Collect and render one node diagnostic through its selected target.

    :param kind: Registered cilium_node or ceph_kernel diagnostic name.
    :param args: Parsed binding, output, discovery, dry-run, and filter options.
    :returns: Zero for discovery, plans and successful reads; 2 for blocked or
        partial results, including unavailable YAML support.
    :raises ValueError: If the diagnostic name is unsupported or report output is invalid.
    :raises OSError: If the report cannot be written to stdout.
    :raises TypeError: If a supplied report has malformed filterable fields.
    :raises RuntimeError: If requested output support becomes unavailable after preflight.
    """  # noqa: DOC502 - Selection, collection, or final output helpers can fail.
    operation = NodeDiagnosticRun(kind, args)
    if getattr(args, "describe", False):
        print(json.dumps(describe_node(f"{kind}.py"), sort_keys=True))
        return 0
    if getattr(args, "yaml", False):
        try:
            import yaml  # noqa: F401 - preflight before node reads
        except ImportError:
            operation.data.update(
                {
                    "status": BLOCKED,
                    "errors": [{"source": "output", "reason": "pyyaml_unavailable"}],
                    "summary": {"record_count": 0, "error_count": 1},
                    "safe_next_step": "Install PyYAML in the project environment or use --json.",
                }
            )
            print(json.dumps(operation.data, sort_keys=True))
            return 2
    try:
        data = operation.execute()
    except (
        TargetError,
        NodePodError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        data = operation.data
        data.update(
            {
                "status": BLOCKED,
                "errors": [
                    {"source": operation.stage, "reason": sanitize(str(exc), 240)}
                ],
                "summary": {"record_count": 0, "error_count": 1},
                "safe_next_step": (
                    "Resolve the failed credential or authority shown in access_failures."
                    if operation.stage == "access_check"
                    else "Correct the selected target or existing Pod route and retry."
                ),
            }
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
    return exit_code(str(data["status"]))
