#!/usr/bin/env python3
"""Plan, create, and verify GitLab merge requests and milestone assignments.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from argparse import ArgumentParser
from collections.abc import Sequence
from typing import Optional

import _bootstrap  # noqa: F401
from lib.python.core.gitlab.gitlab_action import GitLabWorkflow
from lib.python.core.gitlab.gitlab_actions import action_parser


def build_parser() -> ArgumentParser:
    """Declare the GitLab MR interface from the shared command catalogue.

    :returns: Parser accepting MR actions and common operation options.
    """
    return action_parser("gitlab_merge_request")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Compose authentication and the selected MR operation.

    :param argv: Command arguments, or None to read the process argument vector.
    :returns: Existing report exit code.
    """
    args = build_parser().parse_args(argv)
    return GitLabWorkflow.from_args(args).execute()


if __name__ == "__main__":
    raise SystemExit(main())
