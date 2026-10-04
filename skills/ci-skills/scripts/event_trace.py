#!/usr/bin/env python3
"""Read a bounded, time-ordered Kubernetes event trace."""

import argparse

from core.cli import execute, parser
from core.collect import collect_events


# The parser is built by its own function so the declared interface can be
# compared with the real one without rendering help text: the epilog names
# several flags as prose, and a text scan cannot tell those apart from the
# options argparse actually accepts. See tests/test_catalog.py.
# Summary: Define event window, object, namespace, and text filters.
# Arguments: none; Environment inputs: none; Stdout: none; Stderr: none.
# Exit classes: no process exit; Side effects: constructs a parser in memory.
# Idempotency: same options each call; Cleanup: none.
def build_parser() -> argparse.ArgumentParser:
    """Create the Kubernetes event CLI parser.

    :returns: Shared options plus time and object filters.
    """
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


# Summary: Parse an event request and delegate the live event read.
# Arguments: none; Environment inputs: process CLI and selected Kubernetes target.
# Stdout: event report; Stderr: diagnostics or usage errors.
# Exit classes: command status or argparse usage error; Side effects: cluster event reads.
# Idempotency: report follows live events and selected time window; Cleanup: delegated to runtime.
def main() -> int:
    """Run the bounded Kubernetes event collector.

    :returns: Exit status from the shared collector command.
    """
    return execute(build_parser().parse_args(), collect_events)


if __name__ == "__main__":
    raise SystemExit(main())
