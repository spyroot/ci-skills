"""One shared executor composition for the CI13 image-build chain.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from lib.python.core.abstract.callbacks import AbstractCallbacks, Callbacks
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.executor import WorkflowExecutor
from lib.python.core.gitlab.gitlab_pipeline_start import ResolvePipelineStart
from lib.python.core.gitlab.runner_manager import (
    DeployManagerRunner,
    GitLabRunnerRuntime,
    ObserveGitLabRunner,
    PullManagerImage,
    VerifyGitLabRunnerOnline,
    workload_action,
)
from lib.python.core.harbor.registry_mirror import HarborAuth, HarborPush, PushAction
from lib.python.core.kubernetes.kubernetes import DEFAULT_COMMAND_TIMEOUT, K8SAuth
from lib.python.core.kubernetes.podman_callbacks import (
    ConfirmPodmanProbe,
    OpenProbeNamespace,
    RunPodmanProbe,
    SelectReadyWorker,
)
from lib.python.core.kubernetes.podman_probe import ProbeMode
from lib.python.core.kubernetes.scc_access import EnsureSCCAccess
from lib.python.core.toolbox_actions import (
    ConfirmWorkflow,
    EnsureNamespace,
    HarborBuild,
)

from .callbacks import (
    CreateRunnerRecord,
    DeferredComponent,
    PrepareRunnerManager,
    ProviderTransition,
    ResolveChainPlan,
    RetainProvider,
    ScopedGitLabAuth,
    StartBuildPipeline,
    WatchBuildPipeline,
)
from .model import ImageBuildChainAction, ImageBuildChainState
from .verification import CleanupChain, VerifyBuildJob


def workflow(action: ImageBuildChainAction) -> WorkflowExecutor[ImageBuildChainState]:
    """Compose every CI13 component into one callback lifecycle and state.

    :param action: Prepared chain target and component actions.
    :returns: One executor whose reverse cleanup owns all temporary resources.
    :raises ValueError: Required component inputs or callback bindings are invalid.
    """  # noqa: DOC502 - callback constructors enforce their contracts.
    state = action.state
    callbacks: AbstractCallbacks[ImageBuildChainState, SkillAction] = [
        K8SAuth(action.target, min(action.args.timeout, DEFAULT_COMMAND_TIMEOUT)),
        HarborAuth(
            action.target,
            action.toolbox.repository,
            ("pull", "push"),
            action.args.timeout,
        ),
        RetainProvider(action, ProviderTransition.RETAIN_TOOLBOX_HARBOR),
        ConfirmWorkflow(action.toolbox),
        HarborAuth(
            action.target,
            action.args.build_repository,
            ("pull",),
            action.args.timeout,
        ),
        RetainProvider(action, ProviderTransition.RETAIN_BUILD_HARBOR),
        RetainProvider(action, ProviderTransition.USE_TOOLBOX_HARBOR),
        ScopedGitLabAuth(action, state.runner_auth_plan),
        RetainProvider(action, ProviderTransition.RETAIN_RUNNER_GITLAB),
        ScopedGitLabAuth(action, state.pipeline_plan),
        DeferredComponent(
            action, lambda state: ResolvePipelineStart(state.pipeline_plan)
        ),
        RetainProvider(action, ProviderTransition.RETAIN_PIPELINE_GITLAB),
        RetainProvider(action, ProviderTransition.USE_RUNNER_GITLAB),
        SelectReadyWorker(action.probe),
        ConfirmPodmanProbe(action.probe),
        ResolveChainPlan(action),
    ]
    if action.mode == ProbeMode.APPLY:
        callbacks.extend(
            (
                CleanupChain(action),
                OpenProbeNamespace(action.probe),
                EnsureSCCAccess(action.probe.scc_access()),
                RunPodmanProbe(action.probe),
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
                    action, lambda state: ObserveGitLabRunner(state.manager_action)
                ),
                DeferredComponent(
                    action,
                    lambda state: PullManagerImage(
                        workload_action(state.manager_action)
                    ),
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
                DeferredComponent(
                    action,
                    lambda state: VerifyGitLabRunnerOnline(state.manager_action),
                ),
                RetainProvider(action, ProviderTransition.USE_PIPELINE_GITLAB),
                StartBuildPipeline(action),
                WatchBuildPipeline(action),
                RetainProvider(action, ProviderTransition.USE_BUILD_HARBOR),
                VerifyBuildJob(action),
            )
        )
    return WorkflowExecutor(context=state, callbacks=Callbacks(callbacks))
