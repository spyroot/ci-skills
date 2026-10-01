#!/usr/bin/env python3
"""Read Ceph health, OSDs, inactive PGs, and selected storage Pods."""

from core.ceph_cluster import collect_ceph_cluster
from core.cli import execute, parser


def main() -> int:
    cli = parser("Report a selected Rook Ceph cluster and its OSD/monitor Pods.")
    cli.add_argument(
        "--namespace", required=True, metavar="NAME", help="selected Ceph namespace"
    )
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
    args = cli.parse_args()
    return execute(args, collect_ceph_cluster)


if __name__ == "__main__":
    raise SystemExit(main())
