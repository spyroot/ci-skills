#!/usr/bin/env python3
"""Read scoped GitLab runners and plan, apply, or verify record changes.

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
    """Return this command's parser for callers and contract checks.

    :returns: Parser with the runner command's declared options.
    """
    return action_parser("gitlab_runner")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Compose this command's authentication and selected operation.

    :param argv: Command arguments, or None to read the process argument vector.
    :returns: Existing report exit code.
    """
    args = build_parser().parse_args(argv)
    return GitLabWorkflow.from_args(args).execute()


if __name__ == "__main__":
    raise SystemExit(main())
