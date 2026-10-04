"""Shared CLI adapter for explicit targets and output modes."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from textwrap import fill
from typing import Any

from .access import (
    access_evidence,
    check_access,
    check_gitlab_operation_access,
    dry_run_access,
)
from .catalog import (
    AUTHORITIES,
    COMMAND_BY_KIND,
    COMMANDS,
    OUTPUT_MODE_DESCRIPTIONS,
    PROJECT_DIR,
    TARGET_FILENAME,
    describe,
    missing_required_options,
)
from .credentials import bind_gitlab_session, bind_sources
from .portable import portable
from .project_binding import BINDING_ENV, resolve_target_file
from .project_binding import resolve_target as resolve_project_target
from .report import emit, report
from .runtime import redact_tree, sanitize
from .status import (
    BLOCKED,
    DRY_RUN,
    PASS,
    PROFILE_BASE,
    PROFILE_DRY_RUN,
    PROFILE_FULL,
    exit_code,
)
from .target import GitLabOperationTarget, Target, TargetError

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


def resolve_target(explicit: str | None) -> tuple[Path, str]:
    """Preserve the target-file resolver used by existing command consumers."""
    return resolve_target_file(explicit)


# Summary: Resolve one GitLab target without merging lower configuration tiers.
# Arguments: explicit_target/binding select source; dry_run controls required access.
# Environment inputs: declared target tier or binding environment; Stdout: none; Stderr: none.
# Exit classes: TargetError for unresolved GitLab target; Side effects: reads selected config.
# Idempotency: follows unchanged config; Cleanup: file readers close their handles.
def resolve_gitlab_target(
    explicit_target: str | None,
    explicit_binding: str | None,
    *,
    dry_run: bool = False,
) -> tuple[GitLabOperationTarget, str]:
    """Return the selected GitLab target and its reported source.

    :param explicit_target: Optional target TOML path from ``--target``.
    :param explicit_binding: Optional project binding path from ``--binding``.
    :param dry_run: Whether the resolver may plan without live credentials.
    :returns: GitLab operation target and source tier description.
    :raises TargetError: If GitLab or its selected source file is absent.
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


