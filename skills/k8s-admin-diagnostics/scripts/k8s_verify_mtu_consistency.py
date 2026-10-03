#!/usr/bin/env python3
"""Plan or verify PCI Ethernet uplink MTUs on the selected Kubernetes nodes."""

import argparse

from core.cli import parser
from core.mtu_consistency import run


def build_parser() -> argparse.ArgumentParser:
    """Expose one named operation with explicit temporary-Pod authorization."""
    cli = parser("Compare selected nodes' physical PCI Ethernet IPv4 uplink MTUs.")
    cli.add_argument(
        "--node", metavar="NAME", help="one existing node; default all nodes"
    )
    cli.add_argument(
        "--apply",
        action="store_true",
        help="run confirmed oc debug Pods; default is a live-read plan",
    )
    cli.add_argument(
        "--confirm-plan",
        metavar="SHA256",
        help="exact plan_digest from the default dry-run",
    )
    cli.add_argument(
        "--timeout",
        type=int,
        default=90,
        metavar="SECONDS",
        help="per-node oc debug timeout, 10–300 seconds (default 90)",
    )
    cli.add_argument("--run-id", metavar="ID", help="optional log correlation ID")
    cli.add_argument(
        "--log-format",
        choices=("text", "json"),
        default="text",
        help="diagnostic log format on stderr (default text)",
    )
    cli.add_argument(
        "--log-level",
        choices=("debug", "info", "warning", "error"),
        default="info",
        help="minimum diagnostic level (default info)",
    )
    cli.add_argument(
        "--log-file", metavar="PATH", help="append diagnostics to a private local file"
    )
    cli.epilog += (
        " This command reads the selected node inventory by default and returns "
        "a plan_digest. --apply --confirm-plan SHA256 creates temporary oc debug "
        "Pods, reads host links, then verifies their cleanup. Example: "
        "%(prog)s --json; %(prog)s --apply --confirm-plan SHA256 --human"
    )
    return cli


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
