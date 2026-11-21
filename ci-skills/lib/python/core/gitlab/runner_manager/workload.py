"""Compose shared run-scope and runner callbacks for the GitLab manager.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from typing import ClassVar, Final, Optional

from lib.python.core.abstract.callback import (
    AbstractCallback,
    DeferredCallback,
    LocalCallback,
)
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import (
    K8SCallback,
    KubernetesError,
    KubernetesService,
)
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.kubernetes.resources import (
    MANAGER_LABEL,
    MANAGER_VALUE,
    OPERATION_LABEL,
)
from lib.python.core.kubernetes.run_scope import (
    RUN_ID_LABEL,
    OpenRunScope,
    RunScope,
    RunScopeAction,
    validate_namespace,
)
from lib.python.core.kubernetes.runner import (
    DeleteRunner,
    DeployRunner,
    PullImage,
)
from lib.python.core.kubernetes.runner_scope import observe_namespace, scope_key
from lib.python.core.kubernetes.scc_access import role_binding_document
from lib.python.core.runner import RunnerAction, runner_runtime_identities
from lib.python.core.runner_identity import NameSource, ResolvedName
from lib.python.core.runner_scope import (
    ScopeObservation,
    ScopePurpose,
    ScopeSource,
    ScopeState,
)
from lib.python.core.runtime import error_class
from lib.python.sys.status import NO_OP, PASS

from .action import (
    MANAGER_ROLE,
    GitLabRunnerManagerAction,
    GitLabRunnerManagerState,
    ManagerWorkloadAction,
)

MANAGER_SCOPE_NOT_UNIQUE: Final = "runner_manager_scope_not_unique"
_SECRET_COLUMNS: Final = (
    "custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,UID:.metadata.uid"
)


def _run_secret_remnants(
    service: KubernetesService, run_id: str, names: set[str]
) -> bool:
    selector = ",".join(
        (
            f"{MANAGER_LABEL}={MANAGER_VALUE}",
            f"{OPERATION_LABEL}={run_id}",
            f"{RUN_ID_LABEL}={run_id}",
        )
    )
    result = service.command(
        "get",
        "secrets",
        "--all-namespaces",
        "-l",
        selector,
        "-o",
        _SECRET_COLUMNS,
        "--no-headers",
    )
    if result.returncode:
        raise KubernetesError(error_class(result))
    seen: set[tuple[str, str]] = set()
    remnant = False
    for line in result.stdout.splitlines():
        columns = line.split()
        if len(columns) != 3:
            raise KubernetesError("runner_manager_secret_metadata_invalid")
        namespace, name, uid = columns
        key = (namespace, name)
        try:
            validate_namespace(namespace)
        except ValueError:
            raise KubernetesError("runner_manager_secret_metadata_invalid") from None
        if (
            key in seen
            or name == "<none>"
            or uid == "<none>"
            or any(not char.isascii() or not char.isprintable() for char in name + uid)
        ):
            raise KubernetesError("runner_manager_secret_metadata_invalid")
        seen.add(key)
        remnant |= name in names
    return remnant


def _require_manager_resources_absent(
    service: KubernetesService, action: ManagerWorkloadAction, scope: RunScope
) -> None:
    workflow = action.workflow
    names = {scope.name(MANAGER_ROLE), action.runner(scope).name}
    resources = {
        identity for name in names for identity in runner_runtime_identities(name)
    }
    binding = KubernetesObject(role_binding_document(workflow.scc_access(), scope))
    resources.add(("RoleBinding", binding.name))
    for kind, name in sorted(resources):
        if service.read(kind, name, scope.namespace) is not None:
            raise KubernetesError(MANAGER_SCOPE_NOT_UNIQUE)
    secret_names = {name for kind, name in resources if kind == "Secret"}
    if not getattr(workflow.args, "namespace", None) and _run_secret_remnants(
        service, scope.run_id, secret_names
    ):
        raise KubernetesError(MANAGER_SCOPE_NOT_UNIQUE)


def _confirmed_absent_manager_delete(state: GitLabRunnerManagerState) -> bool:
    args = getattr(state, "args", None)
    return getattr(args, "action", None) == "delete" and not state.found_scopes


def present_manager_cleanup(
    callback: AbstractCallback[GitLabRunnerManagerState, SkillAction],
) -> DeferredCallback[GitLabRunnerManagerState, SkillAction]:
    """Defer each existing shared mutation until the confirmed scope is known.

    :param callback: Existing ordered SCC or namespace cleanup callback.
    :returns: Original callback for a present scope, or an empty local callback.
    """
    return DeferredCallback(
        callback.action,
        lambda state: (
            callback if state.found_scopes else LocalCallback(callback.action)
        ),
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
            raise KubernetesError(MANAGER_SCOPE_NOT_UNIQUE)
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
    """Select one live manager scope or independently verify its absence."""

    def _absent_scope(self, state: GitLabRunnerManagerState) -> RunScope:
        """Verify selected namespace and both supported workload identities are absent.

        :param state: Shared state receiving actual namespace absence evidence.
        :returns: Input-derived candidate after independent native absence reads.
        :raises KubernetesError: The generated namespace or any manager resource exists.
        :raises ValueError: The selected namespace or operation identity is invalid.
        """  # noqa: DOC502 - Shared native readers propagate access failures.
        workflow = self.action.workflow
        scope = RunScope.new(
            namespace=getattr(workflow.args, "namespace", None),
            run_id=workflow.scope_id,
            namespace_prefix=workflow.repository_identity.name,
        )
        namespace = self.kubernetes.read("Namespace", scope.namespace, "")
        if scope.owns_namespace and namespace is not None:
            raise KubernetesError(MANAGER_SCOPE_NOT_UNIQUE)
        _require_manager_resources_absent(self.kubernetes, self.action, scope)
        if namespace is None and scope.owns_namespace:
            state.runner_scopes[scope_key(ScopePurpose.MANAGER, scope.namespace)] = (
                ScopeObservation(
                    ScopePurpose.MANAGER,
                    scope.namespace,
                    ScopeSource.EXISTING_RUN_SCOPE,
                    state=ScopeState.ABSENT,
                    cleanup_status=PASS,
                )
            )
        return scope

    def run(self) -> JSONObject:
        """Bind one observed scope or the independently verified absent identity.

        :returns: Verified concrete scope identity.
        :raises TypeError: Workflow state does not retain run scopes.
        :raises KubernetesError: Scope lookup is ambiguous, absence is unproven,
            or the scope conflicts with a caller-selected namespace.
        :raises ValueError: The selected namespace or operation identity is invalid.
        """  # noqa: DOC502,DOC503 - Shared scope and absence readers propagate errors.
        state = self.state
        if not isinstance(state, GitLabRunnerManagerState):
            raise TypeError("gitlab_runner_manager_state_required")
        scopes = tuple(state.found_scopes)
        if len(scopes) > 1:
            raise KubernetesError(MANAGER_SCOPE_NOT_UNIQUE)
        scope = scopes[0] if scopes else self._absent_scope(state)
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
            required=bool(scopes) or not scope.owns_namespace,
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

    def run(self) -> JSONObject:
        """Skip deletion for a confirmed absent standalone delete scope.

        :returns: Native deletion evidence, or empty evidence before absent recheck.
        """
        if _confirmed_absent_manager_delete(self.manager_state):
            return {}
        return super().run()


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
        absent = _confirmed_absent_manager_delete(self.state)
        if absent:
            if self.state.resolved_run_id != scope.run_id or RunScope.find_all(
                self.kubernetes, scope.run_id
            ):
                raise KubernetesError(MANAGER_SCOPE_NOT_UNIQUE)
            _require_manager_resources_absent(self.kubernetes, self.action, scope)
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
        if absent and observed != previous:
            raise KubernetesError(MANAGER_SCOPE_NOT_UNIQUE)
        result: dict[str, str | bool] = {"verified": True, "namespace": scope.namespace}
        if absent:
            result["action"] = NO_OP
        return result
