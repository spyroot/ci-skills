#!/usr/bin/env python3
"""Read Cilium daemon and health JSON from the local CRI agent container.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

import _bootstrap  # noqa: F401
from lib.python.core.kubernetes.node_local_cli import parser, run


def main() -> int:
    """Parse node-local arguments and read the selected Cilium agent.

    :returns: Exit code from the node-local command runner.
    """
    return run("cilium_node", parser("cilium_node").parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
