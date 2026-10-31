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
from lib.python.core.gitlab.gitlab_api import create_guard
from lib.python.core.gitlab.gitlab_session import GitLabService
from lib.python.core.json_types import (
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
PASSWORD_AUTH: Final = "password"
GITLAB_GIT_USERNAME: Final = "oauth2"
HTTPS_SCHEME: Final = "https"
MIRROR_SCHEMES: Final = frozenset((HTTPS_SCHEME, "http", "ssh", "git"))
PROJECT_SCOPE: Final = "project"
MIRROR_RESOURCE: Final = "remote_mirror"
PLAN_CHANGED: Final = "mirror_plan_mismatch"
INVALID_REPOSITORY: Final = "gitlab_source_repository_invalid"
INVALID_MIRROR: Final = "gitlab_mirror_response_invalid"
INVALID_COLLECTION: Final = "gitlab_mirror_collection_invalid"
CONFIGURATION_UNVERIFIED: Final = "gitlab_mirror_configuration_unverified"
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

    def configure(
        self,
        destination: GitRepository,
        policy: MirrorPolicy,
        username: str,
        token: str,
        observed: Optional[PushMirror],
    ) -> PushMirror:
        """Create or reconcile one confirmed mirror using private body transport.

        :param destination: Independently verified destination clone URL.
        :param policy: Confirmed native ref-handling policy.
        :param username: Authenticated destination login.
        :param token: Selected destination credential; never a reportable value.
        :param observed: Discovered matching mirror, absent only for creation.
        :returns: Configuration independently read back after the write.
        :raises APIError: Provider write, returned identity or read-back mismatches.
        :raises OSError: Provider transport or temporary private-body cleanup fails.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
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
        body = {
            MirrorField.URL: secret_url,
            MirrorField.AUTH_METHOD: PASSWORD_AUTH,
            MirrorField.ENABLED: True,
            **policy.evidence(),
        }
        if observed is None:
            with create_guard(
                self.service.session.host,
                PROJECT_SCOPE,
                self.service.target_id,
                MIRROR_RESOURCE,
                destination.clone_url,
            ) as guard:
                matching = tuple(
                    item
                    for item in self.mirrors()
                    if item.url.casefold() == destination.clone_url.casefold()
                )
                if matching:
                    guard.clear()
                    raise APIError(PLAN_CHANGED)
                guard.reconcile_absent()
                guard.mark_pending()
                payload = self.service.api.post_json(
                    self.service.session,
                    MIRRORS_ENDPOINT.format(project=self.service.target_id),
                    body,
                )
                identity = json_integer(json_object(payload).get(MirrorField.ID))
                current = self.mirror(identity)
                guard.clear()
        else:
            identity = observed.id
            self.service.api.put_json(
                self.service.session,
                MIRROR_ENDPOINT.format(project=self.service.target_id, mirror=identity),
                body,
            )
        current = self.mirror(identity)
        if (
            current.url.casefold() != destination.clone_url.casefold()
            or not current.enabled
            or current.policy != policy
            or current.auth_method != PASSWORD_AUTH
        ):
            raise APIError(CONFIGURATION_UNVERIFIED)
        return current

    def sync(self, mirror_id: int) -> None:
        """Request a native update without reporting success before ref read-back.

        :param mirror_id: Selected mirror identifier confirmed by configuration reads.
        :returns: None when the native request was accepted.
        :raises APIError: Native update request fails or its outcome is uncertain.
        :raises OSError: Native transport execution fails.
        """  # noqa: DOC502 - Shared readers and transport propagate the documented failures.
        self.service.api.post_json(
            self.service.session,
            SYNC_ENDPOINT.format(project=self.service.target_id, mirror=mirror_id),
            {},
        )
