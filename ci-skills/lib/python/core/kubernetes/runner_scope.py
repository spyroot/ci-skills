"""Observe and remove runner namespaces through shared ownership primitives.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Final

from lib.python.core.delivery_state import DeliveryState
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import (
    K8SCallback,
    KubernetesError,
    KubernetesService,
)
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.kubernetes.resources import ResourceSet
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
                retention=ScopeRetention.TEMPORARY
                if purpose == ScopePurpose.BUILD
                else ScopeRetention.RETAINED,
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
            raise KubernetesError("runner_namespace_readback_invalid") from exc
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
            retention=(
                ScopeRetention.TEMPORARY
                if purpose == ScopePurpose.BUILD
                else ScopeRetention.RETAINED
            )
            if owned
            else ScopeRetention.PRESERVED,
            cleanup_status=NOT_REQUIRED,
        )
    state.runner_scopes[key] = result
    return result


class ObserveGitHubNamespace(K8SCallback["WorkflowAction"]):
    """Bind namespace UID and ownership before GitHub plan confirmation."""

    def run(self) -> JSONObject:
        """Observe the actual selected runner namespace without mutation.

        :returns: Verified prerequisite result; typed scope evidence stays in shared state.
        :raises KubernetesError: An explicitly selected initial namespace is absent or malformed.
        """
        workflow: WorkflowAction = self.action
        explicit = bool(getattr(workflow.args, "namespace", None))
        source = ScopeSource.EXPLICIT if explicit else ScopeSource.TOOLBOX_DEFAULT
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
