"""Typed namespace and naming evidence shared by every runner workflow.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

from lib.python.core.json_types import JSONObject
from lib.python.core.runner_identity import RepositoryIdentity, ResolvedName
from lib.python.sys.status import BLOCKED, PASS

SCOPE_CALLBACK: Final = "RunnerScopes"
PENDING: Final = "PENDING"
NOT_REQUIRED: Final = "NOT_REQUIRED"
UNKNOWN_SOURCE: Final = "unknown"


class ScopePurpose(StrEnum):
    """Resource roles exposed by the shared runner scope schema."""

    BUILD = "build"
    RUNNER = "runner"
    MANAGER = "manager"
    JOB = "job"
    PROBE = "probe"


class ScopeSource(StrEnum):
    """How a namespace was selected, independently of its ownership."""

    EXPLICIT = "explicit"
    TOOLBOX_DEFAULT = "toolbox_default"
    EXISTING_RUN_SCOPE = "existing_run_scope"
    OPERATION_CREATED = "operation_created"


class ScopeOwnership(StrEnum):
    """Namespace ownership established by independent observation."""

    BORROWED = "borrowed"
    OPERATION_OWNED = "operation_owned"
    UNKNOWN = "unknown"


class ScopeState(StrEnum):
    """Separate planned values from actual presence and absence."""

    PLANNED = "planned"
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class ScopeRetention(StrEnum):
    """Namespace lifetime independent of workload lifetime."""

    TEMPORARY = "temporary"
    RETAINED = "retained"
    PRESERVED = "preserved"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ScopeObservation:
    """Retain one namespace observation without provider-specific payloads.

    :param purpose: Resource role using the namespace.
    :param namespace: Actual selected namespace.
    :param source: Origin of the selection.
    :param uid: Observed immutable namespace identity, absent in an offline plan.
    :param ownership: Independently established namespace ownership.
    :param state: Planned, present, absent, or unknown.
    :param retention: Intended resource lifetime.
    :param cleanup_status: Required cleanup result or its pending/not-required state.
    :param remaining: Exact names whose cleanup remains unverified.
    """

    purpose: ScopePurpose
    namespace: str
    source: ScopeSource
    uid: str | None = None
    ownership: ScopeOwnership = ScopeOwnership.UNKNOWN
    state: ScopeState = ScopeState.PLANNED
    retention: ScopeRetention = ScopeRetention.UNKNOWN
    cleanup_status: str = NOT_REQUIRED
    remaining: tuple[str, ...] = ()

    def document(self) -> JSONObject:
        """Render the installed shared scope contract.

        :returns: Finite, nonsecret observation with explicit unknown values.
        """
        return {
            "purpose": self.purpose,
            "namespace": self.namespace,
            "uid": self.uid,
            "source": self.source,
            "ownership": self.ownership,
            "state": self.state,
            "retention": self.retention,
            "cleanup": {
                "status": self.cleanup_status,
                "remaining": list(self.remaining),
            },
        }

    def removed(self) -> ScopeObservation:
        """Preserve the observed UID while recording verified namespace absence.

        :returns: New immutable observation proving completed cleanup.
        """
        return replace(self, state=ScopeState.ABSENT, cleanup_status=PASS, remaining=())


def scope_record(
    repository: RepositoryIdentity,
    name: ResolvedName | None,
    description: ResolvedName | None,
    scopes: Sequence[ScopeObservation],
) -> JSONObject:
    """Render one shared callback record from already collected observations.

    :param repository: Consumer repository identity and provenance.
    :param name: Resolved workload name when this interface has one.
    :param description: Resolved provider runner description when applicable.
    :param scopes: Namespace observations produced by scope callbacks.
    :returns: Schema-declared callback, identity, and scope evidence.
    """
    return {
        "callback": SCOPE_CALLBACK,
        "verified": all(
            scope.state in (ScopeState.PRESENT, ScopeState.ABSENT)
            and scope.cleanup_status != BLOCKED
            for scope in scopes
        ),
        "identity": {
            "repository": repository.reference,
            "source": repository.source,
            "runner_name": name.value if name else None,
            "name_source": name.source if name else UNKNOWN_SOURCE,
            "runner_description": description.value if description else None,
            "description_source": description.source if description else UNKNOWN_SOURCE,
        },
        "scopes": [scope.document() for scope in scopes],
    }
