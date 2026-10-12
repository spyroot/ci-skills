#!/usr/bin/env python3
"""Run one toolbox or registry operation through the shared callback workflow.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from argparse import ArgumentParser

import _bootstrap  # noqa: F401
from lib.python.core.action import Executor
from lib.python.core.harbor.registry_mirror import HarborAuth, HarborPush, PushAction
from lib.python.core.toolbox_actions import ConfirmWorkflow, WorkflowAction, workflow_parser


def build_parser() -> ArgumentParser:
    """Return this operation's shared argument parser.

    :returns: Catalogue-backed command parser.
    """
    return workflow_parser("harbor_push")


def workflow(action: WorkflowAction) -> Executor:
    """Compose authentication, confirmation and the independent operation.

    :param action: Prepared operation inputs.
    :returns: The existing callback executor.
    """
    confirmation = ConfirmWorkflow(action=action)
    callbacks = [
        HarborAuth(
            target=action.target,
            repository=action.repository,
            actions=("pull", "push"),
            timeout=action.args.timeout,
        ),
        confirmation,
        HarborPush(action=PushAction(action.repository, action.args.tag, confirmation)),
    ]
    return Executor(context=action.context, callbacks=callbacks)


def main(argv: list[str] | None = None) -> int:
    """Run this operation and emit the shared report envelope.

    :param argv: Optional command arguments.
    :returns: Canonical operation exit code.
    """
    action = WorkflowAction(build_parser().parse_args(argv))
    return action.execute(lambda: workflow(action))


if __name__ == "__main__":
    raise SystemExit(main())
