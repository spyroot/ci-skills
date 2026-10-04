#!/usr/bin/env python3
"""Verify one exact GitLab identity and project or group for operations."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from datetime import datetime, timezone
from typing import Any

from core.access import check_gitlab_operation_access
from core.catalog import describe
from core.cli import _failure, log_event, output_mode, parser, resolve_gitlab_target
from core.credentials import bind_gitlab_session
from core.portable import write_portable_receipt
from core.report import emit
from core.status import DRY_RUN, exit_code
from core.target import TargetError, select_gitlab_reference


def build_parser() -> argparse.ArgumentParser:
    """Expose the universal tier and one exact GitLab target selector."""
    cli = parser(
        "Check one GitLab identity and exact project or group for operations.",
        kind="gitlab_access",
    )
    cli.add_argument("operation", nargs="?", choices=("check",), help="check access")
    selection = cli.add_mutually_exclusive_group()
    selection.add_argument(
        "--project", metavar="PATH_OR_ID", help="selected project path or numeric ID"
    )
    selection.add_argument(
        "--group", metavar="PATH_OR_ID", help="selected group path or numeric ID"
    )
    cli.add_argument(
        "--receipt-out",
        metavar="PATH",
        help="write a sanitized, committable live access receipt",
    )
    return cli


def _dry_run(
    *, target_file: str, target_source: str, target_kind: str, reference: str
) -> dict[str, Any]:
    """Describe intent without loading any credential or contacting GitLab."""
    return {
        "schema_version": "1.0",
        "kind": "gitlab_access",
        "status": DRY_RUN,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "execution_host": socket.getfqdn(),
        "target_file": target_file,
        "target_source": target_source,
        "target": {"kind": target_kind, "reference": reference},
        "identity": None,
        "credential_source": None,
        "credential_digest": None,
        "observed_capability": [],
        "errors": [],
    }


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    args = build_parser().parse_args(argv)
    if args.describe:
        sys.stdout.write(json.dumps(describe("gitlab_access.py"), indent=2) + "\n")
        return 0
    if args.operation != "check":
        return _failure(args, "gitlab_access", "arguments", "check_required")
    if args.dry_run and (args.output_dir or args.receipt_out or args.log_file):
        return _failure(
            args, "gitlab_access", "arguments", "dry_run_cannot_write_output"
        )
    source = "target"
    try:
        target, target_source = resolve_gitlab_target(
            args.target, args.binding, dry_run=args.dry_run
        )
        target_kind, reference = select_gitlab_reference(
            target, project=args.project, group=args.group
        )
        if args.dry_run:
            result = _dry_run(
                target_file=str(target.source_file),
                target_source=target_source,
                target_kind=target_kind,
                reference=reference,
            )
        else:
            source = "credential"
            session = bind_gitlab_session(
                target,
                target_kind=target_kind,
                target_reference=reference,
                target_source=target_source,
                revision=args.revision,
            )
            source = "access"
            result = check_gitlab_operation_access(session)
        if args.receipt_out:
            source = "receipt"
            write_portable_receipt(result, args.receipt_out)
        rendered = emit(result, output_mode(args), args.output_dir)
        log_event(
            args,
            "gitlab_access",
            "result",
            result["status"],
            elapsed=time.monotonic() - started,
        )
        sys.stdout.write(rendered)
        return exit_code(result["status"])
    except (TargetError, OSError, RuntimeError, ValueError, TypeError) as exc:
        return _failure(args, "gitlab_access", source, str(exc))
    except Exception:  # noqa: BLE001 - keep unexpected failures machine-readable
        return _failure(args, "gitlab_access", source, "unexpected_runtime_failure")


if __name__ == "__main__":
    raise SystemExit(main())
