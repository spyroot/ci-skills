"""Resolve and own one labeled Kubernetes workflow run.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Mapping, MutableSequence, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Optional

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.kubernetes.kubernetes import (
    K8SCallback,
    KubernetesError,
    KubernetesService,
)
from lib.python.core.kubernetes.resources import (
    MANAGER_LABEL,
    MANAGER_VALUE,
    OPERATION_LABEL,
    ResourceSet,
)
from lib.python.sys.status import APPLIED, NO_OP, PASS, VERIFIED

RUN_SCOPE_PREFIX: Final = "ci-skills-run"
RUN_ID_LABEL: Final = "ci-skills.spyroot.dev/run-id"
DNS_LABEL_LIMIT: Final = 63
RUN_ID_LIMIT: Final = 32
_RUN_ID = re.compile(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?")
_DNS_LABEL = re.compile(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?")
_KIND_TOKEN = re.compile(r"[^a-z0-9-]+")


def validate_run_id(value: str) -> str:
    """Require one bounded DNS-label-safe operation identifier.

    :param value: Caller-selected or generated run identifier.
    :returns: The unchanged validated identifier.
    :raises ValueError: If the identifier cannot be carried by ownership labels.
    """
    if not value or len(value) > RUN_ID_LIMIT or _RUN_ID.fullmatch(value) is None:
        raise ValueError("run_scope_id_invalid")
    return value


def validate_namespace(value: str) -> str:
    """Require one native namespace name accepted by this workflow.

    :param value: Caller-selected or generated namespace name.
    :returns: The unchanged validated namespace.
    :raises ValueError: If the name is not one bounded DNS label.
    """
    if not value or len(value) > DNS_LABEL_LIMIT or _DNS_LABEL.fullmatch(value) is None:
        raise ValueError("run_scope_namespace_invalid")
    return value


def _dns_token(value: str, *, limit: int) -> str:
    """Normalize a resource role as one bounded DNS label token.

    :param value: Resource role such as ``tool-pod``.
    :param limit: Maximum returned character count.
    :returns: Lowercase DNS-safe token.
    :raises ValueError: If no usable token remains.
    """
    token = _KIND_TOKEN.sub("-", value.casefold()).strip("-")[:limit].rstrip("-")
    if not token:
        raise ValueError("run_scope_kind_invalid")
    return token


def _items(document: JSONValue, source: str) -> Sequence[JSONObject]:
    """Require a native Kubernetes list response.

    :param document: Provider response expected to contain ``items``.
    :param source: Stable diagnostic source name.
    :returns: Object items in provider order.
    :raises KubernetesError: If the response is malformed.
    """
    values = document.get("items") if isinstance(document, Mapping) else None
    if not isinstance(values, list) or any(
        not isinstance(item, Mapping) for item in values
    ):
        raise KubernetesError(f"run_scope_{source}_list_invalid")
    return tuple(values)


def _metadata(document: JSONObject) -> Mapping[str, JSONValue]:
    """Require object metadata used for scope resolution.

    :param document: Native Kubernetes object.
    :returns: Metadata mapping.
    :raises KubernetesError: If metadata is absent or malformed.
    """
    metadata = document.get("metadata")
    if not isinstance(metadata, Mapping):
        raise KubernetesError("run_scope_metadata_invalid")
    return metadata


def _labels(document: JSONObject) -> Mapping[str, JSONValue]:
    """Require object labels used for exact scope ownership checks.

    :param document: Native Kubernetes object.
    :returns: Metadata labels mapping.
    :raises KubernetesError: If labels are absent or malformed.
    """
    labels = _metadata(document).get("labels")
    if not isinstance(labels, Mapping):
        raise KubernetesError("run_scope_labels_invalid")
    return labels


@dataclass(frozen=True)
class ResourceIdentity:
    """Exact run-owned object retained across executor cleanup.

    :param kind: Normalized native resource kind.
    :param name: Exact resource name.
    :param namespace: Exact namespace, or empty text for cluster scope.

    .. attribute :: kind
        :type: str

        Normalized native resource kind.

    .. attribute :: name
        :type: str

        Exact resource name.

    .. attribute :: namespace
        :type: str

        Exact namespace, or empty text for cluster scope.
    """

    kind: str
    name: str
    namespace: str

    def as_dict(self) -> JSONObject:
        """Render one retained resource identity.

        :returns: Kind, name, and namespace.
        """
        return {"kind": self.kind, "name": self.name, "namespace": self.namespace}


def resource_identity(kind: str, name: str, namespace: str) -> ResourceIdentity:
    """Construct one normalized retained identity.

    :param kind: Native Kubernetes resource kind.
    :param name: Exact resource name.
    :param namespace: Exact namespace, or empty text for cluster scope.
    :returns: Resource identity with a case-normalized kind.
    """
    return ResourceIdentity(kind.casefold(), name, namespace)


def run_owned(document: JSONObject, run_id: str) -> bool:
    """Check every shared ownership label on one observed resource.

    :param document: Native resource read back immediately before mutation.
    :param run_id: Exact run identifier expected in both operation labels.
    :returns: Whether manager, operation, and run labels all match.
    :raises KubernetesError: If object metadata or labels are malformed.
    :raises ValueError: If the expected run identifier is invalid.
    """  # noqa: DOC502,DOC503 - shared validation can reject malformed inputs.
    validate_run_id(run_id)
    labels = _labels(document)
    return (
        labels.get(MANAGER_LABEL) == MANAGER_VALUE
        and labels.get(OPERATION_LABEL) == run_id
        and labels.get(RUN_ID_LABEL) == run_id
    )


@dataclass(frozen=True)
class RunScope:
    """Concrete live namespace and labels for one operation identifier.

    ``namespace`` is observed or generated independently. A later lookup never
    reconstructs it from ``run_id``.

    :param run_id: Stable operation identifier carried by ownership labels.
    :param namespace: Exact namespace containing namespaced resources.
    :param owns_namespace: Whether this run created and may remove the namespace.
    :param labels: Immutable labels placed on every run-owned resource.

    .. attribute :: run_id
        :type: str

        Stable operation identifier carried by ownership labels.

    .. attribute :: namespace
        :type: str

        Exact observed or generated namespace.

    .. attribute :: owns_namespace
        :type: bool

        Whether the run created and may remove the namespace.

    .. attribute :: labels
        :type: Mapping[str, str]

        Immutable labels placed on every run-owned resource.
    """

    run_id: str
    namespace: str
    owns_namespace: bool
    labels: Mapping[str, str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate identities and construct immutable lookup labels.

        :raises ValueError: If the run or namespace is not a valid DNS label.
        """  # noqa: DOC502 - shared identity validators raise ValueError.
        validate_run_id(self.run_id)
        validate_namespace(self.namespace)
        object.__setattr__(
            self,
            "labels",
            MappingProxyType({RUN_ID_LABEL: self.run_id}),
        )

    @classmethod
    def new(
        cls,
        *,
        namespace: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> RunScope:
        """Create a fresh scope without provider access.

        :param namespace: Existing caller-selected namespace, when supplied.
        :param run_id: Caller-selected correlation identifier, when supplied.
        :returns: Scope with an independently generated namespace when needed.
        """
        identifier = validate_run_id(run_id or uuid.uuid4().hex)
        if namespace:
            return cls(identifier, validate_namespace(namespace), False)
        namespace_token = uuid.uuid4().hex[:20]
        return cls(identifier, f"{RUN_SCOPE_PREFIX}-{namespace_token}", True)

    @classmethod
    def find_all(cls, service: KubernetesService, run_id: str) -> tuple[RunScope, ...]:
        """Resolve every live scope by ownership labels, without name inference.

        :param service: Verified Kubernetes transport.
        :param run_id: Exact operation identifier returned by preparation.
        :returns: Observed scopes ordered by exact namespace.
        :raises KubernetesError: If a provider list or object is malformed.
        :raises ValueError: If the identifier is invalid.
        """  # noqa: DOC503 - validate_run_id raises the declared ValueError.
        validate_run_id(run_id)
        selector = ",".join(
            (
                f"{MANAGER_LABEL}={MANAGER_VALUE}",
                f"{OPERATION_LABEL}={run_id}",
                f"{RUN_ID_LABEL}={run_id}",
            )
        )
        namespaces = _items(
            service.json("get", "namespaces", "-l", selector, "-o", "json"),
            "namespace",
        )
        pods = _items(
            service.json(
                "get",
                "pods",
                "--all-namespaces",
                "-l",
                selector,
                "-o",
                "json",
            ),
            "pod",
        )
        configmaps = _items(
            service.json(
                "get",
                "configmaps",
                "--all-namespaces",
                "-l",
                selector,
                "-o",
                "json",
            ),
            "configmap",
        )
        owned_namespaces = {str(_metadata(item).get("name")) for item in namespaces}
        resource_namespaces = {
            str(_metadata(item).get("namespace")) for item in (*pods, *configmaps)
        }
        candidates = (owned_namespaces | resource_namespaces) - {"", "None"}
        if "None" in owned_namespaces or "" in owned_namespaces:
            raise KubernetesError("run_scope_namespace_identity_invalid")
        return tuple(
            cls(run_id, namespace, namespace in owned_namespaces)
            for namespace in sorted(candidates)
        )

    @classmethod
    def find(cls, service: KubernetesService, run_id: str) -> Optional[RunScope]:
        """Resolve one unique live scope by labels.

        :param service: Verified Kubernetes transport.
        :param run_id: Exact operation identifier returned by preparation.
        :returns: Unique observed scope, or ``None`` when nothing remains.
        :raises KubernetesError: If labels resolve to multiple namespaces.
        :raises ValueError: If the identifier is invalid.
        """  # noqa: DOC503 - find_all validates the run identifier.
        scopes = cls.find_all(service, run_id)
        if len(scopes) > 1:
            raise KubernetesError("run_scope_namespace_ambiguous")
        return scopes[0] if scopes else None

    @classmethod
    def resolve(cls, service: KubernetesService, run_id: str) -> RunScope:
        """Resolve one required live scope by labels.

        :param service: Verified Kubernetes transport.
        :param run_id: Exact operation identifier returned by preparation.
        :returns: Unique observed scope.
        :raises KubernetesError: If the scope is absent or ambiguous.
        """
        scope = cls.find(service, run_id)
        if scope is None:
            raise KubernetesError("run_scope_not_found")
        return scope

    def name(self, kind: str) -> str:
        """Derive one stable resource name within this run.

        :param kind: Resource role such as ``tool-pod``.
        :returns: DNS-safe name unique to this run and role.
        """
        suffix = hashlib.sha256(self.run_id.encode("utf-8")).hexdigest()[:16]
        available = DNS_LABEL_LIMIT - len(RUN_SCOPE_PREFIX) - len(suffix) - 2
        token = _dns_token(kind, limit=available)
        return f"{RUN_SCOPE_PREFIX}-{token}-{suffix}"

    def resources(self, service: KubernetesService) -> ResourceSet:
        """Create a namespaced ownership tracker for this run.

        :param service: Verified Kubernetes transport.
        :returns: Tracker bound to this run and its concrete namespace.
        """
        return ResourceSet(service, self.namespace, self.run_id)

    def cluster_resources(self, service: KubernetesService) -> ResourceSet:
        """Create a cluster-scoped ownership tracker for this run.

        :param service: Verified Kubernetes transport.
        :returns: Tracker bound to this run without a namespace selection.
        """
        return ResourceSet(service, "", self.run_id)

    def as_dict(self) -> JSONObject:
        """Render concrete scope identity and ownership.

        :returns: Run identifier, namespace, and namespace ownership flag.
        """
        return {
            "run_id": self.run_id,
            "namespace": self.namespace,
            "owns_namespace": self.owns_namespace,
        }


def namespace_document(scope: RunScope) -> JSONObject:
    """Render an owned generated namespace.

    :param scope: Run scope whose namespace ownership is already decided.
    :returns: Native Namespace document carrying lookup labels.
    :raises ValueError: If called for a caller-owned namespace.
    """
    if not scope.owns_namespace:
        raise ValueError("selected_namespace_not_owned")
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": scope.namespace,
            "labels": dict(scope.labels),
        },
    }


