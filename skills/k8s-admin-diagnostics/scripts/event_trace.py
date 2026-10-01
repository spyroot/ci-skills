#!/usr/bin/env python3
"""Read a bounded, time-ordered Kubernetes event trace."""

from core.cli import execute, parser
from core.collect import collect_events


def main() -> int:
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
    return execute(cli.parse_args(), collect_events)


if __name__ == "__main__":
    raise SystemExit(main())
