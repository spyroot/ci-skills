#!/usr/bin/env python3
"""Plan, apply, and verify an exact GitLab bug issue.

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
    """Declare the GitLab issue action interface from the shared catalogue.

    :returns: Parser accepting issue operation and common command options.
    """
    return action_parser("gitlab_issue")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Compose this command's authentication and selected operation.

    :param argv: Command arguments, or None to read the process argument vector.
    :returns: Existing report exit code.
    """
    args = build_parser().parse_args(argv)
    return GitLabWorkflow.from_args(args).execute()


if __name__ == "__main__":
    raise SystemExit(main())
