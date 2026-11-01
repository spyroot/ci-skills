"""Share GitHub identity sessions without requiring a repository to exist.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Optional

from lib.python.core.cli_api import APIError, CLIAPIClient
from lib.python.core.json_types import JSONDocument, JSONObject
from lib.python.core.target import TargetError

INVALID_RESPONSE: Final = "github_response_invalid"
UNRESOLVED_CREDENTIAL: Final = "github_credential_unresolved"
SINGLE_CREDENTIAL: Final = 1


class GitHubRequestService:
    """Own the transport and private-credential behavior shared by GitHub services.

    .. attribute :: host
        :type: str

        Shared host retained for this invocation.

    .. attribute :: environment
        :type: Mapping[str, Optional[str]]

        Shared environment retained for this invocation.

    .. attribute :: api
        :type: CLIAPIClient

        Shared api retained for this invocation.
    """

    host: str
    environment: Mapping[str, Optional[str]]
    api: CLIAPIClient

    def request(
        self, method: str, endpoint: str, body: Optional[JSONObject] = None
    ) -> JSONDocument:
        """Call the shared native transport with the retained credential.

        :param method: HTTP operation required by the provider action.
        :param endpoint: Validated relative GitHub API route.
        :param body: Private JSON request fields, absent for a body-free request.
        :returns: Decoded provider object or array; empty DELETE is an empty object.
        :raises APIError: Transport, provider status or response shape is invalid.
        :raises OSError: Native process or private temporary body I/O fails.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
        response = self.api._request(self, method, endpoint, body)
        if not isinstance(response, (Mapping, Sequence)) or isinstance(response, str):
            raise APIError(INVALID_RESPONSE)
        return response

    @property
    def token(self) -> str:
        """Expose the selected credential only to a private provider boundary.

        :returns: One pinned credential, never safe for a report or command argument.
        :raises TargetError: Credential selection did not retain exactly one value.
        """
        selected = [value for value in self.environment.values() if value is not None]
        if len(selected) != SINGLE_CREDENTIAL:
            raise TargetError(UNRESOLVED_CREDENTIAL)
        return selected[0]


@dataclass(frozen=True)
class GitHubIdentitySession(GitHubRequestService):
    """Retain authenticated account access before repository lookup or creation.

    :param host: Exact selected GitHub hostname.
    :param environment: Private child credential environment.
    :param api: Shared bounded native CLI transport.
    :param identity: Login independently returned by the user endpoint.
    :param credential_source: Nonsecret credential-reference label.

    .. attribute :: host
        :type: str

        Exact selected GitHub hostname.

    .. attribute :: environment
        :type: Mapping[str, Optional[str]]

        Private child credential environment.

    .. attribute :: api
        :type: CLIAPIClient

        Shared bounded native CLI transport.

    .. attribute :: identity
        :type: str

        Login independently returned by the user endpoint.

    .. attribute :: credential_source
        :type: str

        Nonsecret credential-reference label.
    """

    host: str
    environment: Mapping[str, Optional[str]] = field(repr=False)
    api: CLIAPIClient = field(repr=False)
    identity: str
    credential_source: str
