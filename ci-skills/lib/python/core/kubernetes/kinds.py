"""Kubernetes kind names and API versions shared by ci-skills modules.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from typing import Final

NAMESPACE_KIND: Final = "Namespace"
DEPLOYMENT_KIND: Final = "Deployment"
REPLICA_SET_KIND: Final = "ReplicaSet"
POD_KIND: Final = "Pod"
SECRET_KIND: Final = "Secret"
ROLE_BINDING_KIND: Final = "RoleBinding"
LEASE_KIND: Final = "Lease"

APPS_API_VERSION: Final = "apps/v1"
COORDINATION_API_VERSION: Final = "coordination.k8s.io/v1"
