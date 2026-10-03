#!/usr/bin/env python3
"""Read Cilium daemon and health JSON from the local CRI agent container."""

from core.node_local_cli import parser, run


def main() -> int:
    return run("cilium_node", parser("cilium_node").parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
