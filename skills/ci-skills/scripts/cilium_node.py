#!/usr/bin/env python3
"""Read Cilium daemon and health JSON from the local CRI agent container."""

from core.node_local_cli import parser, run


# Summary: Read Cilium daemon and health from the selected existing agent Pod.
# Arguments: none; Environment inputs: process CLI and selected Kubernetes target.
# Stdout: node Cilium report; Stderr: diagnostics or usage errors.
# Exit classes: command status or argparse usage error; Side effects: existing Pod exec reads.
# Idempotency: report follows current agent health; Cleanup: no Pod created.
def main() -> int:
    """Run the selected node's Cilium diagnostics.

    :returns: Exit status from the node-local reader.
    """
    return run("cilium_node", parser("cilium_node").parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
