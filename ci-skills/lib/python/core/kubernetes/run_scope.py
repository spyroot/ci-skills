"""Deterministic identities for one disposable Kubernetes workflow run.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from lib.python.core.kubernetes.kubernetes import KubernetesService
from lib.python.core.kubernetes.resources import ResourceSet

RUN_SCOPE_PREFIX: Final = "ci-skills-run"
RUN_ID_LABEL: Final = "ci-skills.spyroot.dev/run-id"
DNS_LABEL_LIMIT: Final = 63
RUN_ID_LIMIT: Final = 32
_RUN_ID = re.compile(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?")
_KIND_TOKEN = re.compile(r"[^a-z0-9-]+")


def _dns_token(value: str, *, limit: int) -> str:
    """Normalize a caller-independent resource suffix as one DNS label token.

    :param value: Resource-role text such as ``job`` or ``service-account``.
    :param limit: Maximum returned character count.
    :returns: Lowercase DNS-safe token without leading or trailing hyphens.
    :raises ValueError: If normalization produces no usable token.
    """
    token = _KIND_TOKEN.sub("-", value.casefold()).strip("-")[:limit].rstrip("-")
    if not token:
        raise ValueError("run_scope_kind_invalid")
    return token


@dataclass(frozen=True)
class RunScope:
    """Name and label every resource owned by one disposable workflow run.

    .. attribute :: run_id
        :type: str

        Generated or reconstructed DNS-safe run identifier.

    .. attribute :: namespace
        :type: str

        Deterministic namespace derived from ``run_id``.

    .. attribute :: labels
        :type: Mapping[str, str]

        Immutable labels placed on every resource in this run.
    """

    run_id: str
    namespace: str = field(init=False)
    labels: Mapping[str, str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate the stable identifier and derive immutable scope fields.

        :raises ValueError: If the identifier is empty, too long, or not DNS-safe.
        """
        if (
            not self.run_id
            or len(self.run_id) > RUN_ID_LIMIT
            or _RUN_ID.fullmatch(self.run_id) is None
        ):
            raise ValueError("run_scope_id_invalid")
        namespace = f"{RUN_SCOPE_PREFIX}-{self.run_id}"
        if len(namespace) > DNS_LABEL_LIMIT:
            raise ValueError("run_scope_namespace_too_long")
        object.__setattr__(self, "namespace", namespace)
        object.__setattr__(
            self, "labels", MappingProxyType({RUN_ID_LABEL: self.run_id})
        )

    @classmethod
    def new(cls) -> RunScope:
        """Create a fresh run scope without reading or writing persistent state.

        :returns: Scope whose random hexadecimal identifier fits Kubernetes labels.
        """
        return cls(uuid.uuid4().hex)

    @classmethod
    def from_id(cls, run_id: str) -> RunScope:
        """Reconstruct the exact names belonging to an existing run.

        :param run_id: Identifier emitted by the original workflow result.
        :returns: Scope with the same namespace, labels, and derived names.
        :raises ValueError: If the supplied identifier is not valid.
        """  # noqa: DOC502 - Construction validates the supplied identifier.
        return cls(run_id)

    def name(self, kind: str) -> str:
        """Derive one stable resource name for a role within this scope.

        :param kind: Resource role, such as ``probe`` or ``service-account``.
        :returns: DNS-safe name unique to this run and role.
        :raises ValueError: If the role has no usable DNS-label characters.
        """  # noqa: DOC502 - The shared DNS-token validator rejects invalid roles.
        available = DNS_LABEL_LIMIT - len(self.namespace) - 1
        return f"{self.namespace}-{_dns_token(kind, limit=available)}"

    def resources(self, service: KubernetesService) -> ResourceSet:
        """Create the existing tracker for this run's namespaced resources.

        :param service: Verified Kubernetes service used for apply and read-back.
        :returns: Resource tracker bound to this namespace and run identifier.
        """
        return ResourceSet(service, self.namespace, self.run_id)

    def cluster_resources(self, service: KubernetesService) -> ResourceSet:
        """Create the existing tracker for cluster-scoped run resources.

        :param service: Verified Kubernetes service used for apply and read-back.
        :returns: Resource tracker with no namespace and this run identifier.
        """
        return ResourceSet(service, "", self.run_id)
