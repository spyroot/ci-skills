#!/usr/bin/env python3
"""Read scoped GitLab runners and plan, apply, or verify record changes."""

import _bootstrap  # noqa: F401
from lib.python.core.abstract.callbacks import Callbacks
from lib.python.core.abstract.skill_exector import WorkflowExecutor
from lib.python.core.gitlab.gitlab_actions import action_parser
from lib.python.core.gitlab.gitlab_action import (
    GitLabAction,
    GitLabAuth,
    operation_callback,
)


def build_parser():
    """Return this command's parser for callers and contract checks.

    :returns: Parser with the runner command's declared options.
    """
    return action_parser("gitlab_runner")


def main(argv: list[str] | None = None) -> int:
    """Compose this command's authentication and selected operation.

    :param argv: Optional command arguments.
    :returns: Existing report exit code.
    """
    args = build_parser().parse_args(argv)
    action = GitLabAction.from_args(args)
    return action.execute(
        lambda: WorkflowExecutor(
            context=action,
            callbacks=Callbacks([GitLabAuth(action=action), operation_callback(action)]),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
