#!/usr/bin/env python3
"""Read a selected GitLab CI job and its bounded trace."""

import argparse

from core.cli import execute_gitlab_job, parser


# The parser is built by its own function so the declared interface can be
# compared with the real one without rendering help text: the epilog names
# several flags as prose, and a text scan cannot tell those apart from the
# options argparse actually accepts. See tests/test_catalog.py.
# Summary: Define the job URL and optional trace filter on the shared CLI.
# Arguments: none; Environment inputs: none; Stdout: none; Stderr: none.
# Exit classes: no process exit; Side effects: constructs a parser in memory.
# Idempotency: same option definitions each call; Cleanup: none.
def build_parser() -> argparse.ArgumentParser:
    """Create the GitLab job CLI parser.

    :returns: Shared options plus one job URL and search filter.
    """
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


# Summary: Parse a job request and delegate its live read and report.
# Arguments: none; Environment inputs: process CLI and selected GitLab target.
# Stdout: job report; Stderr: diagnostics or usage errors.
# Exit classes: command status or argparse usage error; Side effects: GitLab reads.
# Idempotency: report follows current job state; Cleanup: delegated to runtime.
def main() -> int:
    """Run the selected job reader through the shared GitLab CLI.

    :returns: Exit status from the job read and report.
    """
    return execute_gitlab_job(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
