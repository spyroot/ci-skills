#!/usr/bin/env python3
"""Plan, commit, and verify caller-selected GitLab repository files.

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
    """Expose repository commit inputs through the shared command catalogue.

    :returns: Parser for native file actions and shared plan/apply output controls.
    """
    return action_parser("gitlab_repository")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Execute the shared GitLab authentication, confirmation, and commit workflow.

    :param argv: Explicit command arguments, or None for process arguments.
    :returns: Shared report exit status for the planned or applied operation.
    """
    args = build_parser().parse_args(argv)
    return GitLabWorkflow.from_args(args).execute()


if __name__ == "__main__":
    raise SystemExit(main())
