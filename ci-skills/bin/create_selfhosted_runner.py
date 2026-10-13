#!/usr/bin/env python3
"""Build, publish, deploy, and register a toolbox-backed GitHub runner.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from argparse import ArgumentParser
from collections.abc import Sequence
from typing import Optional

import _bootstrap  # noqa: F401
from lib.python.core.abstract.callbacks import AbstractCallbacks, Callbacks
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.executor import WorkflowExecutor
from lib.python.core.github.github import GitHubAuth
from lib.python.core.github.runner import (
    CreateGitHubRunner,
    DeleteGitHubRunner,
    GitHubRunnerRuntime,
)
from lib.python.core.harbor.registry_mirror import HarborAuth, HarborPush, PushAction
from lib.python.core.kubernetes.kubernetes import K8SAuth
from lib.python.core.kubernetes.runner import DeleteRunner, DeployRunner, PullImage
from lib.python.core.toolbox_actions import (
    ConfirmWorkflow,
    EnsureNamespace,
    HarborBuild,
    WorkflowAction,
    workflow_parser,
)


def build_parser() -> ArgumentParser:
    """Return the catalogue-backed combo argument parser.

    :returns: Parser for this installed workflow.
    """
    return workflow_parser("create_selfhosted_runner")


def workflow(action: WorkflowAction) -> WorkflowExecutor[DeliveryState]:
    """Construct the already ordered callbacks for the selected plan or apply mode.

    :param action: Prepared runtime target and concrete operation inputs.
    :returns: Executor sharing one typed state across the declared callback sequence.
    :raises ValueError: If a build has no specification or a callback already
        belongs to a different workflow state.
    """  # noqa: DOC502 - Executor construction rejects callbacks bound elsewhere.
    callbacks: AbstractCallbacks[DeliveryState, SkillAction] = [
        K8SAuth(action.target, action.args.timeout),
        GitHubAuth(action.target, action.args.timeout),
    ]
    if action.args.action == "delete":
        callbacks.append(ConfirmWorkflow(action))
        if action.args.apply:
            callbacks.extend(
                (DeleteRunner(action.runner), DeleteGitHubRunner(action.runner))
            )
    else:
        callbacks.extend(
            (
                HarborAuth(
                    action.target,
                    action.repository,
                    ("pull", "push"),
                    action.args.timeout,
                ),
                ConfirmWorkflow(action),
            )
        )
        if action.args.apply:
            spec = action.spec
            if spec is None:
                raise ValueError("toolbox_spec_missing")
            callbacks.extend(
                (
                    EnsureNamespace(action),
                    HarborBuild(action),
                    HarborPush(PushAction(action.repository, spec.image_tag)),
                    PullImage(action.runner),
                    GitHubRunnerRuntime(action.runner),
                    DeployRunner(action.runner),
                    CreateGitHubRunner(action.runner),
                )
            )
    return WorkflowExecutor(context=action.context, callbacks=Callbacks(callbacks))


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Execute the selected workflow and emit its shared result envelope.

    :param argv: Optional command arguments.
    :returns: Existing command status exit code.
    """
    args = build_parser().parse_args(argv)
    action = WorkflowAction(args)
    return action.execute(lambda: workflow(action))


if __name__ == "__main__":
    raise SystemExit(main())
