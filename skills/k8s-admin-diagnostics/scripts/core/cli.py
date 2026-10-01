"""Shared CLI adapter for explicit targets and output modes."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .access import check_access, dry_run_access
from .credentials import bind_sources
from .report import emit
from .runtime import sanitize
from .status import BLOCKED, PASS, exit_code
from .target import Target, TargetError, load_target


def parser(description: str, *, output_dir: bool = True) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=description,
        epilog="Example: %(prog)s --target ~/.config/ci-skills/target.toml --json",
    )
    result.add_argument(
        "--target",
        required=True,
        metavar="PATH",
        help="explicit nonsecret TOML target file",
    )
    modes = result.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", help="print versioned JSON")
    modes.add_argument("--yaml", action="store_true", help="print versioned YAML")
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
    source = "target"
    try:
        target = load_target(args.target)
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
        if live_checks and not args.dry_run and gate["status"] == PASS:
            from .live import collect_live_checks

            data = collect_live_checks(target, args, gate)
        elif collect is None or args.dry_run or gate["status"] != PASS:
            data = gate
        else:
            source = collect.__name__
            data = collect(target, args)
        mode = "json" if args.json else "yaml" if args.yaml else "human"
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
