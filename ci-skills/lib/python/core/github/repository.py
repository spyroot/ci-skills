"""Read and optionally create GitHub mirror destinations through shared access.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from enum import StrEnum
from http import HTTPMethod
from typing import Final, Optional
from urllib.parse import quote, urlsplit

from lib.python.core.cli_api import APIError
from lib.python.core.git.mirroring import GitRepository, MirrorDestination
from lib.python.core.git.refs import RefSnapshot
from lib.python.core.git.remote import GitRemoteReader
from lib.python.core.github.session import GitHubIdentitySession
from lib.python.core.json_types import json_integer, json_object, json_string

REPOSITORY_ENDPOINT: Final = "repos/{reference}"
OWNER_ENDPOINT: Final = "users/{owner}"
USER_CREATE_ENDPOINT: Final = "user/repos"
ORGANIZATION_CREATE_ENDPOINT: Final = "orgs/{owner}/repos"
NOT_FOUND: Final = "provider_404"
INVALID_REPOSITORY: Final = "github_repository_mismatch"
INVALID_OWNER: Final = "github_destination_owner_unavailable"
CREATE_UNVERIFIED: Final = "github_repository_create_unverified"
SOURCE_PRIVATE: Final = True
NO_AUTO_INIT: Final = False
HTTPS_SCHEME: Final = "https"
PATH_SEPARATOR: Final = "/"
USER_OWNER: Final = "User"
ORGANIZATION_OWNER: Final = "Organization"


class RepositoryField(StrEnum):
    """Name native repository and owner response/request fields.

    .. attribute :: ID

        Native token ``id``.

    .. attribute :: FULL_NAME

        Native token ``full_name``.

    .. attribute :: CLONE_URL

        Native token ``clone_url``.

    .. attribute :: PRIVATE

        Native token ``private``.

    .. attribute :: NAME

        Native token ``name``.

    .. attribute :: LOGIN

        Native token ``login``.

    .. attribute :: TYPE

        Native token ``type``.

    .. attribute :: AUTO_INIT

        Native token ``auto_init``.
    """

    ID = "id"
    FULL_NAME = "full_name"
    CLONE_URL = "clone_url"
    PRIVATE = "private"
    NAME = "name"
    LOGIN = "login"
    TYPE = "type"
    AUTO_INIT = "auto_init"


class GitHubRepositoryService(MirrorDestination):
    """Use identity-only authentication without requiring runner permissions."""

    def __init__(self, session: GitHubIdentitySession, reader: GitRemoteReader) -> None:
        """Retain private provider and native-ref read boundaries.

        :param session: Existing exact-host authenticated GitHub identity.
        :param reader: Shared bounded Git remote reader.
        """
        self.session = session
        self.reader = reader

    def repository(self, reference: str) -> Optional[GitRepository]:
        """Read the exact selected repository without inventing a destination.

        :param reference: Caller-selected owner/repository.
        :returns: Verified repository, or None for native not-found only.
        :raises APIError: Access, native response identity or clone route is invalid.
        :raises OSError: Existing provider request I/O fails.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
        try:
            payload = json_object(
                self.session.request(
                    HTTPMethod.GET,
                    REPOSITORY_ENDPOINT.format(
                        reference=quote(reference, safe=PATH_SEPARATOR)
                    ),
                )
            )
        except APIError as exc:
            if exc.reason == NOT_FOUND:
                return None
            raise
        identity = json_integer(payload.get(RepositoryField.ID))
        path = json_string(payload.get(RepositoryField.FULL_NAME))
        clone_url = json_string(payload.get(RepositoryField.CLONE_URL))
        parsed = urlsplit(clone_url)
        if (
            identity <= 0
            or path.casefold() != reference.casefold()
            or parsed.scheme != HTTPS_SCHEME
            or parsed.netloc != self.session.host
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise APIError(INVALID_REPOSITORY)
        return GitRepository(identity, self.session.host, path, clone_url)

    def create(self, reference: str) -> GitRepository:
        """Create an empty private repository under the explicitly selected owner.

        :param reference: Exact confirmed owner/repository path.
        :returns: Created repository independently read back with private visibility.
        :raises APIError: Owner cannot be used, creation fails or read-back mismatches.
        :raises OSError: Provider request I/O fails.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
        owner, name = reference.split(PATH_SEPARATOR)
        payload = json_object(
            self.session.request(
                HTTPMethod.GET, OWNER_ENDPOINT.format(owner=quote(owner, safe=""))
            )
        )
        owner_type = json_string(payload.get(RepositoryField.TYPE))
        login = json_string(payload.get(RepositoryField.LOGIN))
        if login.casefold() != owner.casefold():
            raise APIError(INVALID_OWNER)
        endpoints = {
            USER_OWNER: USER_CREATE_ENDPOINT,
            ORGANIZATION_OWNER: ORGANIZATION_CREATE_ENDPOINT.format(
                owner=quote(owner, safe="")
            ),
        }
        if owner_type not in endpoints or (
            owner_type == USER_OWNER
            and login.casefold() != self.session.identity.casefold()
        ):
            raise APIError(INVALID_OWNER)
        self.session.request(
            HTTPMethod.POST,
            endpoints[owner_type],
            {
                RepositoryField.NAME: name,
                RepositoryField.PRIVATE: SOURCE_PRIVATE,
                RepositoryField.AUTO_INIT: NO_AUTO_INIT,
            },
        )
        observed = self.repository(reference)
        if observed is None:
            raise APIError(CREATE_UNVERIFIED)
        visibility = json_object(
            self.session.request(
                HTTPMethod.GET,
                REPOSITORY_ENDPOINT.format(
                    reference=quote(reference, safe=PATH_SEPARATOR)
                ),
            )
        )
        if visibility.get(RepositoryField.PRIVATE) is not SOURCE_PRIVATE:
            raise APIError(CREATE_UNVERIFIED)
        return observed

    def refs(self, repository: GitRepository) -> RefSnapshot:
        """Observe exact destination object IDs through the shared native reader.

        :param repository: Independently verified GitHub repository.
        :returns: Complete branch/tag ref observations.
        :raises APIError: Authentication, native read or ref parsing fails.
        :raises OSError: Native process execution fails.
        """  # noqa: DOC502 - Shared readers and transport propagate the documented failures.
        return self.reader.read(
            repository.clone_url, self.session.identity, self.session.token
        )
