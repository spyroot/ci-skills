#!/usr/bin/env python3
"""Aggregate Cilium agents and non-TTY health from ready DaemonSet Pods."""

import argparse

import _bootstrap  # noqa: F401
from core.cli import execute, parser
from core.collect import collect_cilium


# The parser is built by its own function so the declared interface can be
# compared with the real one without rendering help text: the epilog names
# several flags as prose, and a text scan cannot tell those apart from the
# options argparse actually accepts. See tests/test_catalog.py.
def build_parser() -> argparse.ArgumentParser:
    """Return this command's parser: the universal tier plus its own options."""
    cli = parser("Report Cilium DaemonSet, operator, nodes, and agent health.")
    cli.add_argument(
        "--namespace",
        default="auto",
        metavar="NAME|auto",
        help="Cilium namespace (default: discover)",
    )
    cli.add_argument("--node", metavar="NAME", help="filter agent node")
    cli.add_argument("--search", metavar="TEXT", help="case-insensitive text filter")
    return cli


def main() -> int:
    return execute(build_parser().parse_args(), collect_cilium)


if __name__ == "__main__":
    raise SystemExit(main())
