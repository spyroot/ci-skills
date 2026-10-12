"""Shared diagnostic arguments and bounded structured logging.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from lib.python.core.runtime import redact_tree, sanitize
from lib.python.sys.status import (
    BLOCKED,
)

LOG_LEVELS: Mapping[str, int] = {"debug": 0, "info": 1, "warning": 2, "error": 3}


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
        choices=tuple(LOG_LEVELS),
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
    if LOG_LEVELS[level] < LOG_LEVELS[getattr(args, "log_level", "info")]:
        return
    record = _log_record(
        args, kind, event, status, elapsed, error_class, level, details
    )
    line = _render_log(record, getattr(args, "log_format", "text"))
    sys.stderr.write(line)
    destination = getattr(args, "log_file", None)
    if destination and persist:
        _append_log(destination, line)


def _log_record(
    args: argparse.Namespace,
    kind: str,
    event: str,
    status: str,
    elapsed: float,
    error_class: Optional[str],
    level: str,
    details: Optional[Mapping[str, object]],
) -> MutableMapping[str, object]:
    """Build one diagnostic record using the shared redaction policy.

    :param args: Logging controls and operation metadata.
    :param kind: Component producing the event.
    :param event: Lifecycle event name.
    :param status: Event outcome.
    :param elapsed: Duration in seconds.
    :param error_class: Classified failure, or ``None``.
    :param level: Selected severity.
    :param details: Optional event metadata to redact.
    :returns: Sanitized diagnostic fields for text or JSON rendering.
    :raises ValueError: A nonfinite elapsed value cannot be rounded.
    :raises OverflowError: An infinite elapsed value cannot be rounded.
    :raises RecursionError: Event details contain a cycle or excessive nesting.
    """  # noqa: DOC502 - Rounding and shared redaction validate supplied values.
    record: MutableMapping[str, object] = {
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
    return record


def _render_log(record: Mapping[str, object], mode: str) -> str:
    """Render one bounded diagnostic without writing it.

    :param record: Sanitized event fields produced by _log_record.
    :param mode: Selected ``text`` or ``json`` output format.
    :returns: One newline-terminated diagnostic.
    :raises KeyError: Required event fields are absent.
    :raises TypeError: A supplied detail is not JSON serializable.
    :raises ValueError: A supplied detail contains a circular container.
    """  # noqa: DOC502 - Required fields and JSON serialization validate the record.
    if mode == "json":
        return json.dumps(record, sort_keys=True) + "\n"
    line = (
        f"{record['timestamp']} {str(record['level']).upper()} {record['component']} "
        f"{record['operation']} {record['event']} result={record['result']} "
        f"run_id={record['run_id']}"
    )
    if "details" in record:
        line += " " + json.dumps(record["details"], sort_keys=True)
    return line + "\n"


def _append_log(destination: str, line: str) -> None:
    """Append one diagnostic to the caller's exact private destination.

    :param destination: Explicit log filename, with an optional home prefix.
    :param line: Sanitized diagnostic ready for writing.
    :raises OSError: The destination cannot be opened or written.
    """  # noqa: DOC502 - The file descriptor and writer propagate I/O failures.
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(Path(destination).expanduser(), flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(line)
