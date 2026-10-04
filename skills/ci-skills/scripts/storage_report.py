#!/usr/bin/env python3
"""Collect one correlated storage inventory from concurrent Kubernetes reads."""

import argparse

from core.cli import execute, parser
from core.collect import collect_storage


# The parser is built by its own function so the declared interface can be
# compared with the real one without rendering help text: the epilog names
# several flags as prose, and a text scan cannot tell those apart from the
# options argparse actually accepts. See tests/test_catalog.py.
# Summary: Define storage namespace, node, class, phase, and text filters.
# Arguments: none; Environment inputs: none; Stdout: none; Stderr: none.
# Exit classes: no process exit; Side effects: constructs a parser in memory.
# Idempotency: same options each call; Cleanup: none.
def build_parser() -> argparse.ArgumentParser:
    """Create the Kubernetes storage CLI parser.

    :returns: Shared options plus storage inventory filters.
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


# Summary: Parse a storage request and delegate the correlated cluster reads.
# Arguments: none; Environment inputs: process CLI and selected Kubernetes target.
# Stdout: storage report; Stderr: diagnostics or usage errors.
# Exit classes: command status or argparse usage error; Side effects: cluster inventory reads.
# Idempotency: report follows current storage state; Cleanup: delegated to runtime.
def main() -> int:
    """Run the correlated Kubernetes storage collector.

    :returns: Exit status from the shared collector command.
    """
    return execute(build_parser().parse_args(), collect_storage)


if __name__ == "__main__":
    raise SystemExit(main())
