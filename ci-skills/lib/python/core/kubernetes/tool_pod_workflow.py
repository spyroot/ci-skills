"""Compose reusable tool preparation without duplicating provider operations.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from lib.python.core.abstract.callbacks import AbstractCallbacks
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.kubernetes.tool_pod import (
    OpenRunScope,
    PrepareToolPodAction,
    ReadToolState,
    ResolveRunScopeAction,
    ResolveRunScopes,
    RunScope,
    RunScopeAction,
    StageToolPod,
    VerifyTools,
    tool_callbacks,
)


def prepare_tool_pod_callbacks(
    action: PrepareToolPodAction, scope: RunScope
) -> AbstractCallbacks[DeliveryState, SkillAction]:
    """Compose preparation in the existing ordered callback lifecycle.

    Authentication is supplied by the consuming workflow. This function does no
    provider reads, installation or cleanup; each atomic callback owns that work.

    :param action: Confirmed image, dependencies, placement and native mounts.
    :param scope: Concrete selected or newly generated run scope.
    :returns: Scope lookup/open, staging, tool reads, installation and verification.
    """
    callbacks: AbstractCallbacks[DeliveryState, SkillAction] = []
    if action.run_id is not None:
        callbacks.append(ResolveRunScopes(ResolveRunScopeAction(action.run_id)))
    callbacks.extend(
        (
            OpenRunScope(RunScopeAction(scope)),
            StageToolPod(action),
            ReadToolState(action),
        )
    )
    callbacks.extend(tool_callbacks(action))
    callbacks.append(VerifyTools(action))
    return callbacks