@dataclass(frozen=True)
class RunScopeAction(SkillAction):
    """Open one concrete generated or caller-selected run scope.

    :param scope: Candidate scope created independently of provider access.

    .. attribute :: scope
        :type: RunScope

        Candidate scope created independently of provider access.
    """

    scope: RunScope


@dataclass(frozen=True)
class ResolveRunScopeAction(SkillAction):
    """Select all live scopes carrying one exact run identifier.

    :param run_id: Exact run identifier to resolve from live labels.

    .. attribute :: run_id
        :type: str

        Exact run identifier to resolve from live labels.
    """

    run_id: str

    def __post_init__(self) -> None:
        """Validate the exact label value before provider access.

        :raises ValueError: If the identifier is not DNS-label-safe.
        """  # noqa: DOC502 - validate_run_id raises the declared ValueError.
        validate_run_id(self.run_id)


@dataclass
class RunScopeState(DeliveryState):
    """Retain resolved scope ownership across composed Kubernetes callbacks.

    .. attribute :: run_scope
        :type: RunScope

        Concrete scope opened for the current consumer operation.

    .. attribute :: found_scopes
        :type: Sequence[RunScope]

        Exact live scopes returned by the latest labeled lookup.

    .. attribute :: resolved_run_id
        :type: Optional[str]

        Run identifier that produced ``found_scopes``, or absent before lookup.

    .. attribute :: scope_reused
        :type: bool

        Whether opening selected a scope observed before this callback.

    .. attribute :: retained_resources
        :type: set[ResourceIdentity]

        Run-owned resources preserved after complete verification.

    .. attribute :: cleanup_resources
        :type: MutableSequence[JSONObject]

        Independently verified cleanup evidence.
    """

    run_scope: RunScope = field(init=False)
    found_scopes: Sequence[RunScope] = field(default_factory=tuple)
    resolved_run_id: Optional[str] = None
    scope_reused: bool = False
    retained_resources: set[ResourceIdentity] = field(default_factory=set)
    cleanup_resources: MutableSequence[JSONObject] = field(default_factory=list)


