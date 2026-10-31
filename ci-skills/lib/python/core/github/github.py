"""GitHub authentication and runner operations behind the callback interface.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import time
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from dataclasses import dataclass, field, replace
from http import HTTPMethod
from typing import Final, Optional

from lib.python.access.credentials import bind_sources
from lib.python.access.github import github_env
from lib.python.access.sources import Sources
from lib.python.core.abstract.callback import Callback
from lib.python.core.abstract.skill_action import ActionT_co
from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.catalog import github_variables
from lib.python.core.cli_api import APIError, CLIAPIClient, ReadSession, RequestDeadline
from lib.python.core.delivery_state import DeliveryState, ProviderAuthAction
from lib.python.core.github.session import (
    GitHubIdentitySession,
    GitHubRequestService,
)
from lib.python.core.json_types import (
    JSONObject,
    json_object,
    json_objects,
)
from lib.python.core.runtime import run_command_bounded
from lib.python.core.target import GitHubTarget, Target, TargetError

GITHUB_SERVICE_KEY: Final = SkillPluginType.GITHUB.value
GITHUB_PAGE_SIZE: Final = 100
RUNNER_POLL_SECONDS: Final = 2
RUNNER_ONLINE: Final = "online"
IDENTITY_ENDPOINT: Final = "user"
IDENTITY_LOGIN: Final = "login"
IDENTITY_MISSING: Final = "github_identity_missing"


class GitHubAPIClient(CLIAPIClient):
    """Use GitHub's native CLI with the existing shared transport."""

    @staticmethod
    def pagination_arguments() -> Sequence[str]:
        """Collect GitHub pages using the native CLI's page-array output.

        :returns: Native flags that retain each page inside the outer response array.
        """
        return ("--paginate", "--slurp")

    @staticmethod
    def argv(host: str, method: str, endpoint: str) -> MutableSequence[str]:
        """Build an exact-host native GitHub API request.

        :param host: Configured GitHub hostname.
        :param method: HTTP operation.
        :param endpoint: Validated relative GitHub API path.
        :returns: CLI arguments with JSON-compatible response headers.
        """
        return [
            "gh",
            "api",
            "--hostname",
            host,
            "--method",
            method,
            "--include",
            endpoint,
        ]


@dataclass(frozen=True)
class GitHubService(GitHubRequestService):
    """GitHub service created after identity and repository read-back.

    :param host: Selected provider hostname.
    :param repository: Verified owner/repository path.
    :param environment: Child credential overrides; ``None`` removes an ambient variable.
    :param api: Native GitHub CLI transport shared by all service operations.
    :param identity: Nonempty login read from the authenticated user response.
    :param repository_id: Positive repository ID returned by the selected provider.
    :param credential_source: Reportable reference to the selected credential source.

    .. attribute :: host
        :type: str

        Selected provider hostname.

    .. attribute :: repository
        :type: str

        Verified owner/repository path.

    .. attribute :: environment
        :type: Mapping[str, Optional[str]]

        Child credential overrides; ``None`` removes an ambient variable.

    .. attribute :: api
        :type: GitHubAPIClient

        Native GitHub CLI transport shared by all service operations.

    .. attribute :: identity
        :type: str

        Nonempty login read from the authenticated user response.

    .. attribute :: repository_id
        :type: int

        Positive repository ID returned by the selected provider.

    .. attribute :: credential_source
        :type: str

        Reportable reference to the selected credential source.
    """

    host: str
    repository: str
    environment: Mapping[str, Optional[str]] = field(repr=False)
    api: GitHubAPIClient = field(repr=False)
    identity: str
    repository_id: int
    credential_source: str

    def runners(self) -> Sequence[JSONObject]:
        """Read all repository runner pages using the provider's total count.

        :returns: Runner records for the exact authenticated repository.
        :raises APIError: If response shape or pagination is incomplete.
        """
        records: MutableSequence[JSONObject] = []
        page = 1
        while True:
            payload = self.request(
                "GET",
                f"repos/{self.repository}/actions/runners?per_page={GITHUB_PAGE_SIZE}&page={page}",
            )
            try:
                document = json_object(payload)
                batch = json_objects(document.get("runners"))
            except TypeError as exc:
                raise APIError("runner_response_invalid") from exc
            total = document.get("total_count")
            if type(total) is not int or total < 0:
                raise APIError("runner_response_invalid")
            records.extend(batch)
            if len(records) >= total:
                return records
            if not batch:
                raise APIError("runner_pages_incomplete")
            page += 1

    def runner(self, name: str) -> Optional[JSONObject]:
        """Find an exact runner name without silently selecting among duplicates.

        :param name: Caller-selected GitHub runner name.
        :returns: The runner record, or None when no record exists.
        :raises APIError: If listing fails or the name is not unique.
        """
        matching = [record for record in self.runners() if record.get("name") == name]
        if len(matching) > 1:
            raise APIError("runner_name_not_unique")
        return matching[0] if matching else None

    def wait_online(self, name: str, timeout: int) -> JSONObject:
        """Poll native runner state until the exact runner is online.

        :param name: Exact expected runner name.
        :param timeout: Total readiness deadline in seconds.
        :returns: Runner with the provider's online state.
        :raises APIError: If read-back fails or the readiness deadline expires.
        """
        deadline = time.monotonic() + timeout
        while True:
            runner = self.runner(name)
            if runner is not None and runner.get("status") == RUNNER_ONLINE:
                return runner
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise APIError("runner_online_timeout")
            time.sleep(min(RUNNER_POLL_SECONDS, remaining))

    def delete_runner(self, name: str) -> bool:
        """Delete the exact discovered runner and independently prove absence.

        :param name: Caller-selected runner name.
        :returns: True if a provider record needed deletion.
        :raises APIError: If deletion or absence read-back fails.
        """
        observed = self.runner(name)
        if observed is None:
            return False
        self.request(
            "DELETE", f"repos/{self.repository}/actions/runners/{observed['id']}"
        )
        if self.runner(name) is not None:
            raise APIError("runner_delete_incomplete")
        return True


