#!/usr/bin/env python3
"""Offer compact tool clusters and optional references to humans and agents.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

import argparse
import json
import sys

import _bootstrap  # noqa: F401
from core.catalog import describe, proposition
from core.cli import MachineArgumentParser, add_log_arguments, log_event


def build_parser() -> argparse.ArgumentParser:
    """Declare the offline discovery interface.

    :returns: Parser for an optional cluster or installed command filename.
    """
    parser = MachineArgumentParser(
        report_kind="reference_next", help_contract=describe("reference.py")
    )
    parser.add_argument(
        "node", nargs="?", help="cluster or installed filename; omit for root"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--json", action="store_true", help="print versioned JSON (default)"
    )
    modes.add_argument("--yaml", action="store_true", help="print versioned YAML")
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


def main(argv: list[str] | None = None) -> int:
    """Expand one node without loading credentials or fetching references.

    :param argv: Optional command arguments.
    :returns: Zero on success; two for an unknown node.
    """
    args = build_parser().parse_args(argv)
    try:
        data = describe("reference.py") if args.describe else proposition(args.node)
    except ValueError:
        print("path_unknown: choose a node from reference.py", file=sys.stderr)
        return 2
    if args.yaml:
        import yaml

        rendered = yaml.safe_dump(data, sort_keys=True)
    else:
        rendered = json.dumps(data, indent=2, sort_keys=True) + "\n"
    log_event(
        args,
        "reference_next",
        "complete",
        "PASS",
        persist=not (args.dry_run or args.describe),
    )
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
