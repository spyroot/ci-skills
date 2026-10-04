#!/usr/bin/env python3
"""Classify recent local kernel Ceph/RBD journal entries into actions."""

from core.node_local_cli import parser, run


# Summary: Read recent host Ceph/RBD journal entries through a selected Pod.
# Arguments: none; Environment inputs: process CLI and selected Kubernetes target.
# Stdout: classified kernel report; Stderr: diagnostics or usage errors.
# Exit classes: command status or argparse usage error; Side effects: existing Pod exec read.
# Idempotency: report follows current journal window; Cleanup: no Pod created.
def main() -> int:
    """Run the selected node's Ceph kernel diagnostic.

    :returns: Exit status from the node-local reader.
    """
    return run("ceph_kernel", parser("ceph_kernel").parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