class GitHubCallback(Callback[DeliveryState, ActionT_co]):
    """Use the GitHub service established in this delivery state."""

    @property
    def plugin_type(self) -> SkillPluginType:
        """Identify the configuration shared by this plugin's callbacks.

        :returns: The github plugin's configuration cache key.
        """
        return SkillPluginType.GITHUB

    def read_config(self) -> JSONObject:
        """Reuse a caller-bound GitHub service for direct library operations.

        :returns: Empty setup evidence after retaining the existing private session.
        :raises TargetError: Authentication or the caller supplied no service.
        """  # noqa: DOC502 - Direct library callers provide the existing service.
        if not hasattr(self.state, "github"):
            raise TargetError(f"{SkillPluginType.GITHUB.value}_configuration_missing")
        service = self.github
        self.state.github_target = GitHubTarget(service.host, service.repository)
        self.state.github_session = ReadSession(
            service.host, service.environment, service.credential_source
        )
        return {}

    def is_configured(self) -> bool:
        """Check that repository and credential selection completed in shared state.

        :returns: Whether the invocation holds this plugin's concrete configuration.
        """
        return hasattr(self.state, "github_target") and hasattr(
            self.state, "github_session"
        )

    @property
    def github(self) -> GitHubService:
        """Read the authenticated service selected for this workflow.

        :returns: Private client bound to the verified repository and identity.
        :raises AttributeError: If composition omitted GitHub authentication.
        """  # noqa: DOC502 - the executor-owned state attribute is absent before authentication
        return self.state.github


