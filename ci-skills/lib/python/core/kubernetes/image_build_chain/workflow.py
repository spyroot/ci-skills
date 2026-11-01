"""One shared executor composition for the CI13 image-build chain.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from lib.python.core.abstract.callbacks import AbstractCallbacks, Callbacks
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.executor import WorkflowExecutor
from lib.python.core.gitlab.runner_manager import (
    DeployManagerRunner,
    GitLabRunnerRuntime,
    GuardGitLabRunnerManager,
    ObserveGitLabRunner,
    OpenManagerRunScope,
    SelectManagerPlanScope,
    workload_action,
)
from lib.python.core.harbor.registry_mirror import HarborAuth, HarborPush, PushAction
from lib.python.core.kubernetes.kubernetes import K8SAuth
from lib.python.core.kubernetes.podman_probe import ProbeMode
from lib.python.core.kubernetes.run_scope import (
    ResolveRunScopeAction,
    ResolveRunScopes,
)
from lib.python.core.kubernetes.scc_access import EnsureSCCAccess
from lib.python.core.toolbox_actions import (
    ConfirmWorkflow,
    EnsureNamespace,
    HarborBuild,
)

from .callbacks import (
    ConfirmChainManager,
    CreateRunnerRecord,
    DeferredComponent,
    PrepareRunnerManager,
    ProviderTransition,
    ResolveChainPlan,
    RetainProvider,
    ScopedGitLabAuth,
)
from .model import ImageBuildChainAction, ImageBuildChainState
from .verification import CleanupChain


def workflow(action: ImageBuildChainAction) -> WorkflowExecutor[ImageBuildChainState]:
    """Compose every CI13 component into one callback lifecycle and state.

    :param action: Prepared chain target and component actions.
    :returns: One executor whose reverse cleanup owns all temporary resources.
    :raises ValueError: Required component inputs or callback bindings are invalid.
    """  # noqa: DOC502 - callback constructors enforce their contracts.
    state = action.state
    callbacks: AbstractCallbacks[ImageBuildChainState, SkillAction] = [
        K8SAuth(action.target, action.args.timeout),
        HarborAuth(
            action.target,
            action.toolbox.repository,
            ("pull", "push"),
            action.args.timeout,
        ),
        RetainProvider(action, ProviderTransition.RETAIN_TOOLBOX_HARBOR),
        ConfirmWorkflow(action.toolbox),
        RetainProvider(action, ProviderTransition.USE_TOOLBOX_HARBOR),
        ScopedGitLabAuth(action, state.runner_auth_plan),
        RetainProvider(action, ProviderTransition.RETAIN_RUNNER_GITLAB),
        RetainProvider(action, ProviderTransition.USE_RUNNER_GITLAB),
        ResolveChainPlan(action),
    ]
    if action.mode == ProbeMode.APPLY:
        callbacks.extend(
            (
                CleanupChain(action),
                EnsureNamespace(action.toolbox),
                HarborBuild(action.toolbox),
                HarborPush(
                    PushAction(
                        action.toolbox.repository,
                        action.toolbox.spec.image_tag,
                    )
                ),
                CreateRunnerRecord(action),
                PrepareRunnerManager(action),
                DeferredComponent(
                    action,
                    lambda state: GuardGitLabRunnerManager(state.manager_action),
                ),
                DeferredComponent(
                    action,
                    lambda state: ResolveRunScopes(
                        ResolveRunScopeAction(state.manager_action.scope_id)
                    ),
                ),
                DeferredComponent(
                    action,
                    lambda state: SelectManagerPlanScope(
                        workload_action(state.manager_action)
                    ),
                ),
                DeferredComponent(
                    action, lambda state: ObserveGitLabRunner(state.manager_action)
                ),
                ConfirmChainManager(action),
                DeferredComponent(
                    action,
                    lambda state: OpenManagerRunScope(
                        workload_action(state.manager_action)
                    ),
                ),
                DeferredComponent(
                    action,
                    lambda state: EnsureSCCAccess(state.manager_action.scc_access()),
                ),
                DeferredComponent(
                    action, lambda state: GitLabRunnerRuntime(state.manager_action)
                ),
                DeferredComponent(
                    action,
                    lambda state: DeployManagerRunner(
                        workload_action(state.manager_action)
                    ),
                ),
            )
        )
    return WorkflowExecutor(context=state, callbacks=Callbacks(callbacks))
