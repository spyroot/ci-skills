#!/usr/bin/env python3
"""Read a bounded, time-ordered Kubernetes event trace."""

import argparse

from core.cli import execute, parser
from core.collect import collect_events


# The parser is built by its own function so the declared interface can be
# compared with the real one without rendering help text: the epilog names
# several flags as prose, and a text scan cannot tell those apart from the
# options argparse actually accepts. See tests/test_catalog.py.
def build_parser() -> argparse.ArgumentParser:
    """Return this command's parser: the universal tier plus its own options."""
    cli = parser("Trace Kubernetes events with native time and object filters.")
    cli.add_argument(
        "--from",
        dest="from_time",
        metavar="RFC3339",
        help="inclusive start (default: one hour before end)",
    )
    cli.add_argument(
        "--to", dest="to_time", metavar="RFC3339", help="inclusive end (default: now)"
    )
    cli.add_argument(
        "--namespace",
        default="all",
        metavar="NAME|all",
        help="filter namespace (default: all)",
    )
    cli.add_argument(
        "--last",
        metavar="DURATION",
        help="relative window ending now, e.g. 5m, 90s, 2h, 7d; not with --from/--to",
    )
    cli.add_argument("--kind", metavar="KIND", help="filter involved object kind")
    cli.add_argument("--object", metavar="NAME", help="filter involved object name")
    cli.add_argument("--reason", metavar="TEXT", help="case-insensitive reason filter")
    cli.add_argument("--search", metavar="TEXT", help="case-insensitive text filter")
    return cli


def main() -> int:
    return execute(build_parser().parse_args(), collect_events)


if __name__ == "__main__":
    raise SystemExit(main())
