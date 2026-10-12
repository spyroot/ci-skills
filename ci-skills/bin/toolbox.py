#!/usr/bin/env python3
"""Run one toolbox or registry operation through the shared callback workflow.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from argparse import ArgumentParser
from collections.abc import Sequence
from typing import Optional

import _bootstrap  # noqa: F401
from lib.python.core.abstract.callbacks import Callbacks
from lib.python.core.abstract.skill_exector import WorkflowExecutor
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.harbor.registry_mirror import HarborAuth
from lib.python.core.kubernetes.kubernetes import K8SAuth
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


def workflow(action: WorkflowAction) -> WorkflowExecutor[DeliveryState]:
    """Construct the already ordered callbacks for the selected plan or apply mode.

    :param action: Prepared runtime target and concrete operation inputs.
    :returns: Executor sharing one typed state across the declared callback sequence.
    :raises ValueError: If a callback already belongs to a different workflow state.
    """  # noqa: DOC502 - Executor construction rejects callbacks bound elsewhere.
    capabilities = (
        ("pull", "delete") if action.args.action == "delete" else ("pull", "push")
    )
    callbacks = [
        K8SAuth(action.target, action.args.timeout),
        HarborAuth(action.target, action.repository, capabilities, action.args.timeout),
        ConfirmWorkflow(action),
    ]
    if action.args.apply:
        if action.args.action == "delete":
            callbacks.append(DeleteToolbox(action))
        else:
            callbacks.extend((EnsureNamespace(action), HarborBuild(action)))
    return WorkflowExecutor(context=action.context, callbacks=Callbacks(callbacks))


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run this operation and emit the shared report envelope.

    :param argv: Optional command arguments.
    :returns: Canonical operation exit code.
    """
    action = WorkflowAction(build_parser().parse_args(argv))
    return action.execute(lambda: workflow(action))


if __name__ == "__main__":
    raise SystemExit(main())
