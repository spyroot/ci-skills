"""Observe and remove runner namespaces through shared ownership primitives.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Final

from lib.python.core.delivery_state import DeliveryState
from lib.python.core.json_types import JSONObject, json_object
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
    ResourceSet,
)
from lib.python.core.runner_identity import NameSource, ResolvedName
from lib.python.core.runner_options import kubernetes_runner_name
from lib.python.core.runner_scope import (
    NOT_REQUIRED,
    PENDING,
    ScopeObservation,
    ScopeOwnership,
    ScopePurpose,
    ScopeRetention,
    ScopeSource,
    ScopeState,
)
from lib.python.sys.status import APPLIED, NO_OP, PASS

if TYPE_CHECKING:
    from lib.python.core.toolbox_actions import WorkflowAction

NAMESPACE_KIND: Final = "Namespace"
UID_FIELD: Final = "uid"
LABELS_FIELD: Final = "labels"
EXPLICIT_NAMESPACE_MISSING: Final = "selected_namespace_missing"
NAMESPACE_UID_CHANGED: Final = "runner_namespace_uid_changed_rerun_plan"
NAMESPACE_AMBIGUOUS: Final = "runner_namespace_ambiguous"
ITEMS_FIELD: Final = "items"
NAME_FIELD: Final = "name"
NAMESPACE_RESOURCE: Final = "namespaces"
GET_VERB: Final = "get"
SELECTOR_OPTION: Final = "-l"
JSON_OPTIONS: Final = ("-o", "json")
DEPLOYMENT_KIND: Final = "Deployment"
DEPLOYMENT_RESOURCE: Final = "deployments"
NAMESPACE_OPTION: Final = "--namespace"
SPEC_FIELD: Final = "spec"
TEMPLATE_FIELD: Final = "template"
INIT_CONTAINERS_FIELD: Final = "initContainers"
ENVIRONMENT_FIELD: Final = "env"
ENV_VALUE_FIELD: Final = "value"
RUNNER_REPOSITORY_ENV: Final = "RUNNER_REPOSITORY"
RUNNER_HOST_ENV: Final = "RUNNER_HOST"
NAMESPACE_READBACK_INVALID: Final = "runner_namespace_readback_invalid"


def scope_key(purpose: ScopePurpose, namespace: str) -> str:
    """Select one stable observation slot without conflating resource roles.

    :param purpose: Resource role using the namespace.
    :param namespace: Exact namespace selected by that role.
    :returns: Role-qualified namespace key.
    """
    return f"{purpose}:{namespace}"


def observe_namespace(
    state: DeliveryState,
    service: KubernetesService,
    *,
    namespace: str,
    purpose: ScopePurpose,
    source: ScopeSource,
    operation: str,
    required: bool = False,
) -> ScopeObservation:
    """Read native UID and namespace ownership without changing either.

    :param state: Shared state receiving the finite observation.
    :param service: Bound Kubernetes service.
    :param namespace: Exact caller-selected or planned namespace.
    :param purpose: Workload role using this namespace.
    :param source: Origin of the namespace selection.
    :param operation: Existing ResourceSet ownership identity to compare.
    :param required: Whether absence is a missing caller prerequisite.
    :returns: Present observation or an uncreated planned/previously removed scope.
    :raises KubernetesError: Namespace read-back is malformed or a required namespace is missing.
    """
    key = scope_key(purpose, namespace)
    previous = state.runner_scopes.get(key)
    observed = service.read(NAMESPACE_KIND, namespace, "")
    if observed is None:
        if required:
            raise KubernetesError(EXPLICIT_NAMESPACE_MISSING)
        result = (
            previous.removed()
            if previous and (previous.uid or previous.state == ScopeState.ABSENT)
            else ScopeObservation(
                purpose,
                namespace,
                source,
                ownership=ScopeOwnership.OPERATION_OWNED,
                retention=ScopeRetention.RETAINED,
                cleanup_status=PENDING,
            )
        )
    else:
        try:
            metadata = KubernetesObject(observed).metadata
            uid = metadata.required_text(UID_FIELD)
            labels = metadata.object(LABELS_FIELD).document
            owned = bool(labels) and ResourceSet(service, "", operation).owns(observed)
        except TypeError as exc:
            raise KubernetesError(NAMESPACE_READBACK_INVALID) from exc
        if previous and previous.uid and previous.uid != uid:
            raise KubernetesError(NAMESPACE_UID_CHANGED)
        result = ScopeObservation(
            purpose,
            namespace,
            source,
            uid=uid,
            ownership=ScopeOwnership.OPERATION_OWNED
            if owned
            else ScopeOwnership.BORROWED,
            state=ScopeState.PRESENT,
            retention=ScopeRetention.RETAINED if owned else ScopeRetention.PRESERVED,
            cleanup_status=NOT_REQUIRED,
        )
    state.runner_scopes[key] = result
    return result


class ObserveGitHubNamespace(K8SCallback["WorkflowAction"]):
    """Bind namespace UID and ownership before GitHub plan confirmation."""

    def _repository_matches(self, deployment: KubernetesObject) -> bool:
        """Verify an owned Deployment identifies the selected provider repository.

        :param deployment: Independently observed native Deployment.
        :returns: Whether one init container identifies the exact provider repository.
        :raises KubernetesError: Deployment provider-identity fields are malformed.
        """
        workflow = self.action
        if workflow.target.github is None:
            return False
        try:
            pod = (
                deployment.object(SPEC_FIELD).object(TEMPLATE_FIELD).object(SPEC_FIELD)
            )
            for container in pod.objects(INIT_CONTAINERS_FIELD):
                environment = container.objects(ENVIRONMENT_FIELD)
                repository = tuple(
                    item.text(ENV_VALUE_FIELD)
                    for item in environment
                    if item.text(NAME_FIELD) == RUNNER_REPOSITORY_ENV
                )
                host = tuple(
                    item.text(ENV_VALUE_FIELD)
                    for item in environment
                    if item.text(NAME_FIELD) == RUNNER_HOST_ENV
                )
                if repository == (workflow.repository_identity.reference,) and host == (
                    workflow.target.github.host,
                ):
                    return True
        except TypeError as exc:
            raise KubernetesError(NAMESPACE_READBACK_INVALID) from exc
        return False

    def _owned_runner_names(self, namespace: str, operation: str) -> tuple[str, ...]:
        """Resolve native workload names from exact operation labels and provider identity.

        :param namespace: Native namespace selected by its ownership labels.
        :param operation: Exact observed operation label, independent of workload name.
        :returns: Valid owned names matching the selected repository, host, and explicit override.
        :raises KubernetesError: Native Deployment objects or ownership observations are malformed.
        """  # noqa: DOC502 - Shared native access and ownership validation propagate errors.
        resources = ResourceSet(self.kubernetes, namespace, operation)
        try:
            document = KubernetesObject(
                json_object(
                    self.kubernetes.json(
                        GET_VERB,
                        DEPLOYMENT_RESOURCE,
                        NAMESPACE_OPTION,
                        namespace,
                        SELECTOR_OPTION,
                        f"{MANAGER_LABEL}={MANAGER_VALUE},{OPERATION_LABEL}={operation}",
                        *JSON_OPTIONS,
                    )
                )
            )
            names = []
            for deployment in document.objects(ITEMS_FIELD):
                if not resources.owns(
                    deployment.document
                ) or not self._repository_matches(deployment):
                    continue
                name = deployment.name
                if (
                    getattr(self.action.args, "name", None)
                    and name != self.action.runner.name
                ):
                    continue
                try:
                    name = kubernetes_runner_name(
                        ResolvedName(name, NameSource.EXPLICIT)
                    ).value
                except ValueError:
                    continue
                names.append(name)
            return tuple(names)
        except TypeError as exc:
            raise KubernetesError(NAMESPACE_READBACK_INVALID) from exc

    def _select_namespace(self, explicit: bool) -> ScopeSource:
        """Reuse an owned namespace while preserving verified native workload identity.

        :param explicit: Whether the caller chose an exact existing namespace.
        :returns: Native observation source after binding current runner identity.
        :raises KubernetesError: Discovery is ambiguous or native objects are malformed.
        """  # noqa: DOC502 - Shared ownership and native read helpers propagate failures.
        workflow = self.action
        current = ResourceSet(self.kubernetes, "", workflow.runner.operation)
        try:
            if explicit:
                item = self.kubernetes.read(
                    NAMESPACE_KIND, workflow.runner.namespace, ""
                )
                objects = (KubernetesObject(item),) if item is not None else ()
            else:
                document = KubernetesObject(
                    json_object(
                        self.kubernetes.json(
                            GET_VERB,
                            NAMESPACE_RESOURCE,
                            SELECTOR_OPTION,
                            f"{MANAGER_LABEL}={MANAGER_VALUE}",
                            *JSON_OPTIONS,
                        )
                    )
                )
                objects = document.objects(ITEMS_FIELD)
            owned = []
            for item in objects:
                operation = workflow.runner.operation
                name = workflow.runner.name
                if not current.owns(item.document):
                    native = item.metadata.object(LABELS_FIELD).text(OPERATION_LABEL)
                    if native is None:
                        continue
                    observed = ResourceSet(self.kubernetes, "", native)
                    if not observed.owns(item.document):
                        continue
                    names = self._owned_runner_names(item.name, native)
                    if len(names) > 1:
                        raise KubernetesError(NAMESPACE_AMBIGUOUS)
                    if not names:
                        continue
                    operation = native
                    name = names[0]
                owned.append((item.name, operation, name))
            if len(owned) > 1:
                raise KubernetesError(NAMESPACE_AMBIGUOUS)
            if owned:
                namespace, operation, name = owned[0]
                workflow.runner = replace(
                    workflow.runner,
                    namespace=namespace,
                    name=name,
                    ownership_id=operation,
                )
                if not getattr(workflow.args, "name", None) and (
                    workflow.resolved_runner_name is None
                    or workflow.resolved_runner_name.value != name
                ):
                    workflow.resolved_runner_name = ResolvedName(
                        name, NameSource.PROVIDER
                    )
                for key, previous in tuple(self.state.runner_scopes.items()):
                    if previous.purpose == ScopePurpose.RUNNER:
                        del self.state.runner_scopes[key]
                return (
                    ScopeSource.EXPLICIT if explicit else ScopeSource.EXISTING_RUN_SCOPE
                )
        except TypeError as exc:
            raise KubernetesError(NAMESPACE_READBACK_INVALID) from exc
        return ScopeSource.EXPLICIT if explicit else ScopeSource.OPERATION_CREATED

    def run(self) -> JSONObject:
        """Observe the actual selected runner namespace without mutation.

        :returns: Verified prerequisite result; typed scope evidence stays in shared state.
        :raises KubernetesError: An explicitly selected initial namespace is absent or malformed.
        """
        workflow: WorkflowAction = self.action
        explicit = bool(getattr(workflow.args, "namespace", None))
        source = self._select_namespace(explicit)
        deleting = workflow.args.action == "delete"
        scope = observe_namespace(
            self.state,
            self.kubernetes,
            namespace=workflow.runner.namespace,
            purpose=ScopePurpose.RUNNER,
            source=source,
            operation=workflow.runner.operation,
            required=explicit and not deleting,
        )
        if workflow.args.action == "update" and not getattr(
            workflow.args, "name", None
        ):
            current = self.kubernetes.read(
                "Deployment", workflow.runner.name, workflow.runner.namespace
            )
            if current is None:
                raise KubernetesError("runner_update_identity_missing: provide --name")
        if deleting and scope.state == ScopeState.PLANNED:
            self.state.runner_scopes[scope_key(scope.purpose, scope.namespace)] = (
                replace(
                    scope,
                    state=ScopeState.ABSENT,
                    ownership=ScopeOwnership.UNKNOWN,
                    retention=ScopeRetention.UNKNOWN,
                    cleanup_status=PASS,
                )
            )
        return {"verified": True, "namespace": workflow.runner.namespace}


class DeleteGitHubNamespace(K8SCallback["WorkflowAction"]):
    """Delete only the namespace whose live UID and operation ownership match."""

    def run(self) -> JSONObject:
        """Remove an owned namespace after runner resources and registration.

        :returns: Verified absence or borrowed-namespace preservation.
        :raises KubernetesError: UID changed, ownership is inconsistent, or removal fails.
        """
        workflow: WorkflowAction = self.action
        key = scope_key(ScopePurpose.RUNNER, workflow.runner.namespace)
        before = self.state.runner_scopes[key]
        current = self.kubernetes.read(NAMESPACE_KIND, before.namespace, "")
        changed = False
        if current is not None:
            uid = KubernetesObject(current).metadata.required_text(UID_FIELD)
            if before.uid != uid:
                raise KubernetesError(NAMESPACE_UID_CHANGED)
            if before.ownership == ScopeOwnership.OPERATION_OWNED:
                resources = ResourceSet(self.kubernetes, "", workflow.runner.operation)
                if not resources.owns(current):
                    raise KubernetesError("runner_namespace_ownership_mismatch")
                changed = resources.delete(
                    NAMESPACE_KIND, before.namespace, expected_uid=before.uid
                )
        elif before.ownership == ScopeOwnership.BORROWED:
            raise KubernetesError("borrowed_namespace_preservation_failed")
        after = observe_namespace(
            self.state,
            self.kubernetes,
            namespace=before.namespace,
            purpose=before.purpose,
            source=before.source,
            operation=workflow.runner.operation,
            required=before.ownership == ScopeOwnership.BORROWED,
        )
        if (
            before.ownership == ScopeOwnership.OPERATION_OWNED
            and after.state == ScopeState.PRESENT
        ):
            raise KubernetesError("runner_namespace_cleanup_not_absent")
        if after.state == ScopeState.PLANNED:
            self.state.runner_scopes[key] = replace(
                after, state=ScopeState.ABSENT, cleanup_status=PASS
            )
        return {
            "verified": True,
            "action": APPLIED if changed else NO_OP,
            "namespace": before.namespace,
        }
