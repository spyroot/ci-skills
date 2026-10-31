"""Read authenticated remote refs through the shared bounded subprocess runner.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Final, Optional
from urllib.parse import urlsplit

from lib.python.core.cli_api import APIError, CommandRunner, RequestDeadline
from lib.python.core.git.refs import RefSnapshot
from lib.python.core.runtime import error_class, run_command_bounded

GIT_EXECUTABLE: Final = "git"
LS_REMOTE_ARGUMENTS: Final = ("ls-remote", "--refs", "--heads", "--tags")
HTTPS_SCHEME: Final = "https"
AUTHORIZATION_PREFIX: Final = "Authorization: Basic "
TOKEN_SEPARATOR: Final = ":"
PRIVATE_CONFIG_VALUES: Final = (
    ("http.followRedirects", "false"),
    ("credential.helper", ""),
)
GIT_CONFIG_COUNT: Final = "GIT_CONFIG_COUNT"
GIT_CONFIG_KEY: Final = "GIT_CONFIG_KEY_"
GIT_CONFIG_VALUE: Final = "GIT_CONFIG_VALUE_"
GIT_TERMINAL_PROMPT: Final = "GIT_TERMINAL_PROMPT"
NO_PROMPT: Final = "0"
TRACE_VARIABLES: Final = (
    "GIT_TRACE",
    "GIT_TRACE_CURL",
    "GIT_CURL_VERBOSE",
    "GIT_TRACE_PACKET",
    "GIT_TRACE_SETUP",
    "GIT_TRACE2",
    "GIT_TRACE2_EVENT",
    "GIT_TRACE2_PERF",
)
INVALID_REMOTE: Final = "git_remote_invalid"
INVALID_CREDENTIAL: Final = "git_remote_credential_invalid"
EXTRA_HEADER_TEMPLATE: Final = "http.{url}.extraheader"


class GitRemoteReader:
    """Read refs with explicit private credentials and no repository download."""

    def __init__(
        self,
        timeout: int,
        runner: CommandRunner = run_command_bounded,
        *,
        deadline: Optional[RequestDeadline] = None,
    ) -> None:
        """Retain replaceable process execution and its positive time bound.

        :param timeout: Maximum seconds for each native remote observation.
        :param runner: Existing bounded runner, replaceable in isolated tests.
        :param deadline: Optional shared remaining operation budget.
        :raises ValueError: The supplied timeout is not positive.
        """
        if timeout <= 0:
            raise ValueError(INVALID_REMOTE)
        self.timeout = timeout
        self.runner = runner
        self.deadline = deadline

    @staticmethod
    def _environment(
        url: str, username: str, token: str
    ) -> Mapping[str, Optional[str]]:
        """Bind credentials to only the selected HTTPS remote in child config.

        :param url: HTTPS repository clone URL without user information.
        :param username: Provider-authenticated username for Git HTTP access.
        :param token: Private selected credential, never an argument or report field.
        :returns: Child-only config; redirects, prompts and credential traces disabled.
        :raises APIError: URL or credentials cannot identify one usable remote.
        """
        parsed = urlsplit(url)
        if (
            parsed.scheme != HTTPS_SCHEME
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise APIError(INVALID_REMOTE)
        if (
            not username
            or not token
            or any(character.isspace() for character in username + token)
        ):
            raise APIError(INVALID_CREDENTIAL)
        encoded = base64.b64encode(
            (username + TOKEN_SEPARATOR + token).encode()
        ).decode()
        config = (
            (EXTRA_HEADER_TEMPLATE.format(url=url), AUTHORIZATION_PREFIX + encoded),
            *PRIVATE_CONFIG_VALUES,
        )
        environment: dict[str, Optional[str]] = {
            GIT_CONFIG_COUNT: str(len(config)),
            GIT_TERMINAL_PROMPT: NO_PROMPT,
            **dict.fromkeys(TRACE_VARIABLES),
        }
        for index, (key, value) in enumerate(config):
            environment[GIT_CONFIG_KEY + str(index)] = key
            environment[GIT_CONFIG_VALUE + str(index)] = value
        return environment

    def read(self, url: str, username: str, token: str) -> RefSnapshot:
        """Observe exact branch/tag object IDs without cloning Git objects.

        :param url: Validated provider-returned HTTPS clone URL.
        :param username: Username associated with the selected provider credential.
        :param token: Private credential passed only through child environment.
        :returns: Complete parsed remote refs within the runner's byte/time limits.
        :raises APIError: URL, credential, native process result or ref data is invalid.
        :raises OSError: The shared runner cannot start or read the native process.
        """  # noqa: DOC503 - Shared readers and transport propagate the documented failures.
        result = self.runner(
            [GIT_EXECUTABLE, *LS_REMOTE_ARGUMENTS, url],
            timeout=(
                min(self.timeout, self.deadline.remaining())
                if self.deadline is not None
                else self.timeout
            ),
            env=self._environment(url, username, token),
        )
        if result.returncode:
            raise APIError(error_class(result))
        if self.deadline is not None:
            self.deadline.remaining()
        return RefSnapshot.parse(result.stdout)
