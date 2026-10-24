"""Compose shared run-scope and runner callbacks for the GitLab manager.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from typing import ClassVar, Optional

from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import K8SCallback, KubernetesError
from lib.python.core.kubernetes.run_scope import (
    OpenRunScope,
    RunScopeAction,
)
from lib.python.core.kubernetes.runner import DeleteRunner, DeployRunner, PullImage
from lib.python.core.runner import RunnerAction

from .action import (
    GitLabRunnerManagerAction,
    GitLabRunnerManagerState,
    ManagerWorkloadAction,
)


class SelectManagerPlanScope(K8SCallback[ManagerWorkloadAction]):
    """Bind a deploy plan to its actual existing or candidate run scope."""

    def run(self) -> JSONObject:
        """Select one observed scope or the prepared mutation-free candidate.

        :returns: Verified concrete scope identity and whether it already exists.
        :raises TypeError: Workflow state does not retain run scopes.
        :raises KubernetesError: Scope lookup is ambiguous or conflicts with a
            caller-selected namespace.
        """
        state = self.state
        if not isinstance(state, GitLabRunnerManagerState):
            raise TypeError("gitlab_runner_manager_state_required")
        scopes = tuple(state.found_scopes)
        if len(scopes) > 1:
            raise KubernetesError("runner_manager_scope_not_unique")
        candidate = self.action.workflow.candidate_scope
        if candidate is None:
            raise KubernetesError("runner_manager_scope_not_prepared")
        if scopes:
            observed = scopes[0]
            if not candidate.owns_namespace and (
                observed.namespace != candidate.namespace or observed.owns_namespace
            ):
                raise KubernetesError("runner_manager_namespace_mismatch")
            scope = observed
        else:
            scope = candidate
        state.run_scope = scope
        return {"verified": True, "reused": bool(scopes), **scope.as_dict()}


class OpenManagerRunScope(K8SCallback[ManagerWorkloadAction]):
    """Delegate opening the previously selected shared run scope."""

    def __init__(self, action: ManagerWorkloadAction):
        """Retain deferred manager inputs and empty child ownership.

        :param action: Workflow whose selected scope opens only after confirmation.
        """
        super().__init__(action)
        self.child: Optional[OpenRunScope] = None

    @property
    def manager_state(self) -> GitLabRunnerManagerState:
        """Read the concrete manager state.

        :returns: Shared state retaining an optional selected run scope.
        :raises TypeError: The callback was composed with another state type.
        """
        if not isinstance(self.state, GitLabRunnerManagerState):
            raise TypeError("gitlab_runner_manager_state_required")
        return self.state

    def run(self) -> JSONObject:
        """Open the selected scope after its exact ownership lookup and confirmation.

        :returns: Existing shared scope-open evidence with the actual run ID.
        """
        state = self.manager_state
        self.child = OpenRunScope(RunScopeAction(state.run_scope))
        self.child.bind(state)
        return self.child.run()

    def cleanup(self) -> JSONObject:
        """Delegate exact scope cleanup after this callback entered.

        :returns: Shared cleanup evidence, or an empty mapping before entry.
        """
        return self.child.cleanup() if self.child is not None else {}


class SelectResolvedManagerScope(K8SCallback[ManagerWorkloadAction]):
    """Select one unique live scope before deleting its manager resources."""

    def run(self) -> JSONObject:
        """Retain the one exact scope found by the preceding shared lookup.

        :returns: Verified concrete scope identity.
        :raises TypeError: Workflow state does not retain run scopes.
        :raises KubernetesError: Scope lookup is absent, ambiguous, or conflicts
            with an optional caller-selected namespace.
        """
        state = self.state
        if not isinstance(state, GitLabRunnerManagerState):
            raise TypeError("gitlab_runner_manager_state_required")
        scopes = tuple(state.found_scopes)
        if len(scopes) != 1:
            raise KubernetesError("runner_manager_scope_not_unique")
        scope = scopes[0]
        requested = getattr(self.action.workflow.args, "namespace", "")
        if requested and requested != scope.namespace:
            raise KubernetesError("runner_manager_namespace_mismatch")
        state.run_scope = scope
        return {"verified": True, **scope.as_dict()}


class ScopedRunnerCallback(K8SCallback[ManagerWorkloadAction]):
    """Bind one shared runner callback after the run scope is concrete.

    .. attribute :: child_type
        :type: ClassVar[type[K8SCallback[RunnerAction]]]

        Shared runner callback selected by the concrete operation.
    """

    child_type: ClassVar[type[K8SCallback[RunnerAction]]]

    def __init__(self, action: ManagerWorkloadAction):
        """Retain deferred workload inputs and empty child ownership.

        :param action: Workflow whose runner action depends on resolved scope.
        """
        super().__init__(action)
        self.child: Optional[K8SCallback[RunnerAction]] = None

    @property
    def manager_state(self) -> GitLabRunnerManagerState:
        """Read the manager's concrete run-scope state.

        :returns: State populated by scope and provider callbacks.
        :raises TypeError: The callback was composed with another state type.
        """
        if not isinstance(self.state, GitLabRunnerManagerState):
            raise TypeError("gitlab_runner_manager_state_required")
        return self.state

    def _child(self) -> K8SCallback[RunnerAction]:
        """Construct and bind the existing shared runner callback exactly once.

        :returns: Shared callback using an action derived from resolved scope.
        """
        if self.child is None:
            runner = self.action.runner(self.manager_state.run_scope)
            self.child = self.child_type(runner)
            self.child.bind(self.state)
        self.child.whoami()
        return self.child

    def run(self) -> JSONObject:
        """Run the existing shared callback against the bound workflow state.

        :returns: Shared runner operation evidence.
        """
        return self._child().run()

    def cleanup(self) -> JSONObject:
        """Delegate resource recovery to an entered shared callback.

        :returns: Shared cleanup evidence or an empty mapping before entry.
        """
        return self.child.cleanup() if self.child is not None else {}


class PullManagerImage(ScopedRunnerCallback):
    """Verify the immutable toolbox image through shared pull/read-back.

    .. attribute :: child_type

        Shared PullImage callback used after scope resolution.
    """

    child_type = PullImage


class DeployManagerRunner(ScopedRunnerCallback):
    """Deploy the exact-node privileged manager through shared runner logic.

    .. attribute :: child_type

        Shared DeployRunner callback used after scope resolution.
    """

    child_type = DeployRunner


class DeleteManagerRunner(ScopedRunnerCallback):
    """Delete only the resolved manager Deployment and private Secrets.

    .. attribute :: child_type

        Shared DeleteRunner callback used after scope resolution.
    """

    child_type = DeleteRunner


def workload_action(workflow: GitLabRunnerManagerAction) -> ManagerWorkloadAction:
    """Return the one typed action shared by manager workload callbacks.

    :param workflow: Prepared runner-manager operation.
    :returns: Deferred action that derives names from shared run-scope state.
    """
    return workflow.workload()
