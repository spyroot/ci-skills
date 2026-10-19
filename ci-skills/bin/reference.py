#!/usr/bin/env python3
"""Show what an agent can run and read at one node, or read one reference.

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
from lib.python.core.cli import MachineArgumentParser, output_mode
from lib.python.core.logging import add_log_arguments, log_event
from lib.python.core.navigate_common import NavigationError, error_document
from lib.python.core.navigate_references import reference_document, section_document
from lib.python.core.report import emit, render_value


def build_parser() -> argparse.ArgumentParser:
    """Declare the offline discovery interface.

    :returns: Parser for a node, or ``get`` with a reference and optional anchor.
    """
    parser = MachineArgumentParser(
        report_kind="reference_next", help_contract=describe("reference.py")
    )
    parser.add_argument(
        "path", nargs="*", help="a cluster or command node, or get REFERENCE [ANCHOR]"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--json", action="store_true", help="compact JSON; default when piped"
    )
    modes.add_argument("--yaml", action="store_true", help="print versioned YAML")
    modes.add_argument(
        "--human", action="store_true", help="run and read lists; default on a terminal"
    )
    parser.add_argument(
        "--page", type=int, default=1, help="one-based entry or section page for get"
    )
    parser.add_argument(
        "--search", default="", help="literal search within one reference, with get"
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
        help="show the same offline answer without writing a log file",
    )
    add_log_arguments(parser)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Answer one node or one reference without credentials or network access.

    :param argv: Optional command arguments.
    :returns: Zero on success; two for a lookup failure.
    :raises SystemExit: If argparse handles help or rejects invalid arguments.
    :raises RuntimeError: If YAML output is requested without the YAML dependency.
    :raises OSError: If the requested log or process output cannot be written.
    """  # noqa: DOC502,DOC503 - Lookup errors are caught; parser/output errors propagate.
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.describe:
        contract = describe("reference.py")
        sys.stdout.write(render_value(contract, "yaml" if args.yaml else "json"))
        log_event(args, "reference_next", "complete", "PASS", persist=False)
        return 0
    reading = bool(args.path) and args.path[0] == "get"
    tier = "card" if args.card else f"part:{args.part}" if args.part else ""
    if not reading and (len(args.path) > 1 or args.page != 1 or args.search or tier):
        parser.error("--page, --search, --card and --part apply to get")
    try:
        if reading and len(args.path) == 2:
            if tier:
                raise NavigationError("tier_unavailable", tier)
            data = reference_document(args.path[1], page=args.page, search=args.search)
        elif reading and len(args.path) == 3:
            if args.search:
                raise NavigationError("path_unknown", args.search)
            data = section_document(
                args.path[1], args.path[2], page=args.page, tier=tier
            )
        elif reading:
            raise NavigationError("path_unknown", " ".join(args.path))
        else:
            try:
                data = proposition(args.path[0] if args.path else None)
            except ValueError:
                raise NavigationError("path_unknown", args.path[0]) from None
    except NavigationError as exc:
        data = error_document(exc)
    rendered = emit(data, output_mode(args), discovery=True)
    log_event(
        args,
        "reference_next",
        "complete",
        "BLOCKED" if "error" in data else "PASS",
        persist=not args.dry_run,
    )
    sys.stdout.write(rendered)
    return 2 if "error" in data else 0


if __name__ == "__main__":
    raise SystemExit(main())
