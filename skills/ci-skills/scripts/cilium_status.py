#!/usr/bin/env python3
"""Aggregate Cilium agents and non-TTY health from ready DaemonSet Pods."""

import argparse

from core.cli import execute, parser
from core.collect import collect_cilium


# The parser is built by its own function so the declared interface can be
# compared with the real one without rendering help text: the epilog names
# several flags as prose, and a text scan cannot tell those apart from the
# options argparse actually accepts. See tests/test_catalog.py.
# Summary: Define Cilium namespace, node, and search filters.
# Arguments: none; Environment inputs: none; Stdout: none; Stderr: none.
# Exit classes: no process exit; Side effects: constructs a parser in memory.
# Idempotency: same options each call; Cleanup: none.
def build_parser() -> argparse.ArgumentParser:
    """Create the Cilium cluster CLI parser.

    :returns: Shared options plus namespace, node, and text filters.
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


# Summary: Parse a Cilium request and delegate DaemonSet and health reads.
# Arguments: none; Environment inputs: process CLI and selected Kubernetes target.
# Stdout: Cilium report; Stderr: diagnostics or usage errors.
# Exit classes: command status or argparse usage error; Side effects: cluster and Pod reads.
# Idempotency: report follows current agent state; Cleanup: delegated to runtime.
def main() -> int:
    """Run the selected Cilium collector.

    :returns: Exit status from the shared collector command.
    """
    return execute(build_parser().parse_args(), collect_cilium)


if __name__ == "__main__":
    raise SystemExit(main())