class ResolveRunScopes(K8SCallback[ResolveRunScopeAction]):
    """Resolve every namespace carrying one exact run identifier."""

    def run(self) -> JSONObject:
        """Read labeled scopes and retain their exact lookup provenance.

        :returns: Verified ordered scope identities, including an empty sequence.
        :raises TypeError: If the workflow state cannot retain scope evidence.
        :raises KubernetesError: If provider lists or object labels are malformed.
        """  # noqa: DOC503 - provider list validation propagates KubernetesError.
        state = self.state
        if not isinstance(state, RunScopeState):
            raise TypeError("run_scope_state_required")
        scopes = RunScope.find_all(self.kubernetes, self.action.run_id)
        state.resolved_run_id = self.action.run_id
        state.found_scopes = scopes
        return {
            "action": VERIFIED,
            "verified": True,
            "run_id": self.action.run_id,
            "scopes": [scope.as_dict() for scope in scopes],
        }


class OpenRunScope(K8SCallback[RunScopeAction]):
    """Reuse one resolved scope or open one concrete candidate namespace."""

    def __init__(self, action: RunScopeAction):
        """Retain the candidate and initialize empty namespace ownership.

        :param action: Concrete candidate scope and optional consumer role.
        """
        super().__init__(action)
        self.cluster_resources: Optional[ResourceSet] = None

    def _selected_scope(self, found: RunScope) -> RunScope:
        """Preserve a caller-selected namespace without adopting live ownership.

        :param found: Scope observed for the candidate run identifier.
        :returns: The unchanged caller-selected candidate when identities agree.
        :raises KubernetesError: If namespace or ownership conflicts with selection.
        """
        candidate = self.action.scope
        if found.namespace != candidate.namespace:
            raise KubernetesError("run_scope_namespace_mismatch")
        if found.owns_namespace:
            raise KubernetesError("run_scope_selected_namespace_owned")
        return candidate

    def _resolved(self) -> tuple[RunScope, ...]:
        """Read only lookup evidence produced for this action's run identifier.

        :returns: Ordered scopes from the matching prior lookup, or an empty tuple.
        :raises TypeError: If the workflow state cannot retain scope evidence.
        :raises KubernetesError: If lookup evidence is stale or internally mismatched.
        """
        state = self.state
        if not isinstance(state, RunScopeState):
            raise TypeError("run_scope_state_required")
        candidate = self.action.scope
        if (
            state.resolved_run_id is not None
            and state.resolved_run_id != candidate.run_id
        ):
            raise KubernetesError("run_scope_lookup_action_mismatch")
        if any(scope.run_id != candidate.run_id for scope in state.found_scopes):
            raise KubernetesError("run_scope_lookup_action_mismatch")
        return tuple(state.found_scopes)

    def run(self) -> JSONObject:
        """Open a selected namespace or create and re-resolve one owned namespace.

        :returns: Concrete scope identity, reuse status, and namespace action.
        :raises TypeError: If the workflow state cannot retain scope evidence.
        :raises KubernetesError: If lookup is ambiguous, selection conflicts, the
            namespace is absent, or post-create lookup is not uniquely this scope.
        """
        state = self.state
        if not isinstance(state, RunScopeState):
            raise TypeError("run_scope_state_required")
        candidate = self.action.scope
        found = self._resolved()
        if len(found) > 1:
            raise KubernetesError("run_scope_namespace_ambiguous")
        reused = bool(found)
        if found:
            scope = (
                self._selected_scope(found[0])
                if not candidate.owns_namespace
                else found[0]
            )
            resource_action = NO_OP
        elif not candidate.owns_namespace:
            if self.kubernetes.read("Namespace", candidate.namespace, "") is None:
                raise KubernetesError("selected_namespace_missing")
            scope = candidate
            resource_action = NO_OP
        else:
            resources = candidate.cluster_resources(self.kubernetes)
            self.cluster_resources = resources
            evidence = resources.apply(namespace_document(candidate))
            resource_action = str(evidence["action"])
            observed = RunScope.find_all(self.kubernetes, candidate.run_id)
            if len(observed) != 1 or observed[0] != candidate:
                raise KubernetesError("run_scope_post_create_not_unique")
            scope = candidate
            state.resolved_run_id = candidate.run_id
            state.found_scopes = observed
        state.run_scope = scope
        state.scope_reused = reused
        return {
            "action": resource_action,
            "verified": True,
            "reused": reused,
            **scope.as_dict(),
        }

    def cleanup(self) -> JSONObject:
        """Remove only a namespace created by this callback unless retained.

        :returns: Verified removed and retained resource identities.
        :raises TypeError: If the workflow state cannot retain scope evidence.
        :raises KubernetesError: If created namespace cleanup cannot prove absence.
        """  # noqa: DOC503 - owned cleanup propagates KubernetesError.
        state = self.state
        if not isinstance(state, RunScopeState):
            raise TypeError("run_scope_state_required")
        removed: list[JSONObject] = []
        resources = self.cluster_resources
        if resources is not None:
            selected = [
                (kind, name)
                for kind, name in reversed(resources.created)
                if resource_identity(kind, name, "") not in state.retained_resources
            ]
            resources.cleanup_owned(selected)
            removed.extend(
                {
                    "kind": kind,
                    "name": name,
                    "namespace": "",
                    "status": PASS,
                    "absent": True,
                }
                for kind, name in selected
            )
        state.cleanup_resources.extend(removed)
        return {
            "resources": removed,
            "retained": [
                item.as_dict()
                for item in sorted(
                    state.retained_resources,
                    key=lambda item: (item.namespace, item.kind, item.name),
                )
            ],
        }


