#!/usr/bin/env python3
"""Plan, apply, and verify a selected GitLab wiki page."""

import _bootstrap  # noqa: F401
from core.action import ExecutionContext, Executor, emit_result
from core.gitlab_actions import (
    GitLabAction,
    GitLabAuth,
    action_parser,
    operation_callback,
)


def build_parser():
    """Return this command's declared parser for callers and contract checks."""
    return action_parser("gitlab_wiki")


def main(argv: list[str] | None = None) -> int:
    """Compose this command's authentication and selected operation.

    :param argv: Optional command arguments.
    :returns: Existing report exit code.
    """
    args = build_parser().parse_args(argv)
    action = GitLabAction.from_args(args)
    try:
        result = Executor(
            context=ExecutionContext.from_args(args),
            callbacks=[GitLabAuth(action=action), operation_callback(action)],
        ).run()
        return emit_result(result, args)
    except Exception as exc:  # noqa: BLE001 - structured command boundary
        return action.failure(exc)


if __name__ == "__main__":
    raise SystemExit(main())
