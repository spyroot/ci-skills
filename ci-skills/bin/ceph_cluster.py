#!/usr/bin/env python3
"""Read Ceph health, OSDs, inactive PGs, and selected storage Pods.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

import argparse

import _bootstrap  # noqa: F401
from lib.python.core.cli import execute, parser
from lib.python.core.kubernetes.ceph_cluster import collect_ceph_cluster


def build_parser() -> argparse.ArgumentParser:
    """Declare Ceph namespace, operator, and Pod-filter options.

    :returns: Parser for the selected cluster read command.
    """
    cli = parser("Report a selected Rook Ceph cluster and its OSD/monitor Pods.")
    cli.add_argument("--namespace", metavar="NAME", help="selected Ceph namespace")
    cli.add_argument(
        "--operator",
        metavar="NAME",
        help="operator deployment name (default: discover in selected namespace)",
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


def main() -> int:
    """Parse Ceph arguments and collect cluster and storage Pod status.

    :returns: Exit code from the shared command executor.
    """
    return execute(build_parser().parse_args(), collect_ceph_cluster)


if __name__ == "__main__":
    raise SystemExit(main())
