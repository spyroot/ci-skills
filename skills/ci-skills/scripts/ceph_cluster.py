#!/usr/bin/env python3
"""Read Ceph health, OSDs, inactive PGs, and selected storage Pods."""

import argparse

from core.ceph_cluster import collect_ceph_cluster
from core.cli import execute, parser


# Summary: Define Ceph namespace, operator, Pod, and condition selectors.
# Arguments: none; Environment inputs: none; Stdout: none; Stderr: none.
# Exit classes: no process exit; Side effects: constructs a parser in memory.
# Idempotency: same options each call; Cleanup: none.
def build_parser() -> argparse.ArgumentParser:
    """Create the Ceph cluster CLI parser.

    :returns: Shared options plus Ceph namespace and result filters.
    """
    cli = parser("Report a selected Rook Ceph cluster and its OSD/monitor Pods.")
    cli.add_argument("--namespace", metavar="NAME", help="selected Ceph namespace")
    cli.add_argument(
        "--operator",
        default="rook-ceph-operator",
        metavar="NAME",
        help="operator deployment name (default: rook-ceph-operator)",
    )
    cli.add_argument(
        "--conf",
        metavar="PATH",
        help="Ceph config path inside operator (default: derived from namespace)",
    )
    cli.add_argument("--node", metavar="NAME", help="show Pods on this node")
    cli.add_argument(
        "--ready",
        choices=["all", "true", "false"],
        default="all",
        help="Pod Ready filter",
    )
    cli.add_argument("--condition", metavar="TYPE=STATUS", help="Pod condition filter")
    return cli


# Summary: Parse a Ceph request and delegate cluster reads and reporting.
# Arguments: none; Environment inputs: process CLI and selected Kubernetes target.
# Stdout: Ceph report; Stderr: diagnostics or usage errors.
# Exit classes: command status or argparse usage error; Side effects: cluster reads.
# Idempotency: report follows current Ceph state; Cleanup: delegated to runtime.
def main() -> int:
    """Run the selected Ceph health and Pod collector.

    :returns: Exit status from the shared collector command.
    """
    return execute(build_parser().parse_args(), collect_ceph_cluster)


if __name__ == "__main__":
    raise SystemExit(main())
