"""Shared CLI adapter for explicit targets and output modes."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .access import access_evidence, check_access, dry_run_access
from .argument_parser import StructuredParser
from .catalog import (
    COMMAND_BY_KIND,
    PROJECT_DIR,
    TARGET_FILENAME,
    describe,
    missing_required_options,
)
from .credentials import bind_sources
from .portable import portable
from .project_binding import (
    resolve_target as resolve_project_target,
    resolve_target_file as resolve_target,
    target_candidates as _target_candidates,
)
from .report import emit
from .runtime import redact_tree, sanitize
from .status import (
    BLOCKED,
    PASS,
    PROFILE_BASE,
    PROFILE_DRY_RUN,
    PROFILE_FULL,
    exit_code,
)
from .target import Target, TargetError

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
}
ACCESS_CHECK_KIND = "access_check"


def output_mode(args: argparse.Namespace) -> str:
    """Choose the output format, defaulting to JSON for a non-terminal reader.

    An agent should not have to discover `--json`. A human at a terminal wants
    the summary; anything reading a pipe wants the machine-readable document,
    and that is the overwhelmingly common case for this skill. So the default
    follows the reader: a TTY gets `human`, a pipe or file gets `json`. The
    three flags remain explicit overrides in both directions, `--human`
    included, so a person piping into a pager still has a way to ask.
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


def parser(description: str, *, output_dir: bool = True) -> argparse.ArgumentParser:
    result = StructuredParser(
        description=description,
        epilog=(
            "Output: a terminal gets the human summary; a pipe or file gets "
            "versioned JSON, so no flag is needed when a program reads this. "
            "Force it with --json, --yaml or --human. "
            "Exit 0 = PASS or DRY_RUN, 2 = BLOCKED or PARTIAL. "
            "Run --describe for this command's machine-readable contract, or "
            "see tools.json for the whole skill. "
            "Target resolves in order: --target or --binding, CI_SKILLS_TARGET "
            "or K8S_ADMIN_DIAGNOSTICS_BINDING, "
            f"./{PROJECT_DIR}/{TARGET_FILENAME}, {DEFAULT_TARGET}. "
            "Example: %(prog)s --json"
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
    if output_dir:
        result.add_argument(
            "--output-dir", metavar="PATH", help="write paired JSON and human reports"
        )
    return result


def _failure(args: argparse.Namespace, kind: str, source: str, reason: str) -> int:
    """Emit a stable machine-readable failure even when normal rendering fails."""
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


def execute(
    args: argparse.Namespace,
    collect: Callable[[Target, argparse.Namespace], dict[str, Any]] | None = None,
    *,
    live_checks: bool = False,
) -> int:
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
        target = resolve_project_target(
            args.target, getattr(args, "binding", None), dry_run=args.dry_run
        )
        publication = bool(getattr(args, "publication", False))
        if not args.dry_run:
            target = bind_sources(target, revision=getattr(args, "revision", None))
        source = "access_check"
        gate = (
            dry_run_access(target, publication=publication)
            if args.dry_run
            else check_access(target, publication=publication)
        )
        if args.dry_run and collect is not None:
            probes = {
                "collect_storage": [
                    "concurrent node, Pod, PVC/PV, StorageClass, CSI, attachment, controller reads"
                ],
                "collect_events": [
                    "core and events.k8s.io reads",
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
            partial.write_text(
                json.dumps(portable(redact_tree(data)), indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            os.replace(partial, destination)
        mode = output_mode(args)
        sys.stdout.write(emit(data, mode, getattr(args, "output_dir", None)))
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
