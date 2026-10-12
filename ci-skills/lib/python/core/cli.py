"""Shared CLI adapter for explicit targets and output modes.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import NoReturn, Optional

from lib.python.access.access import check_access, dry_run_access
from lib.python.access.credentials import bind_sources
from lib.python.access.shared import access_evidence
from lib.python.core.argument_parser import TrackedArgumentParser
from lib.python.core.catalog import (
    COMMAND_BY_KIND,
    COMMANDS,
    DEFAULT_AUTHORITIES,
    PROJECT_DIR,
    TARGET_FILENAME,
    describe,
    missing_required_options,
)
from lib.python.core.help import CommandHelp, CommandHelpFormatter
from lib.python.core.json_types import JSONObject, JSONValue, copy_json_object
from lib.python.core.logging import add_log_arguments, log_event
from lib.python.core.portable import write_portable_receipt
from lib.python.core.project_binding import resolve_target as resolve_project_target
from lib.python.core.project_binding import resolve_target_file
from lib.python.core.report import emit
from lib.python.core.runtime import sanitize
from lib.python.core.target import (
    Target,
    TargetError,
)
from lib.python.sys.status import (
    BLOCKED,
    PASS,
    PROFILE_BASE,
    PROFILE_DRY_RUN,
    PROFILE_FULL,
    exit_code,
)

# The documented per-host location. Keeping it here rather than in each script
# means one answer to "where does the target live", and the script-interface
# rule that a value the host already knows is not a required argument.
# Composed from the catalog's own directory and filename constants, so help
# text, the no-target error and the search path cannot name different places.
DEFAULT_TARGET = f"~/{PROJECT_DIR}/{TARGET_FILENAME}"


# Which report kind each collector produces. Module scope so the catalog test
# can prove every one of them is a declared command.
COLLECTOR_KINDS = {
    "collect_storage": "storage_report",
    "collect_events": "event_trace",
    "collect_cilium": "cilium_status",
    "collect_gitlab_job": "gitlab_job",
    "collect_ceph_cluster": "ceph_cluster",
    "collect_mtu_consistency": "k8s_verify_mtu_consistency",
}
ACCESS_CHECK_KIND = "access_check"


def resolve_target(explicit: Optional[str]) -> tuple[Path, str]:
    """Resolve the runtime target file through the shared search order.

    :param explicit: Internal path override, or ``None`` for normal resolution.
    :returns: Selected target path and its source identifier.
    :raises TargetError: If the selected file is missing or selection is invalid.
    """  # noqa: DOC502 - Target/source resolution propagates validation and I/O.
    return resolve_target_file(explicit)


def output_mode(args: argparse.Namespace) -> str:
    """Select explicit output or choose a default from stdout's terminal state.

    :param args: Namespace containing JSON, YAML, and optional human-mode flags.
    :returns: ``json`` or ``yaml`` when explicitly selected; otherwise ``human``
        for an explicit human flag or terminal stdout, and ``json`` for a pipe.
    """
    if args.json:
        return "json"
    if args.yaml:
        return "yaml"
    if getattr(args, "human", False):
        return "human"
    try:
        interactive = sys.stdout.isatty()
    except (AttributeError, ValueError):
        interactive = False
    return "human" if interactive else "json"


class MachineArgumentParser(TrackedArgumentParser):
    """Keep parser failures in the selected machine-readable result shape."""

    def __init__(
        self,
        prog: Optional[str] = None,
        description: Optional[str] = None,
        epilog: Optional[str] = None,
        report_kind: Optional[str] = None,
        help_contract: Optional[JSONObject] = None,
    ):
        """Configure argument parsing and optional catalogue-driven help.

        :param prog: Executable name; None uses the process argument name.
        :param description: Optional command purpose for ordinary argparse help.
        :param epilog: Optional final guidance for ordinary argparse help.
        :param report_kind: Command result kind used for structured argument failures.
        :param help_contract: Command metadata used to render ordered help sections.
        """
        super().__init__(
            prog=prog,
            description=description,
            epilog=epilog,
            allow_abbrev=False,
            formatter_class=CommandHelpFormatter,
        )
        self.report_kind = report_kind
        self.help_contract = help_contract
        self.requested: tuple[str, ...] = ()

    def format_help(self) -> str:
        """Render ordered help from a command contract when one is supplied.

        :returns: Human help, with argparse rendering every accepted option.
        :raises TypeError: If catalogue text, description maps, or examples are malformed.
        :raises KeyError: If a catalogue example lacks its description or command.
        """  # noqa: DOC502 - Catalogue field readers validate the help contract.
        contract = self.help_contract
        if contract is None and Path(self.prog).name in COMMANDS:
            contract = describe(Path(self.prog).name)
        if contract is None:
            return super().format_help()
        return CommandHelp(contract).render(self)

    def error(self, message: str) -> NoReturn:
        """Report invalid arguments in the explicitly selected output format.

        :param message: Argparse diagnostic used when no machine format was selected.
        :raises SystemExit: With status 2 after writing the argument failure.
        """  # noqa: DOC502 - argparse exits for help and invalid arguments.
        modes = [
            flag.partition("=")[0]
            for flag in self.requested
            if flag.partition("=")[0] in {"--json", "--yaml"}
        ]
        if not modes:
            super().error(message)
        mode = modes[0]
        entry = COMMANDS.get(Path(self.prog).name)
        kind = self.report_kind or (entry["kind"] if entry else "arguments")
        reason = "invalid_arguments"
        _failure(
            argparse.Namespace(json=mode == "--json", yaml=mode == "--yaml"),
            kind,
            "arguments",
            reason,
        )
        self.exit(2)


def parser(
    description: str, *, output_dir: bool = True, kind: Optional[str] = None
) -> argparse.ArgumentParser:
    """Create the shared parser used by diagnostic command adapters.

    :param description: Human-readable purpose of the command.
    :param output_dir: Whether paired-report file output is accepted.
    :param kind: Result kind used for structured argument failures.
    :returns: Parser with binding selection, output, discovery, dry-run,
        provenance, and logging options; targets use the shared runtime resolver.
    """
    result = MachineArgumentParser(
        report_kind=kind,
        description=description,
        epilog=(
            "Output: a terminal gets the human summary; a pipe or file gets "
            "versioned JSON, so no flag is needed when a program reads this. "
            "Force it with --json, --yaml or --human. "
            "Exit 0 = PASS, DRY_RUN or PLANNED; 2 = BLOCKED or PARTIAL. "
            "Run --describe for this command's machine-readable contract, or "
            "see tools.json for the whole skill. "
            "Target resolves in order: --binding, "
            "CI_SKILLS_TARGET or K8S_ADMIN_DIAGNOSTICS_BINDING, "
            f"./{PROJECT_DIR}/{TARGET_FILENAME}, {DEFAULT_TARGET}. "
            "Example: %(prog)s --json"
        ),
    )
    result.set_defaults(target=None)
    result.add_argument(
        "--binding",
        metavar="PATH",
        help="explicit project binding for target and ordered kubeconfig sources",
    )
    modes = result.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", help="print versioned JSON")
    modes.add_argument("--yaml", action="store_true", help="print versioned YAML")
    modes.add_argument(
        "--human",
        action="store_true",
        help="print the human summary even when stdout is not a terminal",
    )
    result.add_argument(
        "--describe",
        action="store_true",
        help="print this command's machine-readable contract and exit",
    )
    result.add_argument(
        "--dry-run",
        action="store_true",
        help="show planned probes without contacting APIs",
    )
    result.add_argument(
        "--revision",
        metavar="SHA",
        help="exact source commit SHA (required for live installed copies without Git metadata)",
    )
    add_log_arguments(result)
    if output_dir:
        result.add_argument(
            "--output-dir", metavar="PATH", help="write paired JSON and human reports"
        )
    return result


def _failure(
    args: argparse.Namespace,
    kind: str,
    source: str,
    reason: str,
    *,
    cleanup_errors: Optional[Sequence[Mapping[str, str]]] = None,
) -> int:
    """Write a blocked result while preserving the command's output mode.

    :param args: Namespace containing output and diagnostic options.
    :param kind: Command result kind.
    :param source: Stage that failed.
    :param reason: Failure description, sanitized before rendering.
    :param cleanup_errors: Additional resource-cleanup failures, when present.
    :returns: Exit status 2 after writing the blocked result.
    """
    errors: list[Mapping[str, str]] = [
        {"source": source, "reason": sanitize(reason, 240)}
    ]
    if cleanup_errors:
        errors.extend(cleanup_errors)
    data: MutableMapping[str, JSONValue] = {
        "schema_version": "1.0",
        "kind": kind,
        "status": BLOCKED,
        "captured_at": datetime.now(UTC).isoformat(),
        "execution_host": socket.getfqdn(),
        "tested_revision": getattr(args, "revision", None),
        "errors": errors,
        "summary": {"record_count": 0, "error_count": len(errors)},
    }
    if cleanup_errors:
        data["cleanup"] = {"status": BLOCKED}
    try:
        log_event(
            args,
            kind,
            "failure",
            BLOCKED,
            error_class=source,
            persist=not getattr(args, "dry_run", False)
            and getattr(args, "apply", True),
        )
    except OSError:
        # The structured failure must survive an unavailable optional log file.
        pass
    # The reader rule applies to a failure too. A program piping a cold run
    # hits the no-target error FIRST, so emitting only a stderr line there made
    # the one shape a caller cannot parse the one it meets first -- and it
    # contradicted the default_output contract the manifest publishes.
    mode = output_mode(args)
    if mode == "human":
        print(f"BLOCKED: {errors[0]['reason']}", file=sys.stderr)
    else:
        try:
            sys.stdout.write(emit(data, mode))
        except (RuntimeError, OSError):
            sys.stdout.write(json.dumps(data, sort_keys=True) + "\n")
    return 2


Collector = Callable[[Target, argparse.Namespace], JSONObject]
ReportReader = Callable[[Target, argparse.Namespace, JSONObject], JSONObject]

COLLECTION_PROBES: Mapping[str, Sequence[str]] = {
    "collect_storage": (
        "concurrent node, Pod, PVC/PV, StorageClass, CSI, attachment, controller reads",
    ),
    "collect_events": (
        "events.k8s.io read; core fallback only if resource is absent",
        "time and object filtering",
    ),
    "collect_cilium": (
        "concurrent DaemonSet, Pod, operator, CiliumNode reads",
        "non-TTY health on ready agents",
    ),
    "collect_gitlab_job": ("job, pipeline, runner API reads", "bounded job trace read"),
    "collect_ceph_cluster": (
        "concurrent Ceph status, OSD tree, inactive PG and OSD/monitor Pod reads",
        "node, Ready and Pod condition filtering",
    ),
    "collect_mtu_consistency": (
        "read selected nodes and create temporary oc debug Pods on apply",
        "read PCI Ethernet IPv4 links and verify Pod cleanup",
    ),
}


def _access_report(
    target: Target, args: argparse.Namespace, gate: JSONObject
) -> JSONObject:
    """Return the access evidence already collected for this invocation.

    :param target: Resolved runtime authority, retained by the shared reader interface.
    :param args: Parsed command inputs, retained by the shared reader interface.
    :param gate: Completed access report from the selected authority checks.
    :returns: The same access report without an additional provider call.
    """
    return gate


def _live_access_report(
    target: Target, args: argparse.Namespace, gate: JSONObject
) -> JSONObject:
    """Extend a passing access report with the installed diagnostic read bundle.

    :param target: Runtime target used for each independent diagnostic read.
    :param args: Optional job and Ceph selections from the command parser.
    :param gate: Successful access report copied before attaching live evidence.
    :returns: Access report containing each collector's independent read-back.
    :raises RuntimeError: If the worker pool cannot start or submit its reads.
    """  # noqa: DOC502 - The collector thread pool can fail to start.
    from lib.python.core.live import collect_live_checks

    return collect_live_checks(target, args, copy_json_object(gate))


def _collector_report(
    collect: Collector, target: Target, args: argparse.Namespace, gate: JSONObject
) -> JSONObject:
    """Read one diagnostic and attach the access evidence that authorized it.

    :param collect: One concrete collector selected by its installed command.
    :param target: Resolved runtime target for that collector.
    :param args: Parsed collection filters and command options.
    :param gate: Successful prerequisite access evidence.
    :returns: Collector report copied into an owned mapping with its access summary.
    :raises Exception: If the selected collector fails; the command boundary converts
        that original failure into a blocked result with its collection stage.
    :raises RecursionError: If collector evidence contains cyclic nested containers.
    """  # noqa: DOC502 - The supplied collector and recursive copy can fail.
    data = copy_json_object(collect(target, args))
    data["access"] = access_evidence(gate)
    return data


def _diagnostic_target(args: argparse.Namespace, script: Optional[str]) -> Target:
    """Resolve the command's required authorities and bind live source identity.

    :param args: Parsed target resolution, plan, and source revision options.
    :param script: Declared executable name, or None for a library-only collector.
    :returns: Runtime target with source provenance bound for a live invocation.
    :raises TargetError: If target selection, credentials, or source identity is invalid.
    :raises OSError: If selected source files cannot be read.
    """  # noqa: DOC502 - Target/source resolution propagates validation and I/O.
    required = tuple(COMMANDS[script]["requires"]) if script else DEFAULT_AUTHORITIES
    target = resolve_project_target(
        args.target,
        getattr(args, "binding", None),
        dry_run=args.dry_run,
        required_surfaces=required,
    )
    if not args.dry_run:
        target = bind_sources(target, revision=getattr(args, "revision", None))
    return target


def _diagnostic_gate(
    args: argparse.Namespace,
    target: Target,
    kind: str,
    probes: Sequence[str],
    profile: str,
) -> MutableMapping[str, JSONValue]:
    """Check declared access or construct its non-contacting plan.

    :param args: Parsed plan and publication options plus collection filters.
    :param target: Bound authority selected for this invocation.
    :param kind: Canonical result kind, used to select Cilium prerequisites.
    :param probes: Planned collection operations; empty for an access-only command.
    :param profile: Live access profile declared by the calling command.
    :returns: Mutable access evidence or plan with the selected profile.
    :raises TargetError: If a required authority cannot be selected or verified.
    :raises OSError: If a prerequisite probe cannot read its local configuration.
    """  # noqa: DOC502 - Access and target helpers propagate failures.
    publication = bool(getattr(args, "publication", False))
    needs_cilium = kind in {ACCESS_CHECK_KIND, "cilium_status"}
    gate = copy_json_object(
        dry_run_access(target, publication=publication, cilium=needs_cilium)
        if args.dry_run
        else check_access(target, publication=publication, cilium=needs_cilium)
    )
    if args.dry_run and kind != ACCESS_CHECK_KIND:
        gate["collection_probes"] = probes
        gate["filters"] = {
            key: value
            for key, value in vars(args).items()
            if key not in {"target", "binding", "json", "yaml", "dry_run", "output_dir"}
        }
    gate["profile"] = PROFILE_DRY_RUN if args.dry_run else profile
    return gate


def _write_diagnostic(
    args: argparse.Namespace, data: JSONObject, kind: str, started: float
) -> int:
    """Write one diagnostic through the shared receipt, report, and logging paths.

    :param args: Output mode, optional report/receipt paths, and diagnostic settings.
    :param data: Final report from the access gate or selected collector.
    :param kind: Canonical command identity used for logging.
    :param started: Monotonic start time used to calculate elapsed seconds.
    :returns: Canonical process exit code for the report status.
    :raises OSError: If requested files or output streams cannot be written.
    :raises RuntimeError: If YAML output is requested without PyYAML.
    :raises TypeError: If the report or its status has an invalid shape.
    :raises ValueError: If output serialization encounters invalid numeric data.
    :raises KeyError: If the report lacks its required status or rendering fields.
    :raises RecursionError: If report containers contain a cycle or excessive nesting.
    """  # noqa: DOC503 - Report rendering, receipt writes, and logging can fail.
    receipt = getattr(args, "receipt_out", None)
    if receipt:
        write_portable_receipt(data, receipt)
    status = data["status"]
    if not isinstance(status, str):
        raise TypeError("report status must be a string")
    rendered = emit(data, output_mode(args), getattr(args, "output_dir", None))
    log_event(args, kind, "result", status, elapsed=time.monotonic() - started)
    sys.stdout.write(rendered)
    return exit_code(status)


def _execute_report(
    args: argparse.Namespace,
    kind: str,
    read: ReportReader,
    probes: Sequence[str],
    profile: str,
) -> int:
    """Run one selected diagnostic reader after discovery and access checks.

    :param args: Parsed command inputs shared by every stage.
    :param kind: Canonical command result identity.
    :param read: Concrete reader invoked only after a passing live access gate.
    :param probes: Planned collection operations shown without contacting providers.
    :param profile: Access profile recorded by this command.
    :returns: Canonical result exit code; setup and read failures produce a blocked report.
    :raises OSError: If even the final blocked report cannot be written to stdout.
    """  # noqa: DOC502 - Final result writes can still fail at stdout.
    started = time.monotonic()
    script = COMMAND_BY_KIND.get(kind)
    if getattr(args, "describe", False):
        if script is None:
            return _failure(args, kind, "arguments", f"no declared contract for {kind}")
        sys.stdout.write(json.dumps(describe(script), indent=2, sort_keys=True) + "\n")
        return 0
    if args.dry_run and any(
        getattr(args, name, None) for name in ("output_dir", "receipt_out", "log_file")
    ):
        return _failure(args, kind, "arguments", "dry_run_cannot_write_output")
    missing = missing_required_options(script, args) if script else []
    if missing:
        return _failure(
            args, kind, "arguments", f"{', '.join(missing)} is required; see --describe"
        )
    source = "target"
    try:
        target = _diagnostic_target(args, script)
        source = "access_check"
        gate = _diagnostic_gate(args, target, kind, probes, profile)
        data: JSONObject
        if args.dry_run or gate["status"] != PASS:
            data = gate
        else:
            source = kind
            data = read(target, args, gate)
        source = "output"
        return _write_diagnostic(args, data, kind, started)
    except (
        TargetError,
        RuntimeError,
        ValueError,
        OSError,
        TypeError,
        KeyError,
        IndexError,
    ) as exc:
        return _failure(args, kind, source, str(exc))
    except Exception:  # noqa: BLE001 - unexpected provider data must retain the result envelope
        return _failure(args, kind, source, "unexpected_runtime_failure")


def execute(args: argparse.Namespace, collect: Collector) -> int:
    """Run the one concrete collector selected by an installed diagnostic command.

    :param args: Parsed diagnostic options and filters.
    :param collect: Required collector accepting a target and returning a JSON report.
    :returns: Shared command exit code after rendering the result or failure report.
    :raises OSError: If the final report cannot be written to stdout.
    """  # noqa: DOC502 - Final result writes can still fail at stdout.
    kind = COLLECTOR_KINDS.get(collect.__name__, collect.__name__)
    return _execute_report(
        args,
        kind,
        partial(_collector_report, collect),
        COLLECTION_PROBES.get(collect.__name__, ()),
        PROFILE_BASE,
    )


def execute_access(args: argparse.Namespace, live_checks: bool = False) -> int:
    """Run the access command with its explicitly selected diagnostic profile.

    :param args: Parsed authority, publication, output, and optional read selections.
    :param live_checks: Include independent installed collectors after access passes.
    :returns: Shared command exit code after rendering access evidence or its plan.
    :raises OSError: If the final report cannot be written to stdout.
    """  # noqa: DOC502 - Final result writes can still fail at stdout.
    return _execute_report(
        args,
        ACCESS_CHECK_KIND,
        _live_access_report if live_checks else _access_report,
        (),
        PROFILE_FULL if live_checks else PROFILE_BASE,
    )
