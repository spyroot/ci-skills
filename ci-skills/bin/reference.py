#!/usr/bin/env python3
"""Offer compact tool clusters and optional references to humans and agents."""

import argparse
import json
import sys

import _bootstrap  # noqa: F401
import yaml
from core.catalog import describe, proposition


def build_parser() -> argparse.ArgumentParser:
    """Declare the offline discovery interface.

    :returns: Parser for an optional cluster or installed command filename.
    """
    parser = argparse.ArgumentParser(description=__doc__)
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
    sys.stdout.write(
        yaml.safe_dump(data, sort_keys=True)
        if args.yaml
        else json.dumps(data, indent=2, sort_keys=True) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
