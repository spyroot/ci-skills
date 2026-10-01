"""Shared CLI adapter for explicit targets and output modes."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .access import access_evidence, check_access, dry_run_access
from .catalog import PROJECT_DIR, TARGET_FILENAME, describe
from .credentials import bind_sources
from .portable import portable
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
from .target import Target, TargetError, load_target

# The documented per-host location. Keeping it here rather than in each script
# means one answer to "where does the target live", and the script-interface
# rule that a value the host already knows is not a required argument.
# Rendered from the declared protocol so help text cannot drift from behaviour.
# Rendered from the declared protocol so help text cannot drift from behaviour.
DEFAULT_TARGET = f"~/{PROJECT_DIR}/{TARGET_FILENAME}"


def _target_candidates() -> list[tuple[str, Path]]:
    """Return the target search path, in declared order."""
    candidates: list[tuple[str, Path]] = []
    configured = (os.environ.get("CI_SKILLS_TARGET") or "").strip()
    if configured:
        candidates.append(("env:CI_SKILLS_TARGET", Path(configured).expanduser()))
    candidates.append(("project", Path(PROJECT_DIR) / TARGET_FILENAME))
    candidates.append(("user", Path(DEFAULT_TARGET).expanduser()))
    return candidates


def resolve_target(explicit: str | None) -> tuple[Path, str]:
    """Resolve the target file and say which declared source supplied it.

    First match wins. An explicit path that does not exist is an error rather
    than a fallback: silently reading a different target than the one asked for
    is how a command ends up aimed at the wrong cluster.
    """
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise TargetError(f"target_file_missing:{path}")
        return path, "argv:--target"
    for source, path in _target_candidates():
        if path.is_file():
            return path, source
    searched = ", ".join(str(path) for _source, path in _target_candidates())
    raise TargetError(
        "no_target_file. This skill RESOLVES a target; it cannot create one. "
        "Copy target.toml.template to "
        f"{DEFAULT_TARGET} and fill in your authorities, or have the calling "
        "project supply one via --target or CI_SKILLS_TARGET. "
        f"Searched: {searched}"
    )


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
    result = argparse.ArgumentParser(
        description=description,
        epilog=(
            "Output: a terminal gets the human summary; a pipe or file gets "
            "versioned JSON, so no flag is needed when a program reads this. "
            "Force it with --json, --yaml or --human. "
            "Exit 0 = PASS or DRY_RUN, 2 = BLOCKED or PARTIAL. "
            "Run --describe for this command's machine-readable contract, or "
            "see tools.json for the whole skill. "
            f"Target resolves in order: --target, CI_SKILLS_TARGET, "
            f"./{PROJECT_DIR}/{TARGET_FILENAME}, {DEFAULT_TARGET}. "
            "Example: %(prog)s --json"
        ),
    )
    result.add_argument(
        "--target",
        metavar="PATH",
        default=None,
        help=(
            "nonsecret TOML target file "
            f"(default: {DEFAULT_TARGET}, override with CI_SKILLS_TARGET)"
        ),
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
    if getattr(args, "json", False) or getattr(args, "yaml", False):
        try:
            sys.stdout.write(emit(data, "yaml" if args.yaml else "json"))
        except (RuntimeError, OSError):
            sys.stdout.write(json.dumps(data, sort_keys=True) + "\n")
    else:
        print(f"BLOCKED: {data['errors'][0]['reason']}", file=sys.stderr)
    return 2


def execute(
    args: argparse.Namespace,
    collect: Callable[[Target, argparse.Namespace], dict[str, Any]] | None = None,
    *,
    live_checks: bool = False,
) -> int:
    kinds = {
        "collect_storage": "storage_report",
        "collect_events": "event_trace",
        "collect_cilium": "cilium_status",
        "collect_gitlab_job": "gitlab_job",
    }
    kind = (
        "access_check"
        if collect is None
        else kinds.get(collect.__name__, collect.__name__)
    )
    if getattr(args, "describe", False):
        contract = describe(Path(sys.argv[0]).name)
        sys.stdout.write(json.dumps(contract, indent=2, sort_keys=True) + "\n")
        return 0
    source = "target"
    try:
        target_path, target_source = resolve_target(args.target)
        target = replace(
            load_target(target_path),
            source_file=target_path,
            source_kind=target_source,
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
            }
            gate["collection_probes"] = probes.get(collect.__name__, [])
            gate["filters"] = {
                key: value
                for key, value in vars(args).items()
                if key not in {"target", "json", "yaml", "dry_run", "output_dir"}
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
