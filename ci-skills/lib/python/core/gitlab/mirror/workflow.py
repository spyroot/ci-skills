"""Plan and verify native push mirroring through substitutable provider services.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional, Self

from lib.python.core.cli_api import APIError
from lib.python.core.git.mirroring import (
    GitRepository,
    MirrorDestination,
    MirrorPolicy,
    MirrorStatus,
    PushMirror,
    PushMirrorSource,
)
from lib.python.core.git.refs import RefSnapshot
from lib.python.core.gitlab.mirror.constants import (
    DESTINATION_PATTERN,
    KIND,
    OPERATION,
    PASSWORD_AUTH,
    POLL_SECONDS,
    Failure,
    Field,
    Outcome,
)
from lib.python.core.json_types import JSONObject
from lib.python.core.report import plan_digest


@dataclass(frozen=True, slots=True)
class MirrorIntent:
    """Retain caller intent without selecting hidden accounts or credentials.

    :param project: Explicit or configured GitLab source path or ID.
    :param destination: Explicit or configured GitHub owner/repository.
    :param create_if_absent: Whether confirmed creation of a private destination is allowed.

    .. attribute :: project
        :type: str

        Explicit or configured GitLab source path or ID.

    .. attribute :: destination
        :type: str

        Explicit or configured GitHub owner/repository.

    .. attribute :: create_if_absent
        :type: bool

        Whether confirmed creation of a private destination is allowed.
    """

    project: str
    destination: str
    create_if_absent: bool = False

    @classmethod
    def parse(cls, project: str, destination: str, create_if_absent: bool) -> Self:
        """Validate selected intent once at the command boundary.

        :param project: Exact selected GitLab path or numeric reference.
        :param destination: Exact selected owner/repository path.
        :param create_if_absent: Explicit creation intent, never inferred.
        :returns: Immutable normalized intent.
        :raises APIError: A selector is empty, malformed or ambiguously padded.
        """
        if (
            not project
            or project != project.strip()
            or re.fullmatch(DESTINATION_PATTERN, destination) is None
            or type(create_if_absent) is not bool
        ):
            raise APIError(Failure.INVALID_INTENT)
        return cls(project, destination, create_if_absent)

    def evidence(self) -> JSONObject:
        """Serialize only accepted nonsecret operation inputs.

        :returns: Stable normalized-intent fields.
        """
        return {
            Field.OPERATION: OPERATION,
            Field.PROJECT: self.project,
            Field.DESTINATION: self.destination,
            Field.CREATE_IF_ABSENT: self.create_if_absent,
        }


@dataclass(frozen=True, slots=True)
class MirrorObservation:
    """Retain the exact resource/ref state consumed by a live plan.

    :param source: Independently observed source repository.
    :param destination: Existing destination, absent only after native not-found.
    :param mirror: Existing matching native mirror, if present.
    :param source_refs: Complete source branch/tag object IDs.
    :param destination_refs: Complete destination refs or an empty absent-repository state.

    .. attribute :: source
        :type: GitRepository

        Independently observed source repository.

    .. attribute :: destination
        :type: Optional[GitRepository]

        Existing destination, absent only after native not-found.

    .. attribute :: mirror
        :type: Optional[PushMirror]

        Existing matching native mirror, if present.

    .. attribute :: source_refs
        :type: RefSnapshot

        Complete source branch/tag object IDs.

    .. attribute :: destination_refs
        :type: RefSnapshot

        Complete destination refs or an empty absent-repository state.
    """

    source: GitRepository
    destination: Optional[GitRepository]
    mirror: Optional[PushMirror]
    source_refs: RefSnapshot
    destination_refs: RefSnapshot


@dataclass(frozen=True, slots=True)
class MirrorPlan:
    """Bind caller intent, provider observations and runtime identity for confirmation.

    :param intent: Validated nonsecret request.
    :param observation: Independent current repository, mirror and ref observations.
    :param policy: Explicit desired native ref behavior.
    :param functional_digest: Measured functional bytes of this installed workflow.
    :param credential_digest: Fingerprint of the selected source/destination credentials.

    .. attribute :: intent
        :type: MirrorIntent

        Validated nonsecret request.

    .. attribute :: observation
        :type: MirrorObservation

        Independent current repository, mirror and ref observations.

    .. attribute :: policy
        :type: MirrorPolicy

        Explicit desired native ref behavior.

    .. attribute :: functional_digest
        :type: str

        Measured functional bytes of this installed workflow.

    .. attribute :: credential_digest
        :type: str

        Fingerprint of the selected source/destination credentials.
    """

    intent: MirrorIntent
    observation: MirrorObservation
    policy: MirrorPolicy
    functional_digest: str
    credential_digest: str

    @property
    def digest(self) -> str:
        """Fingerprint selection/configuration and refs, excluding transient timestamps.

        :returns: Confirmation digest stable across background status-only updates.
        """
        observed = self.observation
        mirror = observed.mirror
        return plan_digest(
            {
                Field.INTENT: self.intent.evidence(),
                Field.SOURCE: observed.source.evidence(),
                Field.DESTINATION: (
                    observed.destination.evidence() if observed.destination else None
                ),
                Field.SOURCE_REFS: observed.source_refs.digest,
                Field.DESTINATION_REFS: observed.destination_refs.digest,
                Field.MIRROR: None
                if mirror is None
                else {
                    Field.ID: mirror.id,
                    Field.URL: mirror.url,
                    Field.ENABLED: mirror.enabled,
                    Field.POLICY: mirror.policy.evidence(),
                    Field.AUTH_METHOD: mirror.auth_method,
                },
                Field.POLICY: self.policy.evidence(),
                Field.FUNCTIONAL_DIGEST: self.functional_digest,
                Field.CREDENTIAL_DIGEST: self.credential_digest,
            }
        )


@dataclass(frozen=True, slots=True)
class MirrorExecution:
    """Expose independently verified execution without provider credentials.

    :param plan: Freshly checked confirmation plan.
    :param mirror: Independently read final native mirror.
    :param source_refs: Stable independently observed source refs.
    :param destination_refs: Independently observed destination refs.
    :param action: Actual write or verified no-op.
    :param created_repository: Whether this invocation created the intended destination.
    :param created_mirror: Whether this invocation created the intended mirror.

    .. attribute :: plan
        :type: MirrorPlan

        Freshly checked confirmation plan.

    .. attribute :: mirror
        :type: PushMirror

        Independently read final native mirror.

    .. attribute :: source_refs
        :type: RefSnapshot

        Stable independently observed source refs.

    .. attribute :: destination_refs
        :type: RefSnapshot

        Independently observed destination refs.

    .. attribute :: action
        :type: Outcome

        Actual write or verified no-op.

    .. attribute :: created_repository
        :type: bool

        Whether this invocation created the intended destination.

    .. attribute :: created_mirror
        :type: bool

        Whether this invocation created the intended mirror.
    """

    plan: MirrorPlan
    mirror: PushMirror
    source_refs: RefSnapshot
    destination_refs: RefSnapshot
    action: Outcome
    created_repository: bool
    created_mirror: bool

    def evidence(self, destination: GitRepository) -> JSONObject:
        """Return one bounded logical mirror result rather than raw API payloads.

        :param destination: Final independently verified destination identity.
        :returns: Safe source/mirror/destination evidence and complete ref fingerprints.
        """
        return {
            Field.CALLBACK: KIND,
            Field.ACTION: self.action.value,
            Field.VERIFIED: True,
            Field.SOURCE: self.plan.observation.source.evidence(),
            Field.DESTINATION: destination.evidence(),
            Field.MIRROR: self.mirror.evidence(),
            Field.SOURCE_REFS: {
                Field.COUNT: len(self.source_refs.refs),
                Field.SHA256: self.source_refs.digest,
            },
            Field.DESTINATION_REFS: {
                Field.COUNT: len(self.destination_refs.refs),
                Field.SHA256: self.destination_refs.digest,
            },
            Field.VERIFIED_SOURCE_REF_COUNT: self.destination_refs.matching_count(
                self.source_refs
            ),
            Field.CREATED: {
                Field.REPOSITORY: self.created_repository,
                Field.MIRROR: self.created_mirror,
            },
        }


class MirrorEngine:
    """Own live confirmation, native mutation and independent repository verification."""

    def __init__(
        self,
        source: PushMirrorSource,
        destination: MirrorDestination,
        destination_url: str,
        policy: MirrorPolicy,
        username: str,
        token: str,
        timeout: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Bind explicit providers and replaceable bounded polling dependencies.

        :param source: Selected native source provider.
        :param destination: Selected destination provider.
        :param destination_url: Nonsecret desired destination clone URL.
        :param policy: Desired native ref policy.
        :param username: Private destination credential's authenticated login.
        :param token: Private pinned destination credential.
        :param timeout: Positive maximum polling interval in seconds.
        :param clock: Monotonic clock, replaceable for deterministic tests.
        :param sleep: Poll wait function, replaceable for deterministic tests.
        :raises ValueError: The timeout is not positive.
        """
        if timeout <= 0:
            raise ValueError(Failure.INVALID_TIMEOUT)
        self.source = source
        self.destination = destination
        self.destination_url = destination_url
        self.policy = policy
        self.username = username
        self._token = token
        self.timeout = timeout
        self.clock = clock
        self.sleep = sleep
        self.mutated: Optional[bool] = False
        self.created_repository = False
        self.created_mirror = False

    def plan(
        self, intent: MirrorIntent, functional_digest: str, credential_digest: str
    ) -> MirrorPlan:
        """Read exact current state without any provider mutation.

        :param intent: Validated source/destination selection.
        :param functional_digest: Measured installed workflow identity.
        :param credential_digest: Fingerprint of selected credentials.
        :returns: Live plan binding current repositories, refs and mirror configuration.
        :raises APIError: Source/destination selection, access or observations fail.
        :raises OSError: Native provider or Git execution fails.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
        source = self.source.repository(intent.project)
        if source is None:
            raise APIError(Failure.SOURCE_MISSING)
        destination = self.destination.repository(intent.destination)
        if destination is None and not intent.create_if_absent:
            raise APIError(Failure.DESTINATION_MISSING)
        url = destination.clone_url if destination else self.destination_url
        matches = tuple(
            mirror
            for mirror in self.source.mirrors()
            if mirror.url.casefold() == url.casefold()
        )
        if len(matches) > 1:
            raise APIError(Failure.AMBIGUOUS)
        observation = MirrorObservation(
            source,
            destination,
            matches[0] if matches else None,
            self.source.refs(source),
            self.destination.refs(destination) if destination else RefSnapshot(()),
        )
        return MirrorPlan(
            intent, observation, self.policy, functional_digest, credential_digest
        )

    def _configured(self, mirror: Optional[PushMirror]) -> bool:
        """Determine whether a selected mirror already has the confirmed policy.

        :param mirror: Independently observed existing native mirror.
        :returns: Whether enabled state, auth mechanism and ref policy match.
        """
        return (
            mirror is not None
            and mirror.enabled
            and mirror.policy == self.policy
            and mirror.auth_method == PASSWORD_AUTH
        )

    def _successful(self, mirror: PushMirror) -> bool:
        """Require actual native success rather than accepted or queued work.

        :param mirror: Independent current native observation.
        :returns: Whether native status and successful-update evidence are consistent.
        """
        return (
            self._configured(mirror)
            and mirror.update_status == MirrorStatus.FINISHED
            and bool(mirror.last_successful_update_at)
            and not mirror.last_error_present
        )

    def apply(self, plan: MirrorPlan, confirmation: str) -> MirrorExecution:
        """Re-read confirmation state, apply once and independently prove replication.

        :param plan: Prior live plan whose immutable inputs will be re-observed.
        :param confirmation: Exact digest displayed by live planning.
        :returns: Verified execution with explicit write ownership or no-op.
        :raises APIError: Confirmation is stale, mutation fails or read-back cannot prove success.
        :raises OSError: Native I/O fails; mutation uncertainty remains in this engine.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
        self.mutated = False
        fresh = self.plan(plan.intent, plan.functional_digest, plan.credential_digest)
        if (
            not confirmation
            or confirmation != plan.digest
            or confirmation != fresh.digest
        ):
            raise APIError(Failure.STALE_PLAN)
        observed = fresh.observation
        mirror = observed.mirror
        destination = observed.destination
        if (
            mirror is not None
            and destination is not None
            and self._successful(mirror)
            and observed.destination_refs.matches(observed.source_refs)
        ):
            return MirrorExecution(
                fresh,
                mirror,
                observed.source_refs,
                observed.destination_refs,
                Outcome.NO_OP,
                False,
                False,
            )
        if destination is None:
            self.mutated = None
            destination = self.destination.create(fresh.intent.destination)
            self.mutated = True
            self.created_repository = True
        if not self._configured(mirror):
            self.mutated = None
            created = mirror is None
            mirror = self.source.configure(
                destination, self.policy, self.username, self._token, mirror
            )
            self.mutated = True
            self.created_mirror = created
        if mirror is None:
            raise APIError(Failure.REF_MISMATCH)
        self.mutated = None
        self.source.sync(mirror.id)
        self.mutated = True
        final_mirror, final_source, final_destination = self._verify(
            mirror.id,
            observed.source,
            destination,
            observed.source_refs,
            observed.mirror.last_successful_update_at if observed.mirror else None,
        )
        return MirrorExecution(
            fresh,
            final_mirror,
            final_source,
            final_destination,
            Outcome.APPLIED,
            self.created_repository,
            self.created_mirror,
        )

    def _verify(
        self,
        mirror_id: int,
        source: GitRepository,
        destination: GitRepository,
        expected: RefSnapshot,
        previous_success: Optional[str],
    ) -> tuple[PushMirror, RefSnapshot, RefSnapshot]:
        """Bound native polling and require stable source plus matching destination refs.

        :param mirror_id: Exact native mirror selected by confirmed execution.
        :param source: Verified source repository.
        :param destination: Verified destination repository.
        :param expected: Source ref snapshot bound by confirmation.
        :param previous_success: Prior native success timestamp; a sync must advance it.
        :returns: Native success and independent stable source/destination observations.
        :raises APIError: Native failure, changed source or incomplete proof at the deadline.
        :raises OSError: Provider or native Git read fails.
        """
        deadline = self.clock() + self.timeout
        while True:
            observed = self.source.mirror(mirror_id)
            if observed.update_status == MirrorStatus.FAILED:
                raise APIError(Failure.NATIVE_FAILURE)
            if (
                self._successful(observed)
                and observed.last_successful_update_at != previous_success
            ):
                destination_refs = self.destination.refs(destination)
                source_refs = self.source.refs(source)
                if source_refs.digest != expected.digest:
                    raise APIError(Failure.SOURCE_CHANGED)
                if destination_refs.matches(source_refs):
                    return observed, source_refs, destination_refs
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise APIError(Failure.TIMEOUT)
            self.sleep(min(POLL_SECONDS, remaining))
