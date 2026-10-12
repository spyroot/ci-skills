"""Shared CLI adapter for explicit targets and output modes.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Optional

from lib.python.access.access import (
    access_evidence,
    check_access,
    check_gitlab_operation_access,
    dry_run_access,
)
from lib.python.access.credentials import bind_gitlab_session, bind_sources
from lib.python.core.catalog import (
    COMMAND_BY_KIND,
    COMMANDS,
    DEFAULT_AUTHORITIES,
    PROJECT_DIR,
    TARGET_FILENAME,
    describe,
    missing_required_options,
)
from lib.python.core.gitlab.gitlab_api import GlabAPIClient
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
from lib.python.core.help import CommandHelp, CommandHelpFormatter
from lib.python.core.json_types import JSONObject, JSONValue, copy_json_object
from lib.python.core.portable import write_portable_receipt
from lib.python.core.project_binding import BINDING_ENV, resolve_target_file
from lib.python.core.project_binding import resolve_target as resolve_project_target
from lib.python.core.report import emit, report
from lib.python.core.runtime import redact_tree, sanitize
from lib.python.core.target import (
    GitLabOperationTarget,
    Target,
    TargetError,
    select_gitlab_reference,
)
from lib.python.sys.status import (
    BLOCKED,
    DRY_RUN,
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


def resolve_gitlab_target(
    explicit_target: Optional[str],
    explicit_binding: Optional[str],
    *,
    dry_run: bool = False,
) -> tuple[GitLabOperationTarget, str]:
    """Resolve GitLab settings from a target file or project binding.

    :param explicit_target: Internal target-file override, or ``None``.
    :param explicit_binding: Project-binding override, or ``None``.
    :param dry_run: Whether binding resolution may omit live credential-file checks.
    :returns: GitLab operation target and reportable selection source.
    :raises TargetError: If configuration, GitLab settings, or source identity
        cannot be resolved.
    """
    selected = resolve_project_target(
        explicit_target,
        explicit_binding,
        dry_run=dry_run,
        required_surfaces=("gitlab",),
    )
    if selected.gitlab is None or selected.source_file is None:
        raise TargetError("gitlab_target_unresolved")
    if selected.source_kind == "binding":
        selector = "argv:--binding" if explicit_binding else f"env:{BINDING_ENV}"
        source = f"{selector} -> {selected.target_reference}"
    else:
        source = selected.source_kind or "target"
    return (
        GitLabOperationTarget(selected.gitlab, selected.source_file.resolve()),
        source,
    )


def setup_gitlab_operation(
    explicit_target: Optional[str],
    explicit_binding: Optional[str],
    *,
    project: Optional[str],
    group: Optional[str],
    revision: Optional[str],
    api_client: Optional[GlabAPIClient] = None,
) -> tuple[BoundGitLabSession, JSONObject]:
    """Bind a live GitLab target and return its existing access read-back.

    :param explicit_target: Operator-selected target file, or normal resolution.
    :param explicit_binding: Operator-selected project binding, or normal resolution.
    :param project: Optional project path or numeric ID override.
    :param group: Optional group path or numeric ID override.
    :param revision: Optional claimed source revision of the installed skill.
    :param api_client: Existing API client, or the access check's default client.
    :returns: The same bound session used for access and its receipt, including
        a BLOCKED receipt when provider read-back does not prove access.
    :raises Exception: The original setup failure, carrying its target,
        credential or access stage in ``gitlab_setup_source``.
    """  # noqa: DOC503 - retain the original setup exception and type
    source = "target"
    try:
        target, target_source = resolve_gitlab_target(
            explicit_target, explicit_binding, dry_run=False
        )
        target_kind, reference = select_gitlab_reference(
            target, project=project, group=group
        )
        source = "credential"
        session = bind_gitlab_session(
            target,
            target_kind=target_kind,
            target_reference=reference,
            target_source=target_source,
            revision=revision,
        )
        source = "access"
        return session, check_gitlab_operation_access(session, api_client=api_client)
    except Exception as exc:
        exc.gitlab_setup_source = source
        raise


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


class MachineArgumentParser(argparse.ArgumentParser):
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
        self._raw_argv: tuple[str, ...] = ()

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

    def parse_args(
        self,
        args: Optional[Sequence[str]] = None,
        namespace: Optional[argparse.Namespace] = None,
    ) -> argparse.Namespace:
        """Parse arguments and retain the requested failure-output mode.

        :param args: Arguments to parse, or ``None`` to read process arguments.
        :param namespace: Existing namespace to populate, or ``None`` for a new one.
        :returns: Namespace containing parsed arguments and defaults.
        :raises SystemExit: For requested help or invalid CLI arguments.
        """  # noqa: DOC502 - argparse exits for help and invalid arguments.
        self._raw_argv = tuple(sys.argv[1:] if args is None else args)
        return super().parse_args(self._raw_argv, namespace)

    def error(self, message: str) -> None:
        """Report invalid arguments in the explicitly selected output format.

        :param message: Argparse diagnostic used when no machine format was selected.
        :raises SystemExit: With status 2 after writing the argument failure.
        """  # noqa: DOC502 - argparse exits for help and invalid arguments.
        modes = [
            flag.partition("=")[0]
            for flag in self._raw_argv
            if flag.partition("=")[0] in {"--json", "--yaml"}
        ]
        if not modes:
            super().error(message)
        mode = modes[0]
        kind = self.report_kind or COMMANDS.get(Path(self.prog).name, {}).get(
            "kind", "arguments"
        )
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


def add_log_arguments(result: argparse.ArgumentParser) -> None:
    """Add the shared diagnostic controls without adding provider arguments.

    :param result: Parser receiving the common diagnostic options.
    """
    result.add_argument(
        "--log-format",
        choices=("text", "json"),
        default="text",
        help="diagnostic format on stderr and in --log-file (default: text)",
    )
    result.add_argument(
        "--log-level",
        choices=("debug", "info", "warning", "error"),
        default="info",
        help="minimum diagnostic level (default: info)",
    )
    result.add_argument(
        "--log-file",
        metavar="PATH",
        help="also append sanitized diagnostics to PATH",
    )
    result.add_argument(
        "--run-id",
        metavar="ID",
        help="stable identifier for diagnostic logs",
    )


def log_event(
    args: argparse.Namespace,
    kind: str,
    event: str,
    status: str,
    *,
    elapsed: float = 0.0,
    error_class: Optional[str] = None,
    persist: bool = True,
    level: Optional[str] = None,
    details: Optional[Mapping[str, object]] = None,
) -> None:
    """Write a bounded diagnostic to stderr and an optional private log file.

    :param args: Namespace containing logging controls and operation metadata.
    :param kind: Command or component identifier included in the diagnostic.
    :param event: Lifecycle event being reported.
    :param status: Canonical result status for the event.
    :param elapsed: Elapsed time in seconds, recorded as nonnegative milliseconds.
    :param error_class: Classified failure reason, or ``None``.
    :param persist: Whether the optional log-file destination may be written.
    :param level: Explicit severity, or ``None`` to derive it from status.
    :param details: Additional data passed through the shared redaction function.
    :raises OSError: If stderr or the selected log file cannot be written.
    :raises KeyError: If the supplied severity or configured threshold is invalid.
    :raises TypeError: If details retain an unsupported object after redaction
        and therefore cannot be serialized as JSON.
    :raises RecursionError: If details contain a cycle or excessive nesting.
    :raises ValueError: If elapsed is NaN and cannot be rounded to milliseconds.
    :raises OverflowError: If elapsed is infinite and cannot be rounded to milliseconds.
    """  # noqa: DOC502 - Indexing, redaction, rounding, serialization, and I/O can fail.
    if level is None:
        level = "error" if status in {BLOCKED, "PARTIAL"} else "info"
    thresholds = {"debug": 0, "info": 1, "warning": 2, "error": 3}
    if thresholds[level] < thresholds[getattr(args, "log_level", "info")]:
        return
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": level,
        "run_id": sanitize(getattr(args, "run_id", None) or "", 80)
        .replace("\r", " ")
        .replace("\n", " "),
        "component": kind,
        "operation": sanitize(getattr(args, "action", None) or "check", 40),
        "mode": (
            "live_plan"
            if getattr(args, "live_plan", False)
            else "dry_run"
            if getattr(args, "dry_run", False) or not getattr(args, "apply", True)
            else "live"
        ),
        "event": event,
        "attempt": 1,
        "resource": kind,
        "result": status,
        "elapsed_time_ms": max(0, round(elapsed * 1000)),
        "error_class": error_class,
    }
    if details is not None:
        record["details"] = redact_tree(details)
    if getattr(args, "log_format", "text") == "json":
        line = json.dumps(record, sort_keys=True) + "\n"
    else:
        line = (
            f"{record['timestamp']} {level.upper()} {kind} "
            f"{record['operation']} {event} result={status} "
            f"run_id={record['run_id']}\n"
        )
    if details is not None and getattr(args, "log_format", "text") != "json":
        line = (
            line.rstrip("\n")
            + " "
            + json.dumps(record["details"], sort_keys=True)
            + "\n"
        )
    sys.stderr.write(line)
    destination = getattr(args, "log_file", None)
    if destination and persist:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(Path(destination).expanduser(), flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(line)


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
    data = {
        "schema_version": "1.0",
        "kind": kind,
        "status": BLOCKED,
        "captured_at": datetime.now(UTC).isoformat(),
        "execution_host": socket.getfqdn(),
        "tested_revision": getattr(args, "revision", None),
        "errors": [{"source": source, "reason": sanitize(reason, 240)}],
        "summary": {"record_count": 0, "error_count": 1},
    }
    if cleanup_errors:
        data["errors"].extend(cleanup_errors)
        data["summary"]["error_count"] = len(data["errors"])
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
        print(f"BLOCKED: {data['errors'][0]['reason']}", file=sys.stderr)
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


def execute_gitlab_job(args: argparse.Namespace) -> int:
    """Route one job get or bounded list through the GitLab authority.

    :param args: Parsed job command and universal output options.
    :returns: Shared status exit code.
    """
    from lib.python.core.collect import collect_gitlab_job, gitlab_job_reference

    kind = "gitlab_job"
    if args.describe:
        sys.stdout.write(json.dumps(describe(COMMAND_BY_KIND[kind]), indent=2) + "\n")
        return 0
    if args.action == "list":
        return execute_gitlab_job_list(args)
    if args.dry_run and (args.output_dir or args.log_file):
        return _failure(args, kind, "arguments", "dry_run_cannot_write_output")
    missing = missing_required_options(COMMAND_BY_KIND[kind], args)
    if missing:
        return _failure(
            args, kind, "arguments", f"{', '.join(missing)} is required for get"
        )
    if any(
        (
            args.project,
            args.status,
            args.pipeline_id,
            args.ref,
            args.last,
            args.from_time,
            args.to_time,
            args.name_glob,
            args.limit != 50,
            args.stuck_after != 600,
        )
    ):
        return _failure(args, kind, "arguments", "list_options_require_list")
    source = "target"
    started = time.monotonic()
    try:
        target, target_source = resolve_gitlab_target(
            args.target, args.binding, dry_run=args.dry_run
        )
        source = "arguments"
        project, _job_id = gitlab_job_reference(args.job_url, target.gitlab.host)
        filters = {"job_url": args.job_url, "search": args.search}
        if args.dry_run:
            data = report(kind, target.gitlab.url, filters, [], [])
            data["status"] = DRY_RUN
            data["target_source"] = target_source
            data["access"] = {
                "status": DRY_RUN,
                "target": {"kind": "project", "reference": project},
                "observed_capability": [],
            }
            data["collection_probes"] = [
                "exact GitLab identity and project reads",
                "job, pipeline, runner and bounded trace reads",
            ]
        else:
            source = "credential"
            session = bind_gitlab_session(
                target,
                target_kind="project",
                target_reference=project,
                target_source=f"{target_source};argv:--job-url",
                revision=args.revision,
            )
            source = "access"
            gate = check_gitlab_operation_access(session)
            if gate["status"] != PASS:
                data = report(kind, target.gitlab.url, filters, [], gate["errors"])
                data["status"] = BLOCKED
            else:
                source = "collect_gitlab_job"
                data = collect_gitlab_job(
                    target, args, credential=dict(session.environment)
                )
            data["access"] = gate
            data["execution_host"] = session.execution_host
            data["tested_revision"] = session.skill["revision"]["value"]
            data["skill"] = session.skill
            data["target_source"] = session.target_source
        rendered = emit(data, output_mode(args), args.output_dir)
        log_event(
            args, kind, "result", data["status"], elapsed=time.monotonic() - started
        )
        sys.stdout.write(rendered)
        return exit_code(data["status"])
    except (TargetError, RuntimeError, ValueError, OSError, TypeError, KeyError) as exc:
        return _failure(args, kind, source, str(exc))
    except Exception:  # noqa: BLE001 - preserve the machine result on provider failures
        return _failure(args, kind, source, "unexpected_runtime_failure")


def execute_gitlab_job_list(args: argparse.Namespace) -> int:
    """Bind one project and render the reusable bounded job-list read.

    :param args: Parsed list filters and universal output options.
    :returns: Shared status exit code.

    Target, provider, and collection exceptions become structured failure reports.
    """  # noqa: DOC501, DOC503 - the target-kind validation is caught by this adapter.
    from lib.python.core.gitlab.gitlab_jobs import JobQuery, list_jobs
    from lib.python.core.target import select_gitlab_reference

    kind = "gitlab_job"
    if args.job_url:
        return _failure(args, kind, "arguments", "job_url_requires_get")
    if args.dry_run and (args.output_dir or args.log_file):
        return _failure(args, kind, "arguments", "dry_run_cannot_write_output")
    source = "arguments"
    started = time.monotonic()
    try:
        query = JobQuery.from_args(args)
        source = "target"
        target, target_source = resolve_gitlab_target(
            args.target, args.binding, dry_run=args.dry_run
        )
        selected_kind, project = select_gitlab_reference(target, project=args.project)
        if selected_kind != "project":
            raise TargetError("gitlab_project_required_for_job_list")
        filters = {"project": project, **query.public()}
        if args.dry_run:
            data = report(kind, target.gitlab.url, filters, [], [])
            data["status"] = DRY_RUN
            data["access"] = {
                "status": DRY_RUN,
                "target": {"kind": "project", "reference": project},
                "observed_capability": [],
            }
            data["collection_probes"] = [
                "exact GitLab identity and project reads",
                "bounded GitLab project jobs pages",
            ]
            data["target_source"] = target_source
        else:
            source = "credential"
            session = bind_gitlab_session(
                target,
                target_kind="project",
                target_reference=project,
                target_source=(
                    f"{target_source};argv:--project" if args.project else target_source
                ),
                revision=args.revision,
            )
            source = "access"
            gate = check_gitlab_operation_access(session)
            if gate["status"] == PASS:
                source = "list_jobs"
                result = list_jobs(session, gate["target"]["id"], query)
                data = report(
                    kind, target.gitlab.url, filters, result.records, result.errors
                )
                if result.first_page_failed:
                    data["status"] = BLOCKED
                truncated = result.truncated
            else:
                data = report(kind, target.gitlab.url, filters, [], gate["errors"])
                data["status"] = BLOCKED
                truncated = False
            data["access"] = {
                "status": gate["status"],
                "identity": gate["identity"],
                "target": gate["target"],
                "observed_capability": gate["observed_capability"],
            }
            data["execution_host"] = session.execution_host
            revision = session.skill["revision"]
            data["tested_revision"] = (
                revision["value"] if revision["verified"] else None
            )
            data["skill"] = session.skill
            data["target_source"] = session.target_source
        data["operation"] = "list"
        data["truncated"] = truncated if not args.dry_run else False
        rendered = emit(data, output_mode(args), args.output_dir)
        log_event(
            args, kind, "result", data["status"], elapsed=time.monotonic() - started
        )
        sys.stdout.write(rendered)
        return exit_code(data["status"])
    except (TargetError, RuntimeError, ValueError, OSError, TypeError, KeyError) as exc:
        return _failure(args, kind, source, str(exc))
    except Exception:  # noqa: BLE001 - preserve the machine result on provider failures
        return _failure(args, kind, source, "unexpected_runtime_failure")
