"""Compose shared run-scope and runner callbacks for the GitLab manager.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from typing import ClassVar, Optional

from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import (
    K8SCallback,
    KubernetesError,
    KubernetesService,
)
from lib.python.core.kubernetes.run_scope import (
    OpenRunScope,
    RunScope,
    RunScopeAction,
)
from lib.python.core.kubernetes.runner import DeleteRunner, DeployRunner, PullImage
from lib.python.core.kubernetes.runner_scope import observe_namespace, scope_key
from lib.python.core.runner import RunnerAction
from lib.python.core.runner_identity import NameSource, ResolvedName
from lib.python.core.runner_scope import ScopePurpose, ScopeSource, ScopeState

from .action import (
    MANAGER_ROLE,
    GitLabRunnerManagerAction,
    GitLabRunnerManagerState,
    ManagerWorkloadAction,
)


def resolve_manager_name(
    service: KubernetesService,
    scope: RunScope,
    preferred: ResolvedName,
) -> ResolvedName:
    """Resolve one existing owned name before selecting a new default.

    :param service: Bound Kubernetes observation service.
    :param scope: Exact namespace and operation identity.
    :param preferred: Planned repository-derived name and source.
    :returns: Unique existing identity, otherwise the uncreated planned name.
    :raises KubernetesError: Both legacy and preferred names identify owned workloads.
    """
    candidates = {scope.name(MANAGER_ROLE), preferred.value}
    observed_names = [
        name
        for name in sorted(candidates)
        if (document := service.read("Deployment", name, scope.namespace)) is not None
        and scope.resources(service).owns(document)
    ]
    if len(observed_names) > 1:
        raise KubernetesError("runner_manager_workload_ambiguous")
    return (
        ResolvedName(observed_names[0], NameSource.PROVIDER)
        if observed_names
        else preferred
    )


def preserve_manager_name(
    action: ManagerWorkloadAction, service: KubernetesService, scope: RunScope
) -> None:
    """Retain a unique observed manager name through the shared resolver.

    :param action: Manager inputs receiving the discovered identity.
    :param service: Bound Kubernetes observation service.
    :param scope: Exact live scope selected by ownership lookup.
    :returns: None after retaining the observed or planned identity.
    :raises KubernetesError: Multiple owned workload names are present.
    """  # noqa: DOC502 - Shared resolver propagates Kubernetes read-back failures.
    preferred = action.workflow.resolved_runner_name or ResolvedName(
        action.runner(scope).name, NameSource.UNKNOWN
    )
    action.workflow.resolved_runner_name = resolve_manager_name(
        service, scope, preferred
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
                observed.namespace != candidate.namespace
            ):
                raise KubernetesError("runner_manager_namespace_mismatch")
            scope = observed
        else:
            scope = candidate
        state.run_scope = scope
        if scopes:
            preserve_manager_name(self.action, self.kubernetes, scope)
        source = (
            ScopeSource.EXPLICIT
            if getattr(self.action.workflow.args, "namespace", None)
            else ScopeSource.EXISTING_RUN_SCOPE
            if scopes
            else ScopeSource.OPERATION_CREATED
        )
        for key, observation in tuple(state.runner_scopes.items()):
            if observation.purpose == ScopePurpose.MANAGER:
                del state.runner_scopes[key]
        observe_namespace(
            state,
            self.kubernetes,
            namespace=scope.namespace,
            purpose=ScopePurpose.MANAGER,
            source=source,
            operation=scope.run_id,
            required=not scope.owns_namespace,
        )
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
        result = self.child.run()
        scope = state.run_scope
        previous = state.runner_scopes[scope_key(ScopePurpose.MANAGER, scope.namespace)]
        observe_namespace(
            state,
            self.kubernetes,
            namespace=scope.namespace,
            purpose=ScopePurpose.MANAGER,
            source=previous.source,
            operation=scope.run_id,
            required=True,
        )
        return result

    def cleanup(self) -> JSONObject:
        """Delegate exact scope cleanup after this callback entered.

        :returns: Shared cleanup evidence, or an empty mapping before entry.
        """
        result = self.child.cleanup() if self.child is not None else {}
        state = self.manager_state
        if self.child is not None and hasattr(state, "run_scope"):
            scope = state.run_scope
            previous = state.runner_scopes[
                scope_key(ScopePurpose.MANAGER, scope.namespace)
            ]
            observe_namespace(
                state,
                self.kubernetes,
                namespace=scope.namespace,
                purpose=ScopePurpose.MANAGER,
                source=previous.source,
                operation=scope.run_id,
                required=not scope.owns_namespace,
            )
        return result


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
        preserve_manager_name(self.action, self.kubernetes, scope)
        observe_namespace(
            state,
            self.kubernetes,
            namespace=scope.namespace,
            purpose=ScopePurpose.MANAGER,
            source=ScopeSource.EXISTING_RUN_SCOPE,
            operation=scope.run_id,
            required=True,
        )
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


class ObserveManagerNamespace(K8SCallback[ManagerWorkloadAction]):
    """Read final manager scope presence after explicit resource deletion."""

    def run(self) -> JSONObject:
        """Retain actual namespace absence or borrowed-namespace preservation.

        :returns: Verified final scope observation.
        :raises TypeError: Shared state is not the runner-manager state.
        :raises KubernetesError: A borrowed scope disappeared or owned cleanup is incomplete.
        """
        if not isinstance(self.state, GitLabRunnerManagerState):
            raise TypeError("gitlab_runner_manager_state_required")
        scope = self.state.run_scope
        previous = self.state.runner_scopes[
            scope_key(ScopePurpose.MANAGER, scope.namespace)
        ]
        observed = observe_namespace(
            self.state,
            self.kubernetes,
            namespace=scope.namespace,
            purpose=ScopePurpose.MANAGER,
            source=previous.source,
            operation=scope.run_id,
            required=not scope.owns_namespace,
        )
        if (
            scope.owns_namespace
            and observed.uid is not None
            and observed.state == ScopeState.PRESENT
        ):
            raise KubernetesError("runner_namespace_cleanup_not_absent")
        return {"verified": True, "namespace": scope.namespace}
