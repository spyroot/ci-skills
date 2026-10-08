#!/usr/bin/env python3
"""Plan, apply, and verify GitLab runner assignment, creation, or tagging."""

import _bootstrap  # noqa: F401
from core.gitlab_actions import action_parser, run_action_cli


def build_parser():
    """Return this command's parser for callers and contract checks.

    :returns: Parser with the runner command's declared options.
    """
    return action_parser("gitlab_runner")


if __name__ == "__main__":
    raise SystemExit(run_action_cli("gitlab_runner"))
