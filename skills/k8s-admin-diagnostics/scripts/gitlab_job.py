#!/usr/bin/env python3
"""Read a selected GitLab CI job and its bounded trace."""

from core.cli import execute, parser
from core.collect import collect_gitlab_job


def main() -> int:
    cli = parser(
        "Report job, pipeline, runner, and bounded trace data from the selected GitLab host."
    )
    cli.add_argument(
        "--job-url",
        required=True,
        metavar="URL",
        help="full HTTPS URL for one GitLab job",
    )
    cli.add_argument("--search", metavar="TEXT", help="case-insensitive text filter")
    return execute(cli.parse_args(), collect_gitlab_job)


if __name__ == "__main__":
    raise SystemExit(main())
