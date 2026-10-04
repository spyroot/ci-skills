#!/usr/bin/env python3
"""Plan or verify PCI Ethernet uplink MTUs on the selected Kubernetes nodes."""

import argparse

from core.cli import parser
from core.mtu_consistency import run


# Summary: Define node selection and guarded MTU debug-Pod options.
# Arguments: none; Environment inputs: none; Stdout: none; Stderr: none.
# Exit classes: no process exit; Side effects: constructs a parser in memory.
# Idempotency: same options each call; Cleanup: none.
def build_parser() -> argparse.ArgumentParser:
    """Create the OpenShift physical MTU CLI parser.

    :returns: Shared options plus node, apply, plan, and timeout fields.
    """
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
    cli.epilog += (
        " This command reads the selected node inventory by default and returns "
        "a plan_digest. --apply --confirm-plan SHA256 creates temporary oc debug "
        "Pods, reads host links, then verifies their cleanup. Example: "
        "%(prog)s --json; %(prog)s --apply --confirm-plan SHA256 --human"
    )
    return cli


# Summary: Plan MTU reads or run confirmed debug Pods with cleanup.
# Arguments: none; Environment inputs: process CLI and selected Kubernetes target.
# Stdout: plan or MTU report; Stderr: diagnostics or usage errors.
# Exit classes: command status or argparse usage error
# Side effects: node reads and optional debug Pods.
# Idempotency: plan is read-only and apply verifies cleanup; Cleanup: delegated to MTU runtime.
def main() -> int:
    """Run the MTU plan or exact confirmed collection.

    :returns: Exit status from the MTU runtime.
    """
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
