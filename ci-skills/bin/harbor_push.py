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
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.executor import WorkflowExecutor
from lib.python.core.harbor.registry_mirror import HarborAuth, HarborPush, PushAction
from lib.python.core.toolbox_actions import (
    ConfirmWorkflow,
    WorkflowAction,
    workflow_parser,
)


def build_parser() -> ArgumentParser:
    """Return this operation's shared argument parser.

    :returns: Catalogue-backed command parser.
    """
    return workflow_parser("harbor_push")


def workflow(action: WorkflowAction) -> WorkflowExecutor[DeliveryState]:
    """Construct the already ordered callbacks for the selected plan or apply mode.

    :param action: Prepared runtime target and concrete operation inputs.
    :returns: Executor sharing one typed state across the declared callback sequence.
    :raises ValueError: If a callback already belongs to a different workflow state.
    """  # noqa: DOC502 - Executor construction rejects callbacks bound elsewhere.
    callbacks = [
        HarborAuth(
            action.target, action.repository, ("pull", "push"), action.args.timeout
        ),
        ConfirmWorkflow(action),
    ]
    if action.args.apply:
        callbacks.append(HarborPush(PushAction(action.repository, action.args.tag)))
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
