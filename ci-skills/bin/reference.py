#!/usr/bin/env python3
"""Offer compact tool clusters and optional references to humans and agents.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

import argparse
import sys
from collections.abc import Sequence
from typing import Optional

import _bootstrap  # noqa: F401
from lib.python.core.catalog import describe, proposition
from lib.python.core.cli import MachineArgumentParser
from lib.python.core.logging import add_log_arguments, log_event
from lib.python.core.navigate import next_document, section_document
from lib.python.core.report import emit, render_value


def build_parser() -> argparse.ArgumentParser:
    """Declare the offline discovery interface.

    :returns: Parser for an optional cluster or installed command filename.
    """
    parser = MachineArgumentParser(
        report_kind="reference_next", help_contract=describe("reference.py")
    )
    parser.add_argument(
        "path", nargs="*", help="node, next [path ...], or get reference anchor"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--json",
        action="store_true",
        help="print versioned JSON; default for legacy node propositions",
    )
    modes.add_argument("--yaml", action="store_true", help="print versioned YAML")
    modes.add_argument(
        "--human",
        action="store_true",
        help="render next/get for a human (their default)",
    )
    parser.add_argument(
        "--page", type=int, default=1, help="one-based discovery or section page"
    )
    parser.add_argument(
        "--search", default="", help="literal search within one selected reference"
    )
    tiers = parser.add_mutually_exclusive_group()
    tiers.add_argument(
        "--card", action="store_true", help="read a declared reference card"
    )
    tiers.add_argument("--part", default="", help="read one declared reference part")
    parser.add_argument(
        "--describe", action="store_true", help="describe the discovery interface"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show the same offline proposition without writing a log file",
    )
    add_log_arguments(parser)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Expand one node without loading credentials or fetching references.

    :param argv: Optional command arguments.
    :returns: Zero on success; two for an unknown node.
    :raises SystemExit: If argparse handles help or rejects invalid arguments.
    :raises RuntimeError: If YAML output is requested without the YAML dependency.
    :raises OSError: If the requested log or process output cannot be written.
    """  # noqa: DOC502 - Argument parsing, YAML import, and output writes can fail.
    args = build_parser().parse_args(argv)
    progressive = (
        bool(args.path and args.path[0] in ("next", "get")) and not args.describe
    )
    try:
        if args.describe:
            data = describe("reference.py")
        elif progressive and args.path[0] == "next":
            if args.card or args.part:
                build_parser().error("--card and --part require get")
            data = next_document(args.path[1:], page=args.page, search=args.search)
        elif progressive:
            if len(args.path) != 3 or args.search:
                build_parser().error(
                    "get requires one reference and anchor; --search belongs to next"
                )
            tier = "card" if args.card else f"part:{args.part}" if args.part else ""
            data = section_document(
                args.path[1], args.path[2], page=args.page, tier=tier
            )
        else:
            if (
                len(args.path) > 1
                or args.page != 1
                or args.search
                or args.card
                or args.part
                or args.human
            ):
                build_parser().error("progressive options require next or get")
            data = proposition(args.path[0] if args.path else None)
    except ValueError:
        print("path_unknown: choose a node from reference.py", file=sys.stderr)
        return 2
    mode = "yaml" if args.yaml else "json" if args.json or not progressive else "human"
    if args.human:
        mode = "human"
    rendered = (
        emit(data, mode)
        if progressive
        else render_value(data, "yaml" if args.yaml else "json")
    )
    log_event(
        args,
        str(data.get("kind", "reference_next")),
        "complete",
        "BLOCKED" if "error" in data else "PASS",
        persist=not (args.dry_run or args.describe),
    )
    sys.stdout.write(rendered)
    return 2 if "error" in data else 0


if __name__ == "__main__":
    raise SystemExit(main())
