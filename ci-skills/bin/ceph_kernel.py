#!/usr/bin/env python3
"""Classify recent local kernel Ceph/RBD journal entries into actions.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

import _bootstrap  # noqa: F401
from lib.python.core.kubernetes.node_local_cli import parser, run


def main() -> int:
    """Parse node-local arguments and classify Ceph kernel journal entries.

    :returns: Exit code from the node-local command runner.
    """
    return run("ceph_kernel", parser("ceph_kernel").parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
