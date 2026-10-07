#!/usr/bin/env python3
"""Read a selected GitLab CI job and its bounded trace."""

import argparse

import _bootstrap  # noqa: F401
from core.cli import execute_gitlab_job, parser


# The parser is built by its own function so the declared interface can be
# compared with the real one without rendering help text: the epilog names
# several flags as prose, and a text scan cannot tell those apart from the
# options argparse actually accepts. See tests/test_catalog.py.
def build_parser() -> argparse.ArgumentParser:
    """Return this command's parser: the universal tier plus its own options."""
    cli = parser(
        "Report job, pipeline, runner, and bounded trace data from the selected GitLab host.",
        kind="gitlab_job",
    )
    cli.add_argument(
        "--job-url",
        metavar="URL",
        help="full HTTPS URL for one GitLab job (required unless --describe)",
    )
    cli.add_argument("--search", metavar="TEXT", help="case-insensitive text filter")
    return cli


def main() -> int:
    return execute_gitlab_job(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
