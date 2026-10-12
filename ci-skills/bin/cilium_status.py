#!/usr/bin/env python3
"""Aggregate Cilium agents and non-TTY health from ready DaemonSet Pods.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

import argparse

import _bootstrap  # noqa: F401
from lib.python.core.cli import execute, parser
from lib.python.core.collect import collect_cilium


# The parser is built by its own function so the declared interface can be
# compared with the real one without rendering help text: the epilog names
# several flags as prose, and a text scan cannot tell those apart from the
# options argparse actually accepts. See tests/test_catalog.py.
def build_parser() -> argparse.ArgumentParser:
    """Declare Cilium namespace, node, and search filters.

    :returns: Parser for the selected Cilium status read command.
    """
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
    """Parse Cilium filters and execute the aggregate status collector.

    :returns: Exit code from the shared command executor.
    """
    return execute(build_parser().parse_args(), collect_cilium)


if __name__ == "__main__":
    raise SystemExit(main())
