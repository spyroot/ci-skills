"""GitHub authentication and runner operations behind the callback interface.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Final, cast

from lib.python.access.access import github_env
from lib.python.core.action import GitHubCallback
from lib.python.core.catalog import github_variables
from lib.python.core.cli_api import APIError, CLIAPIClient
from lib.python.access.credentials import Sources, bind_sources
from lib.python.core.runtime import run_command_bounded
from lib.python.core.target import Target, TargetError

GITHUB_SERVICE_KEY: Final = "github"
GITHUB_PAGE_SIZE: Final = 100
RUNNER_POLL_SECONDS: Final = 2
RUNNER_ONLINE: Final = "online"


class GitHubAPIClient(CLIAPIClient):
    """Use GitHub's native CLI with the existing shared transport."""

    @staticmethod
    def argv(host: str, method: str, endpoint: str) -> list[str]:
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
class GitHubService:
    """Bound GitHub identity, repository, credential, and API adapter."""

    host: str
    repository: str
    environment: dict[str, str | None] = field(repr=False)
    api: GitHubAPIClient = field(repr=False)
    identity: str
    repository_id: int
    credential_source: str

    def request(
        self, method: str, endpoint: str, body: dict[str, Any] | None = None
    ) -> Any:
        """Call the shared transport against this bound GitHub service.

        :param method: HTTP method required by the atom.
        :param endpoint: Relative API endpoint.
        :param body: Optional request object.
        :returns: Parsed provider response.
        :raises APIError: If the request or provider response fails.
        """
        return self.api._request(self, method, endpoint, body)

    def runners(self) -> list[dict[str, Any]]:
        """Read all repository runner pages using the provider's total count.

        :returns: Runner records for the exact authenticated repository.
        :raises APIError: If response shape or pagination is incomplete.
        """
        records: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self.request(
                "GET",
                f"repos/{self.repository}/actions/runners?per_page={GITHUB_PAGE_SIZE}&page={page}",
            )
            batch = payload.get("runners") if isinstance(payload, dict) else None
            total = payload.get("total_count") if isinstance(payload, dict) else None
            if not isinstance(batch, list) or not isinstance(total, int):
                raise APIError("runner_response_invalid")
            records.extend(batch)
            if len(records) >= total:
                return records
            if not batch:
                raise APIError("runner_pages_incomplete")
            page += 1

    def runner(self, name: str) -> dict[str, Any] | None:
        """Find an exact runner name without silently selecting among duplicates.

        :param name: Caller-selected GitHub runner name.
        :returns: The runner record, or None when no record exists.
        :raises APIError: If listing fails or the name is not unique.
        """
        matching = [record for record in self.runners() if record.get("name") == name]
        if len(matching) > 1:
            raise APIError("runner_name_not_unique")
        return matching[0] if matching else None

    def wait_online(self, name: str, timeout: int) -> dict[str, Any]:
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

    @property
    def token(self) -> str:
        """Return the bound native credential for an owned workload Secret.

        :returns: Selected private credential, never reportable.
        :raises TargetError: If source binding did not retain a credential.
        """
        selected = [value for value in self.environment.values() if value is not None]
        if len(selected) != 1:
            raise TargetError("github_credential_unresolved")
        return selected[0]


class GitHubAuth(GitHubCallback[None, GitHubService]):
    """Verify runtime identity and repository without publication-policy checks."""

    def __init__(self, *, target: Target, timeout: int) -> None:
        """Retain one shared target selection for GitHub operations.

        :param target: Existing target resolver result.
        :param timeout: Per-request time bound in seconds.
        """
        super().__init__()
        self.target = target
        self.timeout = timeout

    def run(self) -> GitHubService:
        """Freeze credentials and read back authenticated user and repository.

        :returns: Shared service bound to the proven repository and identity.
        :raises TargetError: If the selected credential cannot be resolved.
        :raises APIError: If user, repository, or runner access read-back fails.
        """
        target = (
            self.target
            if isinstance(self.target.sources, Sources)
            else bind_sources(self.target)
        )
        if target.github is None:
            raise TargetError("github_target_missing")
        environment = dict(github_env(target) or {})
        sources = cast(Sources, target.sources)
        if sources.github is None:
            raise TargetError("github_credential_unresolved")
        reference = sources.github.reference
        if reference.startswith("gh-credential-store:"):
            result = run_command_bounded(
                ["gh", "auth", "token", "--hostname", target.github.host],
                env=environment,
                timeout=self.timeout,
            )
            token = result.stdout.strip()
            if result.returncode or not token or any(char.isspace() for char in token):
                raise TargetError("credential_store_unpinnable")
            environment[github_variables(target.github.host)[0]] = token
        api = GitHubAPIClient(timeout=self.timeout)
        bound = GitHubService(
            target.github.host,
            target.github.repository,
            environment,
            api,
            "",
            0,
            reference,
        )
        user = bound.request("GET", "user")
        repository = bound.request("GET", f"repos/{bound.repository}")
        if not isinstance(user, dict) or not isinstance(user.get("login"), str):
            raise APIError("github_identity_missing")
        if (
            not isinstance(repository, dict)
            or repository.get("full_name", "").casefold() != bound.repository.casefold()
            or not isinstance(repository.get("id"), int)
        ):
            raise APIError("github_repository_mismatch")
        service = GitHubService(
            bound.host,
            bound.repository,
            environment,
            api,
            user["login"],
            repository["id"],
            reference,
        )
        service.runners()
        self.context.services[GITHUB_SERVICE_KEY] = service
        return service
