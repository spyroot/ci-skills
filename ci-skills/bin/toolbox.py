#!/usr/bin/env python3
"""Run one toolbox or registry operation through the shared callback workflow.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from argparse import ArgumentParser

import _bootstrap  # noqa: F401
from lib.python.core.action import Executor
from lib.python.core.kubernetes.kubernetes import K8SAuth
from lib.python.core.harbor.registry_mirror import HarborAuth
from lib.python.core.toolbox_actions import (
    ConfirmWorkflow,
    DeleteToolbox,
    EnsureNamespace,
    HarborBuild,
    WorkflowAction,
    workflow_parser,
)


def build_parser() -> ArgumentParser:
    """Return this operation's shared argument parser.

    :returns: Catalogue-backed command parser.
    """
    return workflow_parser("toolbox")


def workflow(action: WorkflowAction) -> Executor:
    """Compose authentication, confirmation and the independent operation.

    :param action: Prepared operation inputs.
    :returns: The existing callback executor.
    """
    auth = HarborAuth(
        target=action.target,
        repository=action.repository,
        actions=("pull", "delete")
        if action.args.action == "delete"
        else ("pull", "push"),
        timeout=action.args.timeout,
    )
    callbacks = [
        K8SAuth(target=action.target, timeout=action.args.timeout),
        auth,
        ConfirmWorkflow(action=action),
    ]
    callbacks += (
        [DeleteToolbox(action=action)]
        if action.args.action == "delete"
        else [EnsureNamespace(action=action), HarborBuild(workflow=action)]
    )
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
