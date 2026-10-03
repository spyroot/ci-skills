#!/usr/bin/env python3
"""Verify explicit GitHub, GitLab, and Kubernetes administrator access."""

import argparse

from core.cli import execute, parser


# The parser is built by its own function so the declared interface can be
# compared with the real one without rendering help text: the epilog names
# several flags as prose, and a text scan cannot tell those apart from the
# options argparse actually accepts. See tests/test_catalog.py.
def build_parser() -> argparse.ArgumentParser:
    """Return this command's parser: the universal tier plus its own options."""
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
        "--receipt-out",
        metavar="PATH",
        help="also write the committable receipt, with host paths digested",
    )
    return cli


def main() -> int:
    return execute(build_parser().parse_args(), live_checks=True)


if __name__ == "__main__":
    raise SystemExit(main())
