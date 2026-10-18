"""Prepare one temporary OpenShift SCC permission for a run-scoped account.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final, Optional

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import JSONObject, json_object
from lib.python.core.kubernetes.kubernetes import (
    K8SCallback,
    KubernetesError,
    KubernetesService,
)
from lib.python.core.kubernetes.resources import ResourceSet
from lib.python.core.kubernetes.run_scope import (
    RunScope,
    RunScopeState,
)
from lib.python.core.runtime import error_class
from lib.python.sys.status import APPLIED, NO_OP, PASS

SCC_API_RESOURCE: Final = "securitycontextconstraints.security.openshift.io"
SCC_CLUSTER_ROLE_PREFIX: Final = "system:openshift:scc:"
SCC_BINDING_ROLE: Final = "scc-permission"
SCC_BINDING_IDENTITY_LENGTH: Final = 16
KUBERNETES_NAME_LIMIT: Final = 253
KUBERNETES_NAME: Final = re.compile(
    r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*\Z"
)


def _validate_name(value: str, field: str) -> None:
    """Validate one caller-selected Kubernetes object or account name.

    :param value: Provider name supplied by the composing consumer.
    :param field: Stable input field named in validation failures.
    :returns: None after validating one DNS-subdomain name.
    :raises ValueError: If the value is empty, too long, or not DNS-subdomain-safe.
    """
    if (
        not value
        or len(value) > KUBERNETES_NAME_LIMIT
        or KUBERNETES_NAME.fullmatch(value) is None
    ):
        raise ValueError(f"{field}_invalid")


@dataclass(frozen=True)
class SCCAccessAction(SkillAction):
    """Select one existing SCC and run-scoped ServiceAccount permission.

    :param constraint: OpenShift SecurityContextConstraint name.
    :param service_account: ServiceAccount name inside the resolved run namespace.
    :raises ValueError: If either name is empty or not DNS-subdomain-safe.
    """

    constraint: str
    service_account: str

    def __post_init__(self) -> None:
        """Validate provider names before any Kubernetes access.

        :raises ValueError: If the SCC or ServiceAccount name is invalid.
        """  # noqa: DOC502 - shared validation raises the documented exception.
        _validate_name(self.constraint, "scc")
        _validate_name(self.service_account, "service_account")

    @property
    def cluster_role(self) -> str:
        """Return the OpenShift ClusterRole representing SCC use.

        :returns: Stable ClusterRole name installed by OpenShift for this SCC.
        """
        return SCC_CLUSTER_ROLE_PREFIX + self.constraint

    def plan(self) -> JSONObject:
        """Describe the permission behavior bound into a consumer plan.

        :returns: Constraint, account, role and reuse-or-temporary mutation policy.
        """
        return {
            "provider": "openshift_scc_access",
            "constraint": self.constraint,
            "service_account": self.service_account,
            "cluster_role": self.cluster_role,
            "behavior": "reuse_existing_or_create_temporary_role_binding",
        }


def service_account_subject(scope: RunScope, service_account: str) -> str:
    """Name one ServiceAccount subject in the resolved run namespace.

    :param scope: Concrete run namespace selected by the workflow.
    :param service_account: Validated ServiceAccount name.
    :returns: Kubernetes RBAC subject string used by authorization read-back.
    """
    return f"system:serviceaccount:{scope.namespace}:{service_account}"


def _binding_role(action: SCCAccessAction) -> str:
    """Derive one bounded role token from the full permission identity.

    :param action: Selected SCC and ServiceAccount names.
    :returns: Stable role token that distinguishes both selected names.
    """
    identity = f"{action.constraint}\0{action.service_account}".encode()
    suffix = hashlib.sha256(identity).hexdigest()[:SCC_BINDING_IDENTITY_LENGTH]
    return f"{SCC_BINDING_ROLE}-{suffix}"


def role_binding_document(action: SCCAccessAction, scope: RunScope) -> JSONObject:
    """Render one run-owned namespaced SCC RoleBinding.

    :param action: Selected SCC and ServiceAccount names.
    :param scope: Concrete namespace and ownership labels for this run.
    :returns: Native RoleBinding referencing the OpenShift SCC ClusterRole.
    """
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {
            "name": scope.name(_binding_role(action)),
            "namespace": scope.namespace,
            "labels": dict(scope.labels),
        },
        "subjects": [
            {
                "kind": "ServiceAccount",
                "name": action.service_account,
                "namespace": scope.namespace,
            }
        ],
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": action.cluster_role,
        },
    }


def _can_i(
    service: KubernetesService,
    *arguments: str,
) -> bool:
    """Read one OpenShift authorization decision on the bound target.

    :param service: Verified Kubernetes service retained by the workflow.
    :param *arguments: Native arguments following ``oc auth can-i``.
    :returns: True for an exact ``yes`` response and false for an exact denial.
    :raises KubernetesError: If the authorization command fails ambiguously.
    """
    result = service.command("auth", "can-i", *arguments, openshift=True)
    answer = result.stdout.strip().casefold()
    if result.returncode == 0 and answer == "yes":
        return True
    if answer == "no":
        return False
    raise KubernetesError(error_class(result))


def _scc_api_available(service: KubernetesService) -> bool:
    """Discover the OpenShift SCC API without inferring from one missing role.

    :param service: Verified Kubernetes service retained by the workflow.
    :returns: Whether the selected target exposes the SCC API resource.
    :raises KubernetesError: If discovery fails ambiguously or has an invalid shape.
    """
    result = service.command("get", "--raw=/apis/security.openshift.io/v1")
    if result.returncode:
        detail = f"{result.stdout}\n{result.stderr}".casefold()
        if "404" in detail or "not found" in detail:
            return False
        raise KubernetesError("scc_api_" + error_class(result))
    try:
        document = json_object(response_decoder().decode(result.stdout))
    except (TypeError, ValueError, RecursionError) as exc:
        raise KubernetesError("scc_api_invalid") from exc
    resources = document.get("resources")
    available = (
        document.get("kind") == "APIResourceList"
        and document.get("groupVersion") == "security.openshift.io/v1"
        and isinstance(resources, list)
        and any(
            isinstance(resource, dict)
            and resource.get("name") == "securitycontextconstraints"
            for resource in resources
        )
    )
    if not available:
        raise KubernetesError("scc_api_invalid")
    return True


class EnsureSCCAccess(K8SCallback[SCCAccessAction]):
    """Reuse an existing SCC right or create one temporary run-owned binding."""

    def __init__(self, action: SCCAccessAction):
        """Retain permission inputs and initialize empty resource ownership.

        :param action: Selected SCC and run-scoped ServiceAccount.
        """
        super().__init__(action)
        self.resources: Optional[ResourceSet] = None

    def _scope(self) -> RunScope:
        """Read the concrete run scope opened by the preceding callback.

        :returns: Bound run scope carrying the selected namespace.
        :raises TypeError: If the workflow state does not provide run-scope state.
        :raises AttributeError: If composition invokes this callback before scope open.
        """
        state = self.state
        if not isinstance(state, RunScopeState):
            raise TypeError("run_scope_state_required")
        return state.run_scope

    def _permission(self, scope: RunScope) -> bool:
        """Read SCC use for the exact selected ServiceAccount.

        :param scope: Concrete namespace containing the selected account.
        :returns: Whether OpenShift authorizes this account to use the SCC.
        :raises KubernetesError: If the native authorization response is ambiguous.
        """
        return _can_i(
            self.kubernetes,
            "use",
            f"{SCC_API_RESOURCE}/{self.action.constraint}",
            "--as",
            service_account_subject(scope, self.action.service_account),
            "--namespace",
            scope.namespace,
        )

    def _require_mutation_permissions(self, scope: RunScope) -> None:
        """Preflight only the rights needed to create and clean the binding.

        :param scope: Concrete namespace where the temporary binding would live.
        :raises KubernetesError: If create, delete or exact ClusterRole bind is denied.
        """
        checks = (
            (
                "scc_role_binding_create_denied",
                ("create", "rolebindings.rbac.authorization.k8s.io"),
            ),
            (
                "scc_role_binding_delete_denied",
                ("delete", "rolebindings.rbac.authorization.k8s.io"),
            ),
            (
                "scc_cluster_role_bind_denied",
                (
                    "bind",
                    "clusterroles.rbac.authorization.k8s.io/"
                    + self.action.cluster_role,
                ),
            ),
        )
        for reason, arguments in checks:
            if not _can_i(
                self.kubernetes,
                *arguments,
                "--namespace",
                scope.namespace,
            ):
                raise KubernetesError(reason)

    def run(self) -> JSONObject:
        """Ensure the selected account can use the SCC on the bound target.

        Existing permission is a verified no-op. Absence of the OpenShift SCC API
        is not applicable. When that API exists, the selected SCC and generated
        ClusterRole must exist; if permission is absent, this callback creates one
        uniquely named RoleBinding, reads it back through ``ResourceSet``, and
        verifies authorization before the next consumer callback can start.

        :returns: Existing, not-applicable, or temporary-binding evidence.
        :raises KubernetesError: If provider reads, permission preflight, apply or
            same-target authorization read-back fails.
        """  # noqa: DOC502 - provider and shared ownership helpers propagate failures.
        scope = self._scope()
        subject = service_account_subject(scope, self.action.service_account)
        base: JSONObject = {
            "verified": True,
            "constraint": self.action.constraint,
            "cluster_role": self.action.cluster_role,
            "service_account": subject,
        }
        if not _scc_api_available(self.kubernetes):
            return {
                **base,
                "action": NO_OP,
                "outcome": "scc_not_available",
                "permission_source": "not_applicable",
            }
        if (
            self.kubernetes.read(
                "SecurityContextConstraint",
                self.action.constraint,
                "",
            )
            is None
        ):
            raise KubernetesError("scc_not_found")
        if self.kubernetes.read("ClusterRole", self.action.cluster_role, "") is None:
            raise KubernetesError("scc_cluster_role_missing")
        if self._permission(scope):
            return {
                **base,
                "action": NO_OP,
                "outcome": "scc_access_ready",
                "permission_source": "existing",
            }
        self._require_mutation_permissions(scope)
        resources = scope.resources(self.kubernetes)
        self.resources = resources
        binding = resources.apply(role_binding_document(self.action, scope))
        if not self._permission(scope):
            raise KubernetesError("scc_access_readback_denied")
        return {
            **base,
            "action": APPLIED,
            "outcome": "scc_access_ready",
            "permission_source": "temporary_role_binding",
            "role_binding": binding,
        }

    def cleanup(self) -> JSONObject:
        """Remove only the RoleBinding created by this callback.

        :returns: Verified absence evidence, or an empty mapping after a no-op.
        :raises TypeError: If workflow state does not provide run-scope evidence.
        :raises KubernetesError: If owned binding deletion or absence read-back fails.
        """  # noqa: DOC502 - shared cleanup propagates its declared error.
        resources = self.resources
        if resources is None or not resources.created:
            return {}
        scope = self._scope()
        created = tuple(reversed(resources.created))
        resources.cleanup_created()
        evidence = [
            {
                "kind": kind,
                "name": name,
                "namespace": scope.namespace,
                "status": PASS,
                "absent": True,
            }
            for kind, name in created
        ]
        state = self.state
        if not isinstance(state, RunScopeState):
            raise TypeError("run_scope_state_required")
        state.cleanup_resources.extend(evidence)
        return {"resources": evidence}
