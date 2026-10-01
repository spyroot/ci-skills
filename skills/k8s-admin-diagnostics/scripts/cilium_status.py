#!/usr/bin/env python3
"""Aggregate Cilium agents and non-TTY health from ready DaemonSet Pods."""

from core.cli import execute, parser
from core.collect import collect_cilium


def main() -> int:
    cli = parser("Report Cilium DaemonSet, operator, nodes, and agent health.")
    cli.add_argument(
        "--namespace",
        default="auto",
        metavar="NAME|auto",
        help="Cilium namespace (default: discover)",
    )
    cli.add_argument("--node", metavar="NAME", help="filter agent node")
    cli.add_argument("--search", metavar="TEXT", help="case-insensitive text filter")
    return execute(cli.parse_args(), collect_cilium)


if __name__ == "__main__":
    raise SystemExit(main())
