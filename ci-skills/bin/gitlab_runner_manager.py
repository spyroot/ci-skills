#!/usr/bin/env python3
"""Deploy or remove one toolbox-backed GitLab shell-runner manager.

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
from lib.python.core.executor import WorkflowExecutor
from lib.python.core.gitlab.runner_manager import (
    DeleteManagerRunner,
    DeployManagerRunner,
    GitLabManagerAuth,
    GitLabRunnerManagerAction,
    GitLabRunnerManagerState,
    GitLabRunnerRuntime,
    GuardGitLabRunnerManager,
    ObserveGitLabRunner,
    OpenManagerRunScope,
    PullManagerImage,
    SelectManagerPlanScope,
    SelectResolvedManagerScope,
    VerifyGitLabRunnerOnline,
    VerifyGitLabRunnerPreserved,
    workload_action,
)
from lib.python.core.harbor.registry_mirror import HarborAuth
from lib.python.core.kubernetes.kubernetes import K8SAuth
from lib.python.core.kubernetes.run_scope import (
    RemoveScope,
    ResolveRunScopeAction,
    ResolveRunScopes,
)
from lib.python.core.kubernetes.scc_access import (
    EnsureSCCAccess,
    RemoveSCCAccess,
)
from lib.python.core.toolbox_actions import ConfirmWorkflow, workflow_parser


def build_parser() -> ArgumentParser:
    """Return the catalogue-backed runner-manager parser.

    :returns: Parser for deploy, delete, describe, plan, and apply modes.
    """
    return workflow_parser("gitlab_runner_manager")


def workflow(
    action: GitLabRunnerManagerAction,
) -> WorkflowExecutor[GitLabRunnerManagerState]:
    """Compose the ordered GitLab runner-manager callback lifecycle.

    :param action: Prepared provider, runner, image, and workload inputs.
    :returns: Executor sharing the manager's concrete delivery state.
    :raises ValueError: Required provider inputs or confirmation are invalid.
    """  # noqa: DOC502 - Callback constructors validate their concrete inputs.
    workload = workload_action(action)
    callbacks: AbstractCallbacks[GitLabRunnerManagerState, SkillAction] = [
        K8SAuth(action.target, action.args.timeout),
        GitLabManagerAuth(action),
    ]
    if action.args.action == "deploy":
        callbacks.append(
            HarborAuth(
                action.target,
                action.repository,
                ("pull",),
                action.args.timeout,
            )
        )
    resolve = ResolveRunScopeAction(action.scope_id)
    callbacks.extend((GuardGitLabRunnerManager(action), ResolveRunScopes(resolve)))
    if action.args.action == "delete":
        callbacks.append(SelectResolvedManagerScope(workload))
    else:
        callbacks.append(SelectManagerPlanScope(workload))
    callbacks.extend((ObserveGitLabRunner(action), ConfirmWorkflow(action)))
    if action.args.apply:
        if action.args.action == "deploy":
            callbacks.extend(
                (
                    OpenManagerRunScope(workload),
                    EnsureSCCAccess(action.scc_access()),
                    PullManagerImage(workload),
                    GitLabRunnerRuntime(action),
                    DeployManagerRunner(workload),
                    VerifyGitLabRunnerOnline(action),
                )
            )
        else:
            callbacks.extend(
                (
                    DeleteManagerRunner(workload),
                    VerifyGitLabRunnerPreserved(action),
                    RemoveSCCAccess(action.scc_access()),
                    RemoveScope(resolve),
                )
            )
    return WorkflowExecutor(context=action.context, callbacks=Callbacks(callbacks))


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Execute the selected manager lifecycle and emit its shared result.

    :param argv: Optional command arguments.
    :returns: Existing command status exit code.
    """
    args = build_parser().parse_args(argv)
    action = GitLabRunnerManagerAction(args)
    return action.execute(lambda: workflow(action))


if __name__ == "__main__":
    raise SystemExit(main())
