"""Shared command options and sanitized diagnostics for read-only gate tools."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def add_options(parser: argparse.ArgumentParser) -> None:
    """Add standard argv; no environment, output, side effect, or cleanup."""
    parser.add_argument(
        "--dry-run", action="store_true", help="inspect without changing state"
    )
    parser.add_argument("--log-format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--log-level", choices=("debug", "info", "warning", "error"), default="info"
    )
    parser.add_argument("--log-file", type=Path, help="append sanitized status log")
    parser.add_argument(
        "--run-id", default="", help="caller-selected log correlation ID"
    )


def emit(args: argparse.Namespace, result: dict) -> None:
    """Emit status to stdout and safe log to stderr/file.

    Args: parsed options and status result. Environment: none. Stdout: JSON.
    Stderr: one safe status line. Exit classes: raises on log write failure.
    Side effects: optional log append. Idempotency: validation is read-only;
    logs may repeat. Cleanup: file handle closes on exit.
    """
    if args.dry_run:
        result = {**result, "mode": "DRY_RUN"}
    print(json.dumps(result, sort_keys=True))
    level = "info" if result["status"] == "PASS" else "error"
    if args.log_level == "error" and level != "error":
        return
    if args.log_level == "warning" and level == "info":
        return
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "run_id": args.run_id,
        "component": "validation",
        "result": result["status"],
        "error_count": len(result.get("problems", [])),
    }
    line = (
        json.dumps(event, sort_keys=True)
        if args.log_format == "json"
        else (
            f"{event['level']} validation {event['result']} "
            f"errors={event['error_count']} run_id={event['run_id']}"
        )
    )
    print(line, file=sys.stderr)
    if args.log_file:
        with args.log_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
