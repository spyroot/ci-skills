#!/usr/bin/env python3
"""Classify recent local kernel Ceph/RBD journal entries into actions."""

import _bootstrap  # noqa: F401
from core.node_local_cli import parser, run


def main() -> int:
    return run("ceph_kernel", parser("ceph_kernel").parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