class GitHubAuth(GitHubCallback[ProviderAuthAction]):
    """Verify runtime identity and repository before dependent runner actions."""

    def __init__(
        self,
        target: Target,
        timeout: int,
        *,
        deadline: Optional[RequestDeadline] = None,
    ):
        """Construct the required target and request-bound inputs.

        :param target: Existing runtime target resolver result.
        :param timeout: Positive per-request time bound in seconds.
        :param deadline: Optional shared operation budget; existing callers remain unbounded across requests.
        """
        super().__init__(ProviderAuthAction(target, timeout, deadline=deadline))

    def _environment(
        self, target: Target, reference: str
    ) -> Mapping[str, Optional[str]]:
        """Pin a selected native credential into child-only overrides.

        :param target: Target whose credential sources are already bound.
        :param reference: Reportable selected credential-source reference.
        :returns: Child environment with one resolved private credential.
        :raises TargetError: GitHub is unselected or its native credential is unusable.
        :raises OSError: If the native credential command cannot be launched.
        """  # noqa: DOC503 - credential resolution and native command launch propagate failures
        github = self.state.github_target
        environment: MutableMapping[str, Optional[str]] = dict(github_env(target) or {})
        if reference.startswith("gh-credential-store:"):
            result = run_command_bounded(
                ["gh", "auth", "token", "--hostname", github.host],
                env=environment,
                timeout=(
                    min(self.action.timeout, self.action.deadline.remaining())
                    if self.action.deadline
                    else self.action.timeout
                ),
            )
            token = result.stdout.strip()
            if result.returncode or not token or any(char.isspace() for char in token):
                raise TargetError("credential_store_unpinnable")
            environment[github_variables(github.host)[0]] = token
        return environment

    def _identity_session(self) -> GitHubIdentitySession:
        """Verify account access without assuming that a destination exists.

        :returns: Private identity session reused by repository and runner workflows.
        :raises APIError: Provider identity is missing or unusable.
        :raises OSError: The shared native request cannot execute.
        """
        session = self.state.github_session
        api = (
            GitHubAPIClient(timeout=self.action.timeout, deadline=self.action.deadline)
            if self.action.deadline is not None
            else GitHubAPIClient(timeout=self.action.timeout)
        )
        user = api._request(session, HTTPMethod.GET, IDENTITY_ENDPOINT)
        identity = user.get(IDENTITY_LOGIN) if isinstance(user, Mapping) else None
        if not isinstance(identity, str) or not identity.strip():
            raise APIError(IDENTITY_MISSING)
        return GitHubIdentitySession(
            session.host, session.environment, api, identity, session.credential_source
        )

    def _service(self) -> GitHubService:
        """Verify user and repository fields before constructing a usable service.

        :returns: GitHub service carrying actual provider identity values.
        :raises TargetError: GitHub is unselected or its bound credential is unavailable.
        :raises APIError: If authenticated identity or repository read-back is invalid.
        :raises OSError: If a native provider command cannot be launched.
        """  # noqa: DOC503 - native credential and transport calls propagate OSError
        github = self.state.github_target
        session = self._identity_session()
        api = session.api
        identity = session.identity
        repository = session.request("GET", f"repos/{github.repository}")
        repository_id = (
            repository.get("id") if isinstance(repository, Mapping) else None
        )
        name = repository.get("full_name") if isinstance(repository, Mapping) else None
        if (
            not isinstance(name, str)
            or name.casefold() != github.repository.casefold()
            or not isinstance(repository_id, int)
            or isinstance(repository_id, bool)
            or repository_id <= 0
        ):
            raise APIError("github_repository_mismatch")
        return GitHubService(
            github.host,
            github.repository,
            session.environment,
            api,
            identity,
            repository_id,
            session.credential_source,
        )

    def read_config(self) -> JSONObject:
        """Resolve this plugin's repository and private credential before execution.

        :returns: Empty setup evidence; the private session remains in shared state.
        :raises TargetError: The GitHub target or selected credential is unavailable.
        :raises OSError: The existing native credential store cannot be read.
        """  # noqa: DOC503 - Existing binding and native credential readers propagate errors.
        selected = self.action.target
        target = (
            selected
            if isinstance(selected.sources, Sources)
            else bind_sources(
                replace(selected, active_surfaces=(SkillPluginType.GITHUB.value,))
            )
        )
        github, sources = target.github, target.sources
        if github is None:
            raise TargetError("github_target_missing")
        if not isinstance(sources, Sources) or sources.github is None:
            raise TargetError("github_credential_unresolved")
        self.state.github_target = github
        reference = sources.github.reference
        self.state.github_session = ReadSession(
            github.host, self._environment(target, reference), reference
        )
        return {}

    def run(self) -> JSONObject:
        """Verify live access using the configuration retained during construction.

        :returns: Safe repository, login, and credential-source evidence.
        :raises APIError: Identity, repository, or runner access read-back fails.
        :raises OSError: A native provider command cannot be launched.
        """  # noqa: DOC502 - Provider transport and response validation propagate errors.
        service = self._service()
        service.runners()
        self.state.github = service
        evidence: JSONObject = {
            "host": service.host,
            "identity": service.identity,
            "repository": service.repository,
            "repository_id": service.repository_id,
            "credential_source": service.credential_source,
        }
        self.state.access_evidence[GITHUB_SERVICE_KEY] = evidence
        return evidence


class GitHubIdentityAuth(GitHubAuth):
    """Authenticate an account before an optional repository creation workflow."""

    def run(self) -> JSONObject:
        """Retain shared account access without invoking runner-only checks.

        :returns: Redacted identity evidence for the selected GitHub provider.
        :raises APIError: Authenticated identity cannot be read back.
        :raises OSError: The shared native request cannot execute.
        """  # noqa: DOC502 - Shared readers and transport propagate the documented failures.
        session = self._identity_session()
        self.state.github_identity = session
        evidence: JSONObject = {
            "host": session.host,
            "identity": session.identity,
            "credential_source": session.credential_source,
        }
        self.state.access_evidence[GITHUB_SERVICE_KEY] = evidence
        return evidence
