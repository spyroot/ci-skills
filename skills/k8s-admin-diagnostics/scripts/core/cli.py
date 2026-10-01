"""Shared CLI adapter for explicit targets and output modes."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Any

from .access import check_access, dry_run_access
from .report import emit
from .target import Target, TargetError, load_target


def parser(description: str, *, output_dir: bool = True) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=description, epilog="Example: %(prog)s --target ~/.config/ci-skills/target.toml --json")
    result.add_argument("--target", required=True, metavar="PATH", help="explicit nonsecret TOML target file")
    modes = result.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", help="print versioned JSON")
    modes.add_argument("--yaml", action="store_true", help="print versioned YAML")
    result.add_argument("--dry-run", action="store_true", help="show planned probes without contacting APIs")
    if output_dir:
        result.add_argument("--output-dir", metavar="PATH", help="write paired JSON and human reports")
    return result


def execute(args: argparse.Namespace, collect: Callable[[Target, argparse.Namespace], dict[str, Any]] | None = None) -> int:
    try:
        target = load_target(args.target)
        gate = dry_run_access(target) if args.dry_run else check_access(target)
        if args.dry_run and collect is not None:
            probes = {
                "collect_storage": ["concurrent node, Pod, PVC/PV, StorageClass, CSI, attachment, controller reads"],
                "collect_events": ["core and events.k8s.io reads", "time and object filtering"],
                "collect_cilium": ["concurrent DaemonSet, Pod, operator, CiliumNode reads", "non-TTY health on ready agents"],
                "collect_gitlab_job": ["job, pipeline, runner API reads", "bounded job trace read"],
            }
            gate["collection_probes"] = probes.get(collect.__name__, [])
            gate["filters"] = {key: value for key, value in vars(args).items()
                               if key not in {"target", "json", "yaml", "dry_run", "output_dir"}}
        if collect is None or args.dry_run or gate["status"] != "PASS":
            data = gate
        else:
            data = collect(target, args)
        mode = "json" if args.json else "yaml" if args.yaml else "human"
        sys.stdout.write(emit(data, mode, getattr(args, "output_dir", None)))
        return 0 if data["status"] == "PASS" or data["status"] == "DRY_RUN" else 2
    except (TargetError, RuntimeError, ValueError, OSError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
