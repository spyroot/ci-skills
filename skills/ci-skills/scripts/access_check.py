#!/usr/bin/env python3
"""Verify explicit GitHub, GitLab, and Kubernetes administrator access."""

import argparse

from core.cli import execute, parser


# The parser is built by its own function so the declared interface can be
# compared with the real one without rendering help text: the epilog names
# several flags as prose, and a text scan cannot tell those apart from the
# options argparse actually accepts. See tests/test_catalog.py.
# Summary: Define publication, job, Ceph, and receipt options on the shared CLI.
# Arguments: none; Environment inputs: none; Stdout: none; Stderr: none.
# Exit classes: no process exit; Side effects: constructs a parser in memory.
# Idempotency: same options each call; Cleanup: none.
def build_parser() -> argparse.ArgumentParser:
    """Create the three-authority access CLI parser.

    :returns: Shared options and optional publication and live check selectors.
    """
    cli = parser(
        "Check every selected authority; dry run never counts as access.",
    )
    cli.add_argument(
        "--publication",
        action="store_true",
        help="also require GitHub repository administration before configuring checks",
    )
    cli.add_argument(
        "--job-url",
        metavar="URL",
        help="also verify one exact GitLab job, pipeline, runner, and trace",
    )
    cli.add_argument(
        "--ceph-namespace",
        metavar="NAME",
        help="also prove Ceph health, OSD, PG and Pod reads in this namespace",
    )
    cli.add_argument(
        "--receipt-out",
        metavar="PATH",
        help="also write the committable receipt, with host paths digested",
    )
    return cli


# Summary: Parse the access request and run the shared live receipt path.
# Arguments: none; Environment inputs: CLI, selected target, and credentials.
# Stdout: access report; Stderr: diagnostics or usage errors.
# Exit classes: command status or argparse usage error
# Side effects: live API reads and optional receipt file.
# Idempotency: report follows current authority state; Cleanup: delegated to runtime.
def main() -> int:
    """Run the requested GitHub, GitLab, and Kubernetes access checks.

    :returns: Exit status from the shared access command.
    """
    return execute(build_parser().parse_args(), live_checks=True)


if __name__ == "__main__":
    raise SystemExit(main())
