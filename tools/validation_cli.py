"""Shared command options and sanitized diagnostics for read-only gate tools."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from textwrap import fill


# Summary: expose maintenance help without required target arguments
# Arguments: parser, namespace, option value and spelling; Environment inputs: none
# Stdout: machine help JSON; Stderr: none; Exit classes: exits zero
# Side effects: stdout write; Idempotency: stable parser; Cleanup: none
class DescribeAction(argparse.Action):
    """Publish parser-derived machine help before required target validation."""

    # Summary: emit parser-derived machine help; Arguments: parser and action inputs
    # Environment inputs: none; Stdout: machine help JSON; Stderr: none
    # Exit classes: SystemExit zero; Side effects: stdout write
    # Idempotency: stable parser; Cleanup: none
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        """Print the accepted interface without requiring a repository path.

        :param parser: Configured maintenance command parser.
        :param namespace: Partial argument namespace from argparse.
        :param values: Unused zero-argument option value.
        :param option_string: Option spelling that selected machine help.
        :raises SystemExit: Machine help has been written successfully.
        """
        del namespace, values, option_string
        if not isinstance(parser, ValidationArgumentParser):
            parser.error("machine help requires ValidationArgumentParser")
        print(json.dumps(parser.machine_help(), indent=2, sort_keys=True))
        parser.exit(0)


class ValidationArgumentParser(argparse.ArgumentParser):
    """Render the shared validation tools' human and machine interfaces."""

    # Summary: derive the accepted option map; Arguments: configured parser
    # Environment inputs: none; Stdout: none; Stderr: none; Exit classes: option map
    # Side effects: none; Idempotency: stable parser; Cleanup: none
    def _options(self) -> dict[str, str]:
        """Describe every accepted option from the configured parser actions.

        :returns: Option spellings mapped to their help descriptions.
        """
        return {
            option: action.help or ""
            for action in self._actions
            if action.help is not argparse.SUPPRESS
            for option in action.option_strings
        }

    # Summary: derive machine-readable CLI help; Arguments: configured parser
    # Environment inputs: none; Stdout: none; Stderr: none; Exit classes: help map
    # Side effects: none; Idempotency: stable parser; Cleanup: none
    def machine_help(self) -> dict[str, object]:
        """Expose the same accepted interface and example as human help.

        :returns: Summary, description, example, options, modes, and usage.
        """
        description = self.description or self.prog
        summary, _, detail = description.partition("\n")
        return {
            "summary": summary,
            "description": detail.strip(),
            "examples": [
                {
                    "purpose": "Inspect this command's machine-readable interface",
                    "command": f"{self.prog} --describe",
                }
            ],
            "options": self._options(),
            "output_modes": {"json": "status JSON on stdout"},
            "usage": self.format_usage().strip().removeprefix("usage: ").strip(),
        }

    # Summary: render ordered human help; Arguments: configured parser
    # Environment inputs: none; Stdout: none, returns text; Stderr: none
    # Exit classes: help text; Side effects: none; Idempotency: stable parser
    # Cleanup: none
    def format_help(self) -> str:
        """Render ordered sections with a purpose-labeled example.

        :returns: Complete human help text for accepted options and modes.
        """
        contract = self.machine_help()
        lines = ["Summary:", f"  {contract['summary']}", ""]
        if contract["description"]:
            lines.extend(["Description:", f"  {contract['description']}", ""])
        lines.extend(
            [
                "Examples:",
                "  # Inspect this command's machine-readable interface.",
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
            lines.append(
                fill(
                    f"{names}: {action.help or ''}",
                    width=88,
                    initial_indent="  ",
                    subsequent_indent="    ",
                )
            )
        lines.extend(
            [
                "",
                "Output modes:",
                "  json: status JSON on stdout",
                "",
                "Usage:",
                f"  {contract['usage']}",
                "",
            ]
        )
        return "\n".join(lines)


# Summary: add standard validation options; Arguments: parser to extend
# Environment inputs: none; Stdout: none; Stderr: none
# Exit classes: returns None; Side effects: mutates the supplied parser
# Idempotency: call once per parser; Cleanup: none
def add_options(parser: argparse.ArgumentParser) -> None:
    """Add common diagnostic and read-only options to a validation parser.

    :param parser: Argument parser to extend in place.
    """
    parser.add_argument(
        "--dry-run", action="store_true", help="inspect without changing state"
    )
    parser.add_argument(
        "--log-format",
        choices=("text", "json"),
        default="text",
        help="stderr and log-file diagnostic format (default: text)",
    )
    parser.add_argument(
        "--log-level",
        choices=("debug", "info", "warning", "error"),
        default="info",
        help="minimum diagnostic severity (default: info)",
    )
    parser.add_argument("--log-file", type=Path, help="append sanitized status log")
    parser.add_argument(
        "--run-id", default="", help="caller-selected log correlation ID"
    )
    parser.add_argument(
        "--describe",
        action=DescribeAction,
        nargs=0,
        help="print this command's machine-readable interface and exit",
    )


# Summary: emit safe validation result; Arguments: options and status map
# Environment inputs: UTC clock; Stdout: JSON; Stderr: compact status log
# Exit classes: returns None or raises on log write; Side effects: optional log append
# Idempotency: result repeats, log appends; Cleanup: log file closes
def emit(args: argparse.Namespace, result: dict) -> None:
    """Emit status JSON and an optional concise diagnostic event.

    :param args: Parsed logging and dry-run options.
    :param result: Validation status and problem details.
    :raises OSError: The selected log file cannot be appended.
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
