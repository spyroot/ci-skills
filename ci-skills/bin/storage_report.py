#!/usr/bin/env python3
"""Collect one correlated storage inventory from concurrent Kubernetes reads.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

import argparse

import _bootstrap  # noqa: F401
from lib.python.core.cli import execute, parser
from lib.python.core.collect import collect_storage


# The parser is built by its own function so the declared interface can be
# compared with the real one without rendering help text: the epilog names
# several flags as prose, and a text scan cannot tell those apart from the
# options argparse actually accepts. See tests/test_catalog.py.
def build_parser() -> argparse.ArgumentParser:
    """Declare namespace, node, storage-class, phase, and text filters.

    :returns: Parser for the correlated storage inventory command.
    """
    cli = parser("Report PVC, PV, node, workload, CSI, and attachment state.")
    cli.add_argument(
        "--namespace",
        default="all",
        metavar="NAME|all",
        help="filter PVC namespace (default: all)",
    )
    cli.add_argument("--node", metavar="NAME", help="filter claims used by this node")
    cli.add_argument("--storage-class", metavar="NAME", help="filter StorageClass name")
    cli.add_argument(
        "--phase",
        choices=["Pending", "Bound", "Lost", "Released", "Failed", "all"],
        default="all",
        help="filter PVC or PV phase (default: all)",
    )
    cli.add_argument("--search", metavar="TEXT", help="case-insensitive text filter")
    return cli


def main() -> int:
    """Parse storage filters and execute the correlated inventory collector.

    :returns: Exit code from the shared command executor.
    """
    return execute(build_parser().parse_args(), collect_storage)


if __name__ == "__main__":
    raise SystemExit(main())