# Summary: Select an explicit output mode or infer it from stdout TTY state.
# Arguments: args carries json/yaml/human flags; Environment inputs: stdout TTY state.
# Stdout: none; Stderr: none; Exit classes: no process exit.
# Side effects: queries stdout TTY state; Idempotency: stable for same flags and TTY; Cleanup: none.
def output_mode(args: argparse.Namespace) -> str:
    """Choose human text for a terminal and JSON for a pipe by default.

    :param args: Parsed explicit output-mode flags.
    :returns: ``json``, ``yaml``, or ``human``.
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

    # Summary: Initialize a parser with its optional report kind.
    # Arguments: args/kwargs forward argparse settings; report_kind names result.
    # Environment inputs: none; Stdout: none; Stderr: none; Exit classes: argparse errors.
    # Side effects: parser state only; Idempotency: new equivalent parser per call; Cleanup: none.
    def __init__(
        self, *args: Any, report_kind: str | None = None, **kwargs: Any
    ) -> None:
        """Store the command kind used for structured usage failures.

        :param args: Positional ArgumentParser constructor inputs.
        :param report_kind: Optional result kind for argument failures.
        :param kwargs: Keyword ArgumentParser constructor inputs.
        """
        super().__init__(*args, **kwargs)
        self.report_kind = report_kind
        self._raw_argv: tuple[str, ...] = ()

    # Summary: Preserve requested flags before argparse validates arguments.
    # Arguments: args optionally overrides process argv; namespace receives parsed values.
    # Environment inputs: process argv when args is absent; Stdout: none; Stderr: usage on error.
    # Exit classes: argparse usage exit; Side effects: updates parser argv snapshot.
    # Idempotency: same argv gives same parse; Cleanup: none.
    def parse_args(
        self, args: list[str] | None = None, namespace: argparse.Namespace | None = None
    ) -> argparse.Namespace:
        """Parse arguments while retaining the flags needed for error output.

        :param args: Optional explicit argument vector.
        :param namespace: Optional namespace populated by argparse.
        :returns: Parsed argument namespace.
        :raises SystemExit: If argparse rejects an argument or value.
        """
        self._raw_argv = tuple(sys.argv[1:] if args is None else args)
        return super().parse_args(self._raw_argv, namespace)

    # Summary: Emit usage failure in the caller's requested machine format.
    # Arguments: message is argparse's validation detail; Environment inputs: prior argv snapshot.
    # Stdout: structured failure for JSON/YAML; Stderr: ordinary argparse error otherwise.
    # Exit classes: SystemExit 2; Side effects: writes one error report.
    # Idempotency: same invalid argv gives same class, timestamps may change; Cleanup: none.
    def error(self, message: str) -> None:
        """Report invalid arguments and terminate with usage status.

        :param message: Argparse validation detail for human output.
        :raises SystemExit: Always exits after a usage failure.
        """
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

    # Summary: Render every accepted option in the required help section order.
    # Arguments: self is the configured parser; Environment inputs: none.
    # Stdout: none, returns help text; Stderr: none; Exit classes: no process exit.
    # Side effects: none; Idempotency: same parser gives same help; Cleanup: none.
    def format_help(self) -> str:
        """Present command help with all parser actions and output modes.

        :returns: Human help with summary, examples, options, modes, and usage.
        """
        lines = ["Summary:", f"  {self.description or self.prog}", ""]
        if self.epilog:
            lines.extend(
                [
                    "Description:",
                    fill(
                        self.epilog.replace("%(prog)s", self.prog),
                        width=88,
                        initial_indent="  ",
                        subsequent_indent="  ",
                    ),
                    "",
                ]
            )
        lines.extend(
            [
                "Examples:",
                "  # Inspect this command's machine-readable contract.",
                f"  {self.prog} --describe",
                "",
                "Options:",
            ]
        )
        for action in self._actions:
            if action.help is argparse.SUPPRESS:
                continue
            names = ", ".join(action.option_strings) or action.dest
            if action.nargs != 0:
                names += f" {action.metavar or action.dest.upper()}"
            explanation = (action.help or "").replace("%(prog)s", self.prog)
            lines.append(
                fill(
                    f"{names}: {explanation}",
                    width=88,
                    initial_indent="  ",
                    subsequent_indent="    ",
                )
            )
        lines.extend(["", "Output modes:"])
        for name, explanation in OUTPUT_MODE_DESCRIPTIONS:
            lines.append(f"  {name}: {explanation}")
        usage = self.format_usage().strip().removeprefix("usage: ").strip()
        lines.extend(["", "Usage:", f"  {usage}", ""])
        return "\n".join(lines)


# Summary: Build the shared target, output, logging, and dry-run option parser.
# Arguments: description is command purpose; output_dir selects file option; kind names report.
# Environment inputs: none; Stdout: none; Stderr: none; Exit classes: no process exit.
# Side effects: constructs parser in memory.
# Idempotency: same inputs give same options; Cleanup: none.
def parser(
    description: str, *, output_dir: bool = True, kind: str | None = None
) -> argparse.ArgumentParser:
    """Create the universal CLI options used by collector commands.

    :param description: Human command purpose shown in help.
    :param output_dir: Whether paired report file output is offered.
    :param kind: Optional report kind for structured argument failures.
    :returns: Parser that callers extend with command-specific arguments.
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
            "Target resolves in order: --target or --binding, "
            "CI_SKILLS_TARGET or CI_SKILLS_BINDING, "
            f"./{PROJECT_DIR}/{TARGET_FILENAME}, {DEFAULT_TARGET}."
        ),
    )
    selection = result.add_mutually_exclusive_group()
    selection.add_argument(
        "--target",
        metavar="PATH",
        default=None,
        help=(
            "nonsecret TOML target file "
            f"(default: {DEFAULT_TARGET}, override with CI_SKILLS_TARGET)"
        ),
    )
    selection.add_argument(
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
    if output_dir:
        result.add_argument(
            "--output-dir", metavar="PATH", help="write paired JSON and human reports"
        )
    return result


# Summary: Write one sanitized diagnostic to stderr and an optional log file.
# Arguments: args selects format/file; kind, event, status and timing describe result.
# Environment inputs: current clock; Stdout: none; Stderr: one diagnostic line.
# Exit classes: OSError if optional file write fails; Side effects: stderr and optional append.
# Idempotency: repeated call appends a new timestamped record; Cleanup: closes file descriptor.
def log_event(
    args: argparse.Namespace,
    kind: str,
    event: str,
    status: str,
    *,
    elapsed: float = 0.0,
    error_class: str | None = None,
    persist: bool = True,
) -> None:
    """Emit a classified and sanitized event for one command action.

    :param args: Parsed log level, format, file, run ID, and mode.
    :param kind: Command component name.
    :param event: Diagnostic event name.
    :param status: Result status used to choose severity.
    :param elapsed: Duration in seconds for the diagnostic record.
    :param error_class: Optional classified failure source.
    :param persist: Whether an optional log-file append is allowed.
    :raises OSError: If the selected log file cannot be opened or written.
    """
    level = "error" if status in {BLOCKED, "PARTIAL"} else "info"
    thresholds = {"debug": 0, "info": 1, "warning": 2, "error": 3}
    if thresholds[level] < thresholds[getattr(args, "log_level", "info")]:
        return
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
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
    if getattr(args, "log_format", "text") == "json":
        line = json.dumps(record, sort_keys=True) + "\n"
    else:
        line = (
            f"{record['timestamp']} {level.upper()} {kind} "
            f"{record['operation']} {event} result={status} "
            f"run_id={record['run_id']}\n"
        )
    sys.stderr.write(line)
    destination = getattr(args, "log_file", None)
    if destination and persist:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(Path(destination).expanduser(), flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(line)


# Summary: Emit a classified failure even if ordinary rendering fails.
# Arguments: args chooses output; kind/source/reason identify failure.
# Environment inputs: clock and local hostname; Stdout: machine failure if requested.
# Stderr: human failure and diagnostic log; Exit classes: returns status 2.
# Side effects: writes output and optional log.
# Idempotency: new timestamp each call; Cleanup: log helper closes file.
def _failure(args: argparse.Namespace, kind: str, source: str, reason: str) -> int:
    """Return a structured BLOCKED result for a command failure.

    :param args: Parsed output and logging options.
    :param kind: Command report kind.
    :param source: Stage that failed.
    :param reason: Diagnostic detail sanitized before output.
    :returns: Blocked exit status 2.
    """
    data = {
        "schema_version": "1.0",
        "kind": kind,
        "status": BLOCKED,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "execution_host": socket.getfqdn(),
        "tested_revision": getattr(args, "revision", None),
        "errors": [{"source": source, "reason": sanitize(reason, 240)}],
        "summary": {"record_count": 0, "error_count": 1},
    }
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


# Summary: Resolve access, run a selected collector, and emit its result.
# Arguments: args holds CLI options; collect selects a reader; live_checks enables full receipt.
# Environment inputs: selected target, credentials, clock, and host identity.
# Stdout: versioned report or command contract; Stderr: classified diagnostics.
# Exit classes: 0 for PASS/DRY_RUN, 2 for BLOCKED/PARTIAL or failure.
# Side effects: live reads and optional receipt/report files; Idempotency: follows target state.
# Cleanup: temporary receipt is removed on every exit, output helpers close files.
def execute(
    args: argparse.Namespace,
    collect: Callable[[Target, argparse.Namespace], dict[str, Any]] | None = None,
    *,
    live_checks: bool = False,
) -> int:
    """Run the shared access gate and selected collection path.

    :param args: Parsed target, mode, filters, and output options.
    :param collect: Optional collector; omitted for the access receipt.
    :param live_checks: Whether to run expanded cross-authority checks.
    :returns: Exit status matching the structured result.
    """
    started = time.monotonic()
    kind = (
        ACCESS_CHECK_KIND
        if collect is None
        else COLLECTOR_KINDS.get(collect.__name__, collect.__name__)
    )
    # The catalog is keyed by script name, but a command must not learn its own
    # identity from argv[0]: a symlink, a wrapper or a renamed installed copy
    # would then print another command's contract, or raise KeyError where every
    # other path returns a structured envelope. The report kind IS the identity.
    # `.get`, because a caller may pass a collector this catalog does not
    # declare. Every real command's kind IS declared -- COLLECTOR_KINDS is
    # checked against the catalog by test -- so this only keeps an undeclared
    # one from raising where every other path returns an envelope.
    script = COMMAND_BY_KIND.get(kind)
    if getattr(args, "describe", False):
        if script is None:
            return _failure(args, kind, "arguments", f"no declared contract for {kind}")
        contract = describe(script)
        sys.stdout.write(json.dumps(contract, indent=2, sort_keys=True) + "\n")
        return 0
    if args.dry_run and any(
        getattr(args, name, None) for name in ("output_dir", "receipt_out", "log_file")
    ):
        return _failure(args, kind, "arguments", "dry_run_cannot_write_output")
    missing = missing_required_options(script, args) if script else []
    if missing:
        # Enforced here rather than by argparse because --describe must answer
        # without it, and enforced BEFORE the access gate because a forgotten
        # argument is not an access failure: reporting it as one sends the
        # reader to the wrong authority.
        return _failure(
            args, kind, "arguments", f"{', '.join(missing)} is required; see --describe"
        )
    source = "target"
    try:
        required_surfaces = (
            tuple(COMMANDS[script]["requires"]) if script else AUTHORITIES
        )
        target = resolve_project_target(
            args.target,
            getattr(args, "binding", None),
            dry_run=args.dry_run,
            required_surfaces=required_surfaces,
        )
        publication = bool(getattr(args, "publication", False))
        if not args.dry_run:
            target = bind_sources(target, revision=getattr(args, "revision", None))
        source = "access_check"
        needs_cilium = kind in {ACCESS_CHECK_KIND, "cilium_status"}
        gate = (
            dry_run_access(target, publication=publication, cilium=needs_cilium)
            if args.dry_run
            else check_access(target, publication=publication, cilium=needs_cilium)
        )
        if args.dry_run and collect is not None:
            probes = {
                "collect_storage": [
                    "concurrent node, Pod, PVC/PV, StorageClass, CSI, attachment, controller reads"
                ],
                "collect_events": [
                    "events.k8s.io read; core fallback only if resource is absent",
                    "time and object filtering",
                ],
                "collect_cilium": [
                    "concurrent DaemonSet, Pod, operator, CiliumNode reads",
                    "non-TTY health on ready agents",
                ],
                "collect_gitlab_job": [
                    "job, pipeline, runner API reads",
                    "bounded job trace read",
                ],
                "collect_ceph_cluster": [
                    "concurrent Ceph status, OSD tree, inactive PG and OSD/monitor Pod reads",
                    "node, Ready and Pod condition filtering",
                ],
                "collect_mtu_consistency": [
                    "read selected nodes and create temporary oc debug Pods on apply",
                    "read PCI Ethernet IPv4 links and verify Pod cleanup",
                ],
            }
            gate["collection_probes"] = probes.get(collect.__name__, [])
            gate["filters"] = {
                key: value
                for key, value in vars(args).items()
                if key
                not in {"target", "binding", "json", "yaml", "dry_run", "output_dir"}
            }
        # Only access_check.py runs the expanded bundle, so every report names
        # which gate it actually passed rather than the docs implying one.
        gate["profile"] = (
            PROFILE_DRY_RUN
            if args.dry_run
            else (PROFILE_FULL if live_checks else PROFILE_BASE)
        )
        if live_checks and not args.dry_run and gate["status"] == PASS:
            from .live import collect_live_checks

            data = collect_live_checks(target, args, gate)
        elif collect is None or args.dry_run or gate["status"] != PASS:
            data = gate
        else:
            source = collect.__name__
            data = collect(target, args)
            data["access"] = access_evidence(gate)
        receipt_out = getattr(args, "receipt_out", None)
        if receipt_out:
            # The committable form: produced by code, never by hand-editing, so
            # a receipt that reaches a public repository cannot carry a host
            # path.
            destination = Path(receipt_out).expanduser()
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_name(f".{destination.name}.partial")
            try:
                partial.write_text(
                    json.dumps(portable(redact_tree(data)), indent=2, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )
                os.replace(partial, destination)
            finally:
                partial.unlink(missing_ok=True)
        mode = output_mode(args)
        rendered = emit(data, mode, getattr(args, "output_dir", None))
        log_event(
            args, kind, "result", data["status"], elapsed=time.monotonic() - started
        )
        sys.stdout.write(rendered)
        return exit_code(data["status"])
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
    except Exception:  # noqa: BLE001 - preserve structured output for unexpected provider data
        return _failure(args, kind, source, "unexpected_runtime_failure")


# Summary: Bind a GitLab-only session and report one exact job.
# Arguments: args supplies job URL, target, filter, and output options.
# Environment inputs: selected GitLab target, credential, clock, and hostname.
# Stdout: job report or command contract; Stderr: classified diagnostics.
# Exit classes: 0 for PASS/DRY_RUN, 2 for BLOCKED/PARTIAL or failure.
# Side effects: GitLab GETs and optional report file; Idempotency: follows live job state.
# Cleanup: delegated output helper closes report files.
def execute_gitlab_job(args: argparse.Namespace) -> int:
    """Read one job through the GitLab-only target and bound session.

    :param args: Parsed job URL, target, filter, and output options.
    :returns: Exit status matching the job report.
    """
    from .collect import collect_gitlab_job, gitlab_job_reference

    kind = "gitlab_job"
    if args.describe:
        sys.stdout.write(json.dumps(describe(COMMAND_BY_KIND[kind]), indent=2) + "\n")
        return 0
    if args.dry_run and (args.output_dir or args.log_file):
        return _failure(args, kind, "arguments", "dry_run_cannot_write_output")
    missing = missing_required_options(COMMAND_BY_KIND[kind], args)
    if missing:
        return _failure(
            args, kind, "arguments", f"{', '.join(missing)} is required; see --describe"
        )
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
