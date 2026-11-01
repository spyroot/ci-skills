"""Define substitutable repository and native push-mirror services.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional

from lib.python.core.git.refs import RefSnapshot
from lib.python.core.json_types import JSONObject


class MirrorField(StrEnum):
    """Name the stable mirror evidence and native configuration fields.

    .. attribute :: ID

        Native token ``id``.

    .. attribute :: HOST

        Native token ``host``.

    .. attribute :: PATH

        Native token ``path``.

    .. attribute :: COUNT

        Native token ``count``.

    .. attribute :: SHA256

        Native token ``sha256``.

    .. attribute :: ENABLED

        Native token ``enabled``.

    .. attribute :: AUTH_METHOD

        Native token ``auth_method``.

    .. attribute :: KEEP_DIVERGENT

        Native token ``keep_divergent_refs``.

    .. attribute :: ONLY_PROTECTED

        Native token ``only_protected_branches``.

    .. attribute :: UPDATE_STATUS

        Native token ``update_status``.

    .. attribute :: LAST_SUCCESS

        Native token ``last_successful_update_at``.

    .. attribute :: LAST_ERROR

        Native token ``last_error_present``.

    .. attribute :: URL

        Native token ``url``.
    """

    ID = "id"
    HOST = "host"
    PATH = "path"
    COUNT = "count"
    SHA256 = "sha256"
    ENABLED = "enabled"
    AUTH_METHOD = "auth_method"
    KEEP_DIVERGENT = "keep_divergent_refs"
    ONLY_PROTECTED = "only_protected_branches"
    UPDATE_STATUS = "update_status"
    LAST_SUCCESS = "last_successful_update_at"
    LAST_ERROR = "last_error_present"
    URL = "url"


class MirrorStatus(StrEnum):
    """Retain native update state without treating queued work as success.

    .. attribute :: NONE

        Native token ``none``.

    .. attribute :: SCHEDULED

        Native token ``scheduled``.

    .. attribute :: STARTED

        Native token ``started``.

    .. attribute :: FINISHED

        Native token ``finished``.

    .. attribute :: FAILED

        Native token ``failed``.
    """

    NONE = "none"
    SCHEDULED = "scheduled"
    STARTED = "started"
    FINISHED = "finished"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class GitRepository:
    """Identify one provider-returned repository and its private Git read route.

    :param id: Positive independently observed provider repository ID.
    :param host: Exact provider host.
    :param path: Provider-returned namespace/repository path.
    :param clone_url: HTTPS clone route without embedded credentials.

    .. attribute :: id
        :type: int

        Positive independently observed provider repository ID.

    .. attribute :: host
        :type: str

        Exact provider host.

    .. attribute :: path
        :type: str

        Provider-returned namespace/repository path.

    .. attribute :: clone_url
        :type: str

        HTTPS clone route without embedded credentials.
    """

    id: int
    host: str
    path: str
    clone_url: str

    def evidence(self) -> JSONObject:
        """Expose repository identity without credentials or clone arguments.

        :returns: Stable public identity fields used by plans and reports.
        """
        return {
            MirrorField.ID: self.id,
            MirrorField.HOST: self.host,
            MirrorField.PATH: self.path,
        }


@dataclass(frozen=True, slots=True)
class MirrorPolicy:
    """Declare the confirmed native ref-handling policy.

    :param keep_divergent_refs: Preserve diverging destination refs.
    :param only_protected_branches: Restrict replication to protected branches.

    .. attribute :: keep_divergent_refs
        :type: bool

        Preserve diverging destination refs.

    .. attribute :: only_protected_branches
        :type: bool

        Restrict replication to protected branches.
    """

    keep_divergent_refs: bool
    only_protected_branches: bool

    def evidence(self) -> JSONObject:
        """Return only the native policy values bound into confirmation.

        :returns: Desired native mirror policy.
        """
        return {
            MirrorField.KEEP_DIVERGENT: self.keep_divergent_refs,
            MirrorField.ONLY_PROTECTED: self.only_protected_branches,
        }


@dataclass(frozen=True, slots=True)
class PushMirror:
    """Retain redacted native mirror observations independently of credentials.

    :param id: Positive provider mirror ID.
    :param url: Destination clone URL with all user information removed.
    :param enabled: Native enabled state.
    :param policy: Native observed ref-handling policy.
    :param auth_method: Native authentication mechanism.
    :param update_status: Native queue/execution result.
    :param last_successful_update_at: Native successful-update timestamp, if any.
    :param last_error_present: Whether the provider reported an error, without text.

    .. attribute :: id
        :type: int

        Positive provider mirror ID.

    .. attribute :: url
        :type: str

        Destination clone URL with all user information removed.

    .. attribute :: enabled
        :type: bool

        Native enabled state.

    .. attribute :: policy
        :type: MirrorPolicy

        Native observed ref-handling policy.

    .. attribute :: auth_method
        :type: str

        Native authentication mechanism.

    .. attribute :: update_status
        :type: MirrorStatus

        Native queue/execution result.

    .. attribute :: last_successful_update_at
        :type: Optional[str]

        Native successful-update timestamp, if any.

    .. attribute :: last_error_present
        :type: bool

        Whether the provider reported an error, without text.
    """

    id: int
    url: str
    enabled: bool
    policy: MirrorPolicy
    auth_method: str
    update_status: MirrorStatus
    last_successful_update_at: Optional[str]
    last_error_present: bool

    def evidence(self) -> JSONObject:
        """Serialize safe native state without the provider's raw URL or error.

        :returns: Bounded mirror observations suitable for a command report.
        """
        return {
            MirrorField.ID: self.id,
            MirrorField.ENABLED: self.enabled,
            MirrorField.AUTH_METHOD: self.auth_method,
            **self.policy.evidence(),
            MirrorField.UPDATE_STATUS: self.update_status.value,
            MirrorField.LAST_SUCCESS: self.last_successful_update_at,
            MirrorField.LAST_ERROR: self.last_error_present,
        }


class RepositoryReader(ABC):
    """Read repository identity and refs through a selected provider."""

    @abstractmethod
    def repository(self, reference: str) -> Optional[GitRepository]:
        """Resolve an exact provider repository.

        :param reference: Caller-selected namespace/repository path.
        :returns: Actual repository, or None only for a verified not-found result.
        :raises NotImplementedError: Concrete providers implement this operation.
        """
        raise NotImplementedError

    @abstractmethod
    def refs(self, repository: GitRepository) -> RefSnapshot:
        """Read complete branch and tag object IDs.

        :param repository: Previously verified repository identity.
        :returns: Independent native ref observation.
        :raises NotImplementedError: Concrete providers implement this operation.
        """
        raise NotImplementedError


class MirrorDestination(RepositoryReader):
    """Extend repository reads with explicitly requested destination creation."""

    @abstractmethod
    def create(self, reference: str) -> GitRepository:
        """Create and independently read back one empty private destination.

        :param reference: Exact confirmed namespace/repository path.
        :returns: Created repository independently observed after the write.
        :raises NotImplementedError: Concrete providers implement this operation.
        """
        raise NotImplementedError


class PushMirrorSource(RepositoryReader):
    """Extend repository reads with native mirror configuration and execution."""

    @abstractmethod
    def mirrors(self) -> tuple[PushMirror, ...]:
        """Read all native mirrors for the selected source.

        :returns: Complete collection of redacted mirror states.
        :raises NotImplementedError: Concrete providers implement this operation.
        """
        raise NotImplementedError

    @abstractmethod
    def mirror(self, mirror_id: int) -> PushMirror:
        """Read the selected native mirror independently.

        :param mirror_id: Provider ID established by prior read-back.
        :returns: Current redacted native mirror observation.
        :raises NotImplementedError: Concrete providers implement this operation.
        """
        raise NotImplementedError

    @abstractmethod
    def configure(
        self,
        destination: GitRepository,
        policy: MirrorPolicy,
        username: str,
        token: str,
        observed: Optional[PushMirror],
    ) -> PushMirror:
        """Create or reconcile the exact confirmed native mirror.

        :param destination: Verified destination repository.
        :param policy: Confirmed ref-handling policy.
        :param username: Authenticated destination login.
        :param token: Private destination credential for server-side Git pushes.
        :param observed: Existing mirror snapshot, absent only for creation.
        :returns: Independently read mirror configuration after the write.
        :raises NotImplementedError: Concrete providers implement this operation.
        """
        raise NotImplementedError

    @abstractmethod
    def sync(self, mirror_id: int) -> None:
        """Request one native update without interpreting acceptance as success.

        :param mirror_id: Verified selected native mirror ID.
        :returns: None after native request acceptance.
        :raises NotImplementedError: Concrete providers implement this operation.
        """
        raise NotImplementedError
