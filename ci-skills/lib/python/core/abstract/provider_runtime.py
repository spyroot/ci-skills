"""Retain immutable provider observations for one serial workflow invocation.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass


class ProviderRoleError(ValueError):
    """A provider role or identity prerequisite is unmet."""


class ProviderRoleMismatch(ProviderRoleError):
    """The observed provider role differs from its declared requirement."""


@dataclass(frozen=True)
class ProviderRuntime:
    """Record observed identity and access, independently of declared requirements.

    :param role: Observed provider role; None means the prerequisite is unestablished.
    :param identity: Provider-reported caller identity, excluding credentials.
    :param authority: Exact provider origin or Kubernetes API server.
    :param credential_identity: Non-secret identifier of the bound authentication input.

    .. attribute :: role
        :type: str | None

        Observed provider role; initially unset.

    .. attribute :: identity
        :type: str | None

        Provider-reported caller identity, excluding credentials.

    .. attribute :: authority
        :type: str | None

        Provider origin or Kubernetes API server used for the observation.

    .. attribute :: credential_identity
        :type: str | None

        Non-secret identifier binding the observation to its authentication input.
    """

    role: str | None = None
    identity: str | None = None
    authority: str | None = None
    credential_identity: str | None = None


@dataclass(frozen=True)
class KubernetesRuntime(ProviderRuntime):
    """Expose the Kubernetes role under its provider-specific field name."""

    @property
    def cluster_role(self) -> str | None:
        """Read the observed Kubernetes role using the declaration's field name.

        :returns: The retained role, or None before authentication establishes it.
        """
        return self.role
