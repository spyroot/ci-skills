#!/usr/bin/env python3
"""Read one GitLab job or list bounded jobs from the selected project."""

import argparse

import _bootstrap  # noqa: F401
from core.cli import execute_gitlab_job, parser


def build_parser() -> argparse.ArgumentParser:
    """Return the declared GitLab job interface.

    :returns: Parser for the existing get route and the bounded list route.
    """
    cli = parser(
        "Get one GitLab job or list bounded jobs from one selected project.",
        kind="gitlab_job",
    )
    cli.add_argument("action", nargs="?", choices=("get", "list"), default="get")
    cli.add_argument(
        "--job-url",
        metavar="URL",
        help="full HTTPS URL for get (required unless --describe)",
    )
    cli.add_argument("--project", metavar="PROJECT", help="exact project for list")
    cli.add_argument("--search", metavar="TEXT", help="case-insensitive text filter")
    cli.add_argument(
        "--status",
        action="append",
        choices=("failed", "success", "running", "pending", "canceled", "stuck"),
        help="list status; repeat to select several",
    )
    cli.add_argument("--pipeline-id", type=int, help="list jobs of one pipeline")
    cli.add_argument("--ref", help="list jobs on this exact branch or tag")
    cli.add_argument("--last", help="list window ending now, such as 2h or 7d")
    cli.add_argument("--from", dest="from_time", help="inclusive RFC3339 start")
    cli.add_argument("--to", dest="to_time", help="inclusive RFC3339 end")
    cli.add_argument("--name-glob", help="shell-style job name pattern")
    cli.add_argument(
        "--limit", type=int, default=50, help="maximum list records (1–500)"
    )
    cli.add_argument(
        "--stuck-after",
        type=int,
        default=600,
        metavar="SECONDS",
        help="minimum age of a pending or created job (default: 600)",
    )
    return cli


def main() -> int:
    return execute_gitlab_job(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
