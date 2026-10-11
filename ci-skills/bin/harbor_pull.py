#!/usr/bin/env python3
"""Run one toolbox or registry operation through the shared callback workflow.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from argparse import ArgumentParser

import _bootstrap  # noqa: F401
from core.action import Executor
from core.kubernetes import K8SAuth
from core.registry_mirror import HarborAuth
from core.runner import PullImage
from core.toolbox_actions import ConfirmWorkflow, WorkflowAction, workflow_parser


def build_parser() -> ArgumentParser:
    """Return this operation's shared argument parser.

    :returns: Catalogue-backed command parser.
    """
    return workflow_parser("harbor_pull")


def workflow(action: WorkflowAction) -> Executor:
    """Compose authentication, confirmation and the independent operation.

    :param action: Prepared operation inputs.
    :returns: The existing callback executor.
    """
    auth = HarborAuth(
        target=action.target,
        repository=action.repository,
        actions=("pull",),
        timeout=action.args.timeout,
    )
    callbacks = [
        K8SAuth(target=action.target, timeout=action.args.timeout),
        auth,
        ConfirmWorkflow(action=action),
        PullImage(action=action.runner, image=action.args.image, dockerconfig=auth),
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
