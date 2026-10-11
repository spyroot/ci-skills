#!/usr/bin/env python3
"""Build, publish, deploy, and register a toolbox-backed GitHub runner.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from argparse import ArgumentParser

import _bootstrap  # noqa: F401
from core.action import Executor
from core.github import GitHubAuth
from core.github_runner import (
    CreateGitHubRunner,
    DeleteGitHubRunner,
    GitHubRunnerRuntime,
)
from core.kubernetes import K8SAuth
from core.registry_mirror import HarborAuth, HarborPush, PushAction
from core.runner import DeleteRunner, DeployRunner, PullImage
from core.toolbox_actions import (
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


def workflow(action: WorkflowAction) -> Executor:
    """Connect independent callback outputs into the selected operation.

    :param action: Prepared caller inputs and shared target selection.
    :returns: Executor with authentication preceding every provider operation.
    """
    callbacks = [
        K8SAuth(target=action.target, timeout=action.args.timeout),
        GitHubAuth(target=action.target, timeout=action.args.timeout),
    ]
    if action.args.action == "delete":
        callbacks += [
            ConfirmWorkflow(action=action),
            DeleteRunner(action=action.runner),
            DeleteGitHubRunner(action=action.runner),
        ]
    else:
        auth = HarborAuth(
            target=action.target,
            repository=action.spec.image_repository,
            actions=("pull", "push"),
            timeout=action.args.timeout,
        )
        build = HarborBuild(workflow=action)
        publish = HarborPush(
            action=PushAction(
                action.spec.image_repository, action.spec.image_tag, build
            )
        )
        runtime = GitHubRunnerRuntime(action=action.runner)
        runner = DeployRunner(
            action=action.runner, image=publish, runtime=runtime, dockerconfig=auth
        )
        callbacks += [
            auth,
            ConfirmWorkflow(action=action),
            EnsureNamespace(action=action),
            build,
            publish,
            PullImage(action=action.runner, image=publish, dockerconfig=auth),
            runtime,
            runner,
            CreateGitHubRunner(action=action.runner, deployment=runner),
        ]
    return Executor(context=action.context, callbacks=callbacks)


def main(argv: list[str] | None = None) -> int:
    """Execute the selected workflow and emit its shared result envelope.

    :param argv: Optional command arguments.
    :returns: Existing command status exit code.
    """
    args = build_parser().parse_args(argv)
    action = WorkflowAction(args)
    return action.execute(lambda: workflow(action))


if __name__ == "__main__":
    raise SystemExit(main())