class RemoveScope(K8SCallback[ResolveRunScopeAction]):
    """Remove only exact run-owned namespaces from matching lookup evidence."""

    def run(self) -> JSONObject:
        """Delete labeled owned namespaces and preserve caller-selected namespaces.

        :returns: Verified namespace cleanup records or an idempotent no-op.
        :raises TypeError: If the workflow state cannot retain scope evidence.
        :raises KubernetesError: If lookup evidence is stale, ownership labels
            differ, or deletion cannot prove absence.
        """
        state = self.state
        if not isinstance(state, RunScopeState):
            raise TypeError("run_scope_state_required")
        if state.resolved_run_id != self.action.run_id or any(
            scope.run_id != self.action.run_id for scope in state.found_scopes
        ):
            raise KubernetesError("run_scope_lookup_action_mismatch")
        removed: list[JSONObject] = []
        preserved: list[JSONObject] = []
        for scope in state.found_scopes:
            if not scope.owns_namespace:
                preserved.append(scope.as_dict())
                continue
            observed = self.kubernetes.read("Namespace", scope.namespace, "")
            if observed is None:
                continue
            if not run_owned(observed, self.action.run_id):
                raise KubernetesError("run_scope_namespace_ownership_mismatch")
            resources = scope.cluster_resources(self.kubernetes)
            if resources.delete("Namespace", scope.namespace):
                removed.append(
                    {
                        "kind": "Namespace",
                        "name": scope.namespace,
                        "namespace": "",
                        "status": PASS,
                        "absent": True,
                    }
                )
        state.cleanup_resources.extend(removed)
        return {
            "action": APPLIED if removed else NO_OP,
            "verified": True,
            "run_id": self.action.run_id,
            "resources": removed,
            "preserved_scopes": preserved,
        }
