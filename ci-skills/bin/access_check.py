#!/usr/bin/env python3
"""Verify explicit GitHub, GitLab, and Kubernetes administrator access.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

import argparse

import _bootstrap  # noqa: F401
from lib.python.core.cli import execute_access, parser


# The parser is built by its own function so the declared interface can be
# compared with the real one without rendering help text: the epilog names
# several flags as prose, and a text scan cannot tell those apart from the
# options argparse actually accepts. See tests/test_catalog.py.
def build_parser() -> argparse.ArgumentParser:
    """Declare access-check options in addition to the common command tier.

    :returns: Parser for publication, job, Ceph, and receipt selections.
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


def main() -> int:
    """Parse access-check arguments and execute selected live checks.

    :returns: Exit code from the shared command executor.
    """
    return execute_access(build_parser().parse_args(), live_checks=True)


if __name__ == "__main__":
    raise SystemExit(main())
