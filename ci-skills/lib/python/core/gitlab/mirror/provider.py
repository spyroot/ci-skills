"""Implement native GitLab push mirrors with redacted provider state.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Optional
from urllib.parse import quote, urlsplit, urlunsplit

from lib.python.core.api_read import ApiJSONDecoder, GitProvider
from lib.python.core.catalog import GITLAB_VARIABLES
from lib.python.core.cli_api import APIError
from lib.python.core.git.mirroring import (
    GitRepository,
    MirrorField,
    MirrorPolicy,
    MirrorStatus,
    PushMirror,
    PushMirrorSource,
)
from lib.python.core.git.refs import RefSnapshot
from lib.python.core.git.remote import GitRemoteReader
from lib.python.core.gitlab.gitlab_api import CreateGuard, create_guard
from lib.python.core.gitlab.gitlab_session import GitLabService
from lib.python.core.gitlab.mirror.constants import PASSWORD_AUTH, Failure
from lib.python.core.gitlab.mirror.reconciliation import requires_replacement
from lib.python.core.json_types import (
    JSONObject,
    json_integer,
    json_object,
    json_objects,
    json_string,
)
from lib.python.core.read_tools import ReadToolError

PROJECT_ENDPOINT: Final = "projects/{project}"
MIRRORS_ENDPOINT: Final = "projects/{project}/remote_mirrors"
MIRROR_ENDPOINT: Final = "projects/{project}/remote_mirrors/{mirror}"
SYNC_ENDPOINT: Final = "projects/{project}/remote_mirrors/{mirror}/sync"
GITLAB_GIT_USERNAME: Final = "oauth2"
HTTPS_SCHEME: Final = "https"
MIRROR_SCHEMES: Final = frozenset((HTTPS_SCHEME, "http", "ssh", "git"))
PROJECT_SCOPE: Final = "project"
MIRROR_RESOURCE: Final = "remote_mirror"
SYNC_METHOD: Final = "POST"
INVALID_REPOSITORY: Final = "gitlab_source_repository_invalid"
INVALID_MIRROR: Final = "gitlab_mirror_response_invalid"
INVALID_COLLECTION: Final = "gitlab_mirror_collection_invalid"
CONFIGURATION_UNVERIFIED: Final = "gitlab_mirror_configuration_unverified"
DELETION_UNVERIFIED: Final = "gitlab_mirror_deletion_unverified"
MIRROR_NOT_FOUND: Final = "provider_404"
CREDENTIAL_UNAVAILABLE: Final = "gitlab_mirror_source_credential_unavailable"
SINGLE_VALUE: Final = 1


class SourceField(StrEnum):
    """Name native source fields not shared with mirror evidence.

    .. attribute :: PATH

        Native token ``path_with_namespace``.

    .. attribute :: CLONE_URL

        Native token ``http_url_to_repo``.

    .. attribute :: LAST_ERROR

        Native token ``last_error``.
    """

    PATH = "path_with_namespace"
    CLONE_URL = "http_url_to_repo"
    LAST_ERROR = "last_error"


class GitLabPushMirrorService(PushMirrorSource):
    """Use one existing verified GitLab session for the selected source project."""

    def __init__(self, service: GitLabService, reader: GitRemoteReader) -> None:
        """Retain verified source access and shared native ref execution.

        :param service: Existing selected-project service from access verification.
        :param reader: Shared bounded native Git reader.
        """
        self.service = service
        self.reader = reader

    def repository(self, reference: str) -> Optional[GitRepository]:
        """Read the selected source ID and native HTTPS clone route.

        :param reference: Source path or ID used during access selection.
        :returns: Verified existing source; missing sources propagate provider errors.
        :raises APIError: Source identity or clone URL is inconsistent.
        :raises OSError: Provider request execution fails.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
        session = self.service.session
        payload = json_object(
            self.service.api.get_json(
                session, PROJECT_ENDPOINT.format(project=self.service.target_id)
            )
        )
        identity = json_integer(payload.get(MirrorField.ID))
        path = json_string(payload.get(SourceField.PATH))
        clone_url = json_string(payload.get(SourceField.CLONE_URL))
        parsed = urlsplit(clone_url)
        selected = reference == str(identity) or reference == path
        if (
            identity != self.service.target_id
            or not selected
            or parsed.scheme != HTTPS_SCHEME
            or parsed.netloc != session.host
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise APIError(INVALID_REPOSITORY)
        return GitRepository(identity, session.host, path, clone_url)

    def refs(self, repository: GitRepository) -> RefSnapshot:
        """Read complete source refs using the already selected private credential.

        :param repository: Verified selected source identity.
        :returns: Complete native branch and tag object observations.
        :raises APIError: The pinned credential, native execution or ref data fails.
        :raises OSError: Native process execution fails.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
        selected = [
            self.service.session.environment.get(name)
            for name in GITLAB_VARIABLES
            if self.service.session.environment.get(name)
        ]
        if len(set(selected)) != SINGLE_VALUE:
            raise APIError(CREDENTIAL_UNAVAILABLE)
        token = selected[0]
        if token is None:
            raise APIError(CREDENTIAL_UNAVAILABLE)
        return self.reader.read(repository.clone_url, GITLAB_GIT_USERNAME, token)

    @staticmethod
    def _mirror(payload: object) -> PushMirror:
        """Decode one native mirror without retaining private URLs or error text.

        :param payload: Native object returned by the selected mirror API.
        :returns: Strongly typed redacted mirror observation.
        :raises APIError: Required identity, booleans, URL or native state is invalid.
        """
        try:
            document = json_object(payload)
            identity = json_integer(document.get(MirrorField.ID))
            raw_url = json_string(document.get(MirrorField.URL))
            parsed = urlsplit(raw_url)
            host = parsed.netloc.rsplit("@", SINGLE_VALUE)[-1]
            clean_url = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
            enabled = document.get(MirrorField.ENABLED)
            divergent = document.get(MirrorField.KEEP_DIVERGENT)
            protected = document.get(MirrorField.ONLY_PROTECTED)
            if identity <= 0 or any(
                type(item) is not bool for item in (enabled, divergent, protected)
            ):
                raise APIError(INVALID_MIRROR)
            if (
                parsed.scheme not in MIRROR_SCHEMES
                or not parsed.hostname
                or parsed.query
                or parsed.fragment
            ):
                raise APIError(INVALID_MIRROR)
            stamp = document.get(MirrorField.LAST_SUCCESS)
            if stamp is not None:
                stamp = json_string(stamp)
            return PushMirror(
                identity,
                clean_url,
                enabled,
                MirrorPolicy(divergent, protected),
                json_string(document.get(MirrorField.AUTH_METHOD)),
                MirrorStatus(json_string(document.get(MirrorField.UPDATE_STATUS))),
                stamp,
                bool(document.get(SourceField.LAST_ERROR)),
            )
        except (TypeError, ValueError) as exc:
            raise APIError(INVALID_MIRROR) from exc

    def mirrors(self) -> tuple[PushMirror, ...]:
        """Read all source mirrors using the existing native pagination decoder.

        :returns: Complete redacted collection within the shared transport limits.
        :raises APIError: Native access or complete collection decoding fails.
        :raises OSError: Provider request execution fails.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
        text = self.service.api.get_text(
            self.service.session,
            MIRRORS_ENDPOINT.format(project=self.service.target_id),
            paginate=True,
        )
        try:
            records = json_objects(
                ApiJSONDecoder(GitProvider.GITLAB, True).decode(text)
            )
        except (ReadToolError, TypeError) as exc:
            raise APIError(INVALID_COLLECTION) from exc
        return tuple(self._mirror(item) for item in records)

    def mirror(self, mirror_id: int) -> PushMirror:
        """Independently read exactly one selected native mirror.

        :param mirror_id: Previously discovered native mirror identifier.
        :returns: Redacted current native state.
        :raises APIError: Provider access or response validation fails.
        :raises OSError: Provider request execution fails.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
        payload = self.service.api.get_json(
            self.service.session,
            MIRROR_ENDPOINT.format(project=self.service.target_id, mirror=mirror_id),
        )
        observed = self._mirror(payload)
        if observed.id != mirror_id:
            raise APIError(INVALID_MIRROR)
        return observed

    def _read_configuration(
        self, destination: GitRepository, policy: MirrorPolicy, identity: int
    ) -> PushMirror:
        """Independently verify the identity and policy of one configured mirror.

        :param destination: Confirmed destination clone route without credentials.
        :param policy: Confirmed native ref-handling policy.
        :param identity: Positive mirror ID returned by creation or selected for update.
        :returns: Exact mirror after native configuration read-back succeeds.
        :raises APIError: Native read fails or configuration differs from confirmed intent.
        :raises OSError: Native provider execution fails.
        """  # noqa: DOC503 - Shared mirror reads propagate documented transport failures.
        current = self.mirror(identity)
        if (
            current.url.casefold() != destination.clone_url.casefold()
            or not current.enabled
            or current.policy != policy
            or current.auth_method != PASSWORD_AUTH
        ):
            raise APIError(CONFIGURATION_UNVERIFIED)
        return current

    @staticmethod
    def _creation_body(
        destination: GitRepository, policy: MirrorPolicy, username: str, token: str
    ) -> JSONObject:
        """Build private native creation inputs for one confirmed destination.

        :param destination: Verified HTTPS clone route without credential material.
        :param policy: Confirmed native ref-handling policy.
        :param username: Authenticated destination account login.
        :param token: Private selected credential, never reportable.
        :returns: Native POST fields sent only through private request-body transport.
        """
        parsed = urlsplit(destination.clone_url)
        secret_url = urlunsplit(
            (
                parsed.scheme,
                f"{quote(username, safe='')}:{quote(token, safe='')}@{parsed.netloc}",
                parsed.path,
                "",
                "",
            )
        )
        return {
            MirrorField.URL: secret_url,
            MirrorField.AUTH_METHOD: PASSWORD_AUTH,
            MirrorField.ENABLED: True,
            **policy.evidence(),
        }

    def _create_configuration(
        self,
        destination: GitRepository,
        policy: MirrorPolicy,
        username: str,
        token: str,
    ) -> PushMirror:
        """Create and verify the same native configuration for new and replacement mirrors.

        :param destination: Explicitly confirmed destination repository.
        :param policy: Confirmed native ref-handling policy.
        :param username: Authenticated destination login.
        :param token: Private pinned destination credential.
        :returns: Newly created mirror after independent configuration read-back.
        :raises APIError: Native creation or read-back fails.
        :raises OSError: Private-body transport or native execution fails.
        :raises TypeError: Native creation returns an invalid mirror identifier.
        """  # noqa: DOC502 - Shared transport, identity readers and read-back retain their failures.
        payload = self.service.api.post_json(
            self.service.session,
            MIRRORS_ENDPOINT.format(project=self.service.target_id),
            self._creation_body(destination, policy, username, token),
        )
        identity = json_integer(json_object(payload).get(MirrorField.ID))
        return self._read_configuration(destination, policy, identity)

    def _delete_failed(
        self, destination: GitRepository, observed: PushMirror, guard: CreateGuard
    ) -> None:
        """Delete only the confirmed failed configuration and prove exact-ID absence.

        :param destination: Confirmed selected destination clone URL.
        :param observed: Exact failed mirror bound into the accepted live plan.
        :param guard: Selected-URL guard retained until replacement read-back;
            an uncertain prior write is reconciled by read-back and requires replanning.
        :returns: None after the selected mirror independently returns native not-found.
        :raises APIError: ID, URL or native failure state changed, deletion fails,
            or independent read-back cannot prove absence.
        :raises OSError: Guard persistence or native provider execution fails.
        """  # noqa: DOC503 - Shared guard and mirror transport preserve documented failures.
        current = self.mirror(observed.id)
        if (
            current.id != observed.id
            or current.url.casefold() != observed.url.casefold()
            or current.url.casefold() != destination.clone_url.casefold()
            or not requires_replacement(current)
        ):
            raise APIError(Failure.STALE_PLAN)
        if guard.pending:
            guard.clear()
            raise APIError(Failure.STALE_PLAN)
        guard.mark_pending()
        endpoint = MIRROR_ENDPOINT.format(
            project=self.service.target_id, mirror=observed.id
        )
        self.service.api.delete_json(self.service.session, endpoint)
        try:
            self.mirror(observed.id)
        except APIError as exc:
            if exc.reason == MIRROR_NOT_FOUND:
                return
            raise
        raise APIError(DELETION_UNVERIFIED)

    def configure(
        self,
        destination: GitRepository,
        policy: MirrorPolicy,
        username: str,
        token: str,
        observed: Optional[PushMirror],
    ) -> PushMirror:
        """Create, update policy or replace the exact confirmed failed mirror.

        :param destination: Independently verified destination clone URL.
        :param policy: Confirmed native ref-handling policy.
        :param username: Authenticated destination login.
        :param token: Selected destination credential; never a reportable value.
        :param observed: Discovered matching mirror, absent only for creation;
            replacement requires its re-observed native FAILED state.
        :returns: Configuration independently read back after the write.
        :raises APIError: Provider write, returned identity or read-back mismatches.
        :raises OSError: Provider transport or temporary private-body cleanup fails.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
        if observed is not None and not requires_replacement(observed):
            self.service.api.put_json(
                self.service.session,
                MIRROR_ENDPOINT.format(
                    project=self.service.target_id, mirror=observed.id
                ),
                {MirrorField.ENABLED: True, **policy.evidence()},
            )
            return self._read_configuration(destination, policy, observed.id)
        with create_guard(
            self.service.session.host,
            PROJECT_SCOPE,
            self.service.target_id,
            MIRROR_RESOURCE,
            destination.clone_url,
        ) as guard:
            if observed is None:
                matching = tuple(
                    item
                    for item in self.mirrors()
                    if item.url.casefold() == destination.clone_url.casefold()
                )
                if matching:
                    guard.clear()
                    raise APIError(Failure.STALE_PLAN)
                guard.reconcile_absent()
                guard.mark_pending()
            else:
                self._delete_failed(destination, observed, guard)
            current = self._create_configuration(destination, policy, username, token)
            guard.clear()
            return current

    def sync(self, mirror_id: int) -> None:
        """Request a native update without reporting success before ref read-back.

        :param mirror_id: Selected mirror identifier confirmed by configuration reads.
        :returns: None when the native request was accepted.
        :raises APIError: Native update request fails or its outcome is uncertain.
        :raises OSError: Native transport execution fails.
        """  # noqa: DOC502 - Shared readers and transport propagate the documented failures.
        self.service.api.request_no_content(
            self.service.session,
            SYNC_METHOD,
            SYNC_ENDPOINT.format(project=self.service.target_id, mirror=mirror_id),
        )
