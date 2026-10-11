#!/usr/bin/env python3
"""Read scoped GitLab runners and plan, apply, or verify record changes."""

import _bootstrap  # noqa: F401
from core.action import ExecutionContext, Executor
from core.gitlab_actions import (
    GitLabAction,
    GitLabAuth,
    action_parser,
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
        lambda: Executor(
            context=ExecutionContext.from_args(args),
            callbacks=[GitLabAuth(action=action), operation_callback(action)],
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
