"""GitHub authentication and runner operations behind the callback interface.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from dataclasses import dataclass, field, replace
from typing import Final, Optional

from lib.python.access.credentials import bind_sources
from lib.python.access.sources import Sources
from lib.python.core.abstract.callback import Callback
from lib.python.core.abstract.provider_runtime import ProviderRuntime
from lib.python.core.abstract.skill_action import ActionT_co
from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.abstract.skill_state import SkillState
from lib.python.core.catalog import github_variables
from lib.python.core.cli_api import (
    APIError,
    APISession,
    CLIAPIClient,
    NativeResponse,
    ReadSession,
    RequestDeadline,
)
from lib.python.core.delivery_state import DeliveryState, ProviderAuthAction
from lib.python.core.json_types import (
    JSONDocument,
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


def pin_github_environment(
    host: str,
    reference: str,
    environment: Mapping[str, Optional[str]],
    timeout: int,
) -> Mapping[str, Optional[str]]:
    """Resolve native-store credentials once into private child-only overrides.

    :param host: Selected GitHub hostname.
    :param reference: Reportable selected credential-source reference.
    :param environment: Existing provider credential overrides.
    :param timeout: Maximum seconds for the native credential-store read.
    :returns: Pinned environment, without changing persistent native CLI login state.
    :raises TargetError: The selected native credential cannot be pinned.
    :raises OSError: The native credential command cannot be launched.
    """  # noqa: DOC503 - The existing native command runner propagates OSError.
    pinned: MutableMapping[str, Optional[str]] = dict(environment)
    if reference.startswith("gh-credential-store:"):
        result = run_command_bounded(
            ["gh", "auth", "token", "--hostname", host],
            env=pinned,
            timeout=timeout,
        )
        token = result.stdout.strip()
        if result.returncode or not token or any(char.isspace() for char in token):
            raise TargetError("credential_store_unpinnable")
        pinned[github_variables(host)[0]] = token
    return pinned


@dataclass(frozen=True)
class GitHubRuntime(ProviderRuntime):
    """Keep repository access separate from token scope observations.

    :param repository_id: Positive ID of the selected repository.
    :param user_id: Positive authenticated GitHub user ID.
    :param token_scopes: Classic OAuth scopes when reported; None means unreported.

    .. attribute :: repository_id
        :type: int | None

        Provider ID binding this observation to the selected repository.

    .. attribute :: user_id
        :type: int | None

        Native authenticated user identifier, retained for later identity reports.

    .. attribute :: token_scopes
        :type: tuple[str, ...] | None

        Reported classic scopes, not inferred fine-grained token permissions.
    """

    repository_id: int | None = None
    user_id: int | None = None
    token_scopes: tuple[str, ...] | None = None


def _github_credential_identity(session: ReadSession) -> str:
    """Fingerprint the selected private authentication input without reporting it.

    :param session: Exact-host session with already-resolved credential overrides.
    :returns: A private continuity identifier for this invocation.
    """
    fields = [session.credential_source]
    for key, value in sorted(session.environment.items()):
        fields.extend((key, value if value is not None else ""))
    return hashlib.sha256("\0".join(fields).encode()).hexdigest()


def require_github_runtime(
    state: SkillState, session: ReadSession, repository: str
) -> GitHubRuntime:
    """Check retained repository access without performing a provider request.

    :param state: Shared invocation state containing declaration and observation.
    :param session: Credential and hostname selected for the pending operation.
    :param repository: Exact owner/repository selected by the caller.
    :returns: The previously established repository identity and role.
    :raises ValueError: Declaration, runtime, role, or selected binding is invalid.
    :raises TypeError: The retained observation lacks native GitHub identity fields.
    """
    expected = state.provider_spec(SkillPluginType.GITHUB).get("role")
    if not isinstance(expected, str) or not expected.strip():
        raise ValueError("github_role_requirement_missing")
    runtime = state.require_runtime(
        SkillPluginType.GITHUB,
        expected,
        authority=f"https://{session.host}/repos/{repository}".casefold(),
        credential_identity=_github_credential_identity(session),
    )
    if not isinstance(runtime, GitHubRuntime):
        raise TypeError("github_repository_identity_missing")
    return runtime


def establish_github_identity(
    state: SkillState,
    session: ReadSession,
    repository: str,
    api: GitHubAPIClient,
) -> GitHubRuntime:
    """Observe identity and selected repository role once for authentication.

    :param state: Shared invocation state whose GitHub observation is write-once.
    :param session: Private session selected before the callback loop starts.
    :param repository: Caller-selected owner/repository path.
    :param api: Existing bounded GitHub transport.
    :returns: Retained repository role, identity, and separately observed token scopes.
    :raises APIError: Provider identity or repository response is invalid.
    :raises ValueError: The retained or observed access does not match the declaration.
    :raises OSError: A native provider command cannot be launched.
    """  # noqa: DOC503 - Native reads propagate process errors.
    if not repository:
        raise ValueError(
            "github_repository_required: select github.repository in target.toml "
            "to check the declared repository role"
        )
    if state.github_runtime.role is not None:
        return require_github_runtime(state, session, repository)
    user, headers, record = api._identity_documents(session, repository)
    identity = user.get("login") if isinstance(user, Mapping) else None
    user_id = user.get("id") if isinstance(user, Mapping) else None
    if (
        not isinstance(identity, str)
        or not identity.strip()
        or type(user_id) is not int
        or user_id <= 0
    ):
        raise APIError("github_identity_missing")
    repository_id = record.get("id") if isinstance(record, Mapping) else None
    name = record.get("full_name") if isinstance(record, Mapping) else None
    if (
        not isinstance(name, str)
        or name.casefold() != repository.casefold()
        or type(repository_id) is not int
        or repository_id <= 0
    ):
        raise APIError("github_repository_mismatch")
    permissions = record.get("permissions") if isinstance(record, Mapping) else None
    if not isinstance(permissions, Mapping):
        raise APIError("github_repository_permissions_missing")
    role = next(
        (
            role
            for field_name, role in (
                ("admin", "admin"),
                ("maintain", "maintain"),
                ("push", "write"),
                ("triage", "triage"),
                ("pull", "read"),
            )
            if permissions.get(field_name) is True
        ),
        "none",
    )
    scopes = None
    for line in headers.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.casefold() == "x-oauth-scopes":
            scopes = tuple(
                sorted(scope.strip() for scope in value.split(",") if scope.strip())
            )
            break
    state.establish_runtime(
        SkillPluginType.GITHUB,
        GitHubRuntime(
            role=role,
            identity=identity,
            authority=f"https://{session.host}/repos/{repository}".casefold(),
            credential_identity=_github_credential_identity(session),
            repository_id=repository_id,
            user_id=user_id,
            token_scopes=scopes,
        ),
    )
    return require_github_runtime(state, session, repository)


class GitHubAPIClient(CLIAPIClient):
    """Use GitHub's native CLI with the existing shared transport."""

    def _identity_documents(
        self, session: ReadSession, repository: str
    ) -> tuple[JSONDocument, str, JSONDocument]:
        """Bootstrap only the two fixed reads needed to establish native identity.

        :param session: Selected private credentials before identity is available.
        :param repository: Exact repository whose role the initializer will retain.
        :returns: Native user body, user headers, and selected repository body.
        :raises APIError: A native read or response decoding fails.
        :raises OSError: A native provider process cannot be launched.
        """  # noqa: DOC502 - Existing bounded transport and decoding propagate failures.
        user = super()._exchange(session, "GET", "user")
        record = super()._exchange(session, "GET", f"repos/{repository}")
        return (
            self._decode_response(user.text, "GET"),
            user.headers,
            self._decode_response(record.text, "GET"),
        )

    def _exchange(
        self,
        session: APISession,
        method: str,
        endpoint: str,
        body: Optional[JSONObject] = None,
        *,
        raw: bool = False,
        paginate: bool = False,
        deadline: Optional[RequestDeadline] = None,
    ) -> NativeResponse:
        """Require retained access before every ordinary native API operation.

        :param session: Bound GitHub service or invocation-aware read session.
        :param method: HTTP operation supplied by the provider service.
        :param endpoint: Relative endpoint at the selected provider.
        :param body: Optional request fields.
        :param raw: Whether native output is retained without HTTP headers.
        :param paginate: Whether the native CLI collects response pages.
        :param deadline: Optional shared read deadline.
        :returns: Existing bounded transport result after local authorization checks.
        :raises TypeError: The caller did not supply a supported bound session.
        :raises ValueError: Invocation state, role, or repository binding is invalid.
        :raises APIError: Provider request, decoding, or response bounds fail.
        :raises OSError: The native process or private request-file operation fails.
        """  # noqa: DOC502,DOC503 - Shared guards and transport propagate provider failures.
        if not isinstance(session, (ReadSession, GitHubService)):
            raise TypeError("github_identity_preflight_missing")
        if session.invocation is None or session.repository is None:
            raise ValueError("github_identity_preflight_missing")
        selected = ReadSession(
            session.host, session.environment, session.credential_source
        )
        require_github_runtime(session.invocation, selected, session.repository)
        if endpoint.startswith("repos/"):
            requested = "/".join(endpoint.split("/", 3)[1:3]).partition("?")[0]
            if requested.casefold() != session.repository.casefold():
                raise ValueError("github_runtime_authority_mismatch")
        return super()._exchange(
            session,
            method,
            endpoint,
            body,
            raw=raw,
            paginate=paginate,
            deadline=deadline,
        )

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
class GitHubService:
    """GitHub service created after identity and repository read-back.

    :param host: Selected provider hostname.
    :param repository: Verified owner/repository path.
    :param environment: Child credential overrides; ``None`` removes an ambient variable.
    :param api: Native GitHub CLI transport shared by all service operations.
    :param identity: Nonempty login read from the authenticated user response.
    :param repository_id: Positive repository ID returned by the selected provider.
    :param credential_source: Reportable reference to the selected credential source.
    :param invocation: Shared state whose retained authorization protects service calls.

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

    .. attribute :: invocation
        :type: SkillState | None

        Shared invocation state, required before provider operations.
    """

    host: str
    repository: str
    environment: Mapping[str, Optional[str]] = field(repr=False)
    api: GitHubAPIClient = field(repr=False)
    identity: str
    repository_id: int
    credential_source: str
    invocation: SkillState | None = field(default=None, repr=False, compare=False)

    def require_access(self) -> GitHubRuntime:
        """Reject direct library operations that omit the identity prerequisite.

        :returns: Previously verified repository role and authentication binding.
        :raises ValueError: Invocation state or its required observation is missing.
        """  # noqa: DOC502 - The shared runtime guard rejects invalid bindings.
        if self.invocation is None:
            raise ValueError("github_identity_preflight_missing")
        return require_github_runtime(
            self.invocation,
            ReadSession(self.host, self.environment, self.credential_source),
            self.repository,
        )

    def request(
        self, method: str, endpoint: str, body: Optional[JSONObject] = None
    ) -> JSONDocument:
        """Call the shared transport against this bound GitHub service.

        :param method: HTTP method required by the atom.
        :param endpoint: Relative API endpoint.
        :param body: JSON request fields; ``None`` sends no request body.
        :returns: Decoded response object or array; empty DELETE returns an empty mapping.
        :raises APIError: If endpoint or body validation fails, a provider failure
            is terminal or exhausts GET retries, response bytes exceed the limit,
            or the decoded result is not a JSON object or array.
        :raises OSError: If the native CLI cannot start or private request-body
            file creation, writing, or removal fails.
        """  # noqa: DOC503 - The shared transport propagates native process and body-file errors.
        self.require_access()
        response = self.api._request(self, method, endpoint, body)
        if not isinstance(response, (Mapping, Sequence)) or isinstance(response, str):
            raise APIError("github_response_invalid")
        return response

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

    @property
    def token(self) -> str:
        """Return the bound native credential for an owned workload Secret.

        :returns: Selected private credential, never reportable.
        :raises TargetError: If source binding did not retain a credential.
        """
        self.require_access()
        selected = [value for value in self.environment.values() if value is not None]
        if len(selected) != 1:
            raise TargetError("github_credential_unresolved")
        return selected[0]


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
        service = self.state.github
        self.state.github_target = GitHubTarget(service.host, service.repository)
        self.state.github_session = ReadSession(
            service.host,
            service.environment,
            service.credential_source,
            invocation=self.state,
            repository=service.repository,
        )
        return {}

    def is_configured(self) -> bool:
        """Check that repository and credential selection completed in shared state.

        :returns: Whether the invocation holds this plugin's concrete configuration.
        """
        return hasattr(self.state, "github_target") and hasattr(
            self.state, "github_session"
        )

    def whoami(self) -> bool:
        """Require the selected repository observation established by authentication.

        :returns: True after checking retained access without network requests.
        :raises ValueError: Identity, role, or selected binding is missing or invalid.
        """  # noqa: DOC502 - The shared runtime guard validates the retained binding.
        require_github_runtime(
            self.state, self.state.github_session, self.state.github_target.repository
        )
        return True

    @property
    def github(self) -> GitHubService:
        """Read the authenticated service selected for this workflow.

        :returns: Private client bound to the verified repository and identity.
        :raises AttributeError: If composition omitted GitHub authentication.
        """  # noqa: DOC502 - the executor-owned state attribute is absent before authentication
        GitHubCallback.whoami(self)
        return self.state.github


class GitHubAuth(GitHubCallback[ProviderAuthAction]):
    """Verify runtime identity and repository before dependent runner actions."""

    def __init__(self, target: Target, timeout: int):
        """Construct the required target and request-bound inputs.

        :param target: Existing runtime target resolver result.
        :param timeout: Positive per-request time bound in seconds.
        """
        super().__init__(ProviderAuthAction(target, timeout))

    def _service(self) -> GitHubService:
        """Verify user and repository fields before constructing a usable service.

        :returns: GitHub service carrying actual provider identity values.
        :raises TargetError: GitHub is unselected or its bound credential is unavailable.
        :raises APIError: If authenticated identity or repository read-back is invalid.
        :raises OSError: If a native provider command cannot be launched.
        """  # noqa: DOC503 - native credential and transport calls propagate OSError
        github = self.state.github_target
        session = self.state.github_session
        api = GitHubAPIClient(timeout=self.action.timeout)
        observed = establish_github_identity(
            self.state, session, github.repository, api
        )
        identity, repository_id = observed.identity, observed.repository_id
        if identity is None or repository_id is None:
            raise APIError("github_repository_identity_missing")
        return GitHubService(
            github.host,
            github.repository,
            session.environment,
            api,
            identity,
            repository_id,
            session.credential_source,
            self.state,
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
            github.host,
            pin_github_environment(
                github.host, reference, sources.github.environment, self.action.timeout
            ),
            reference,
            invocation=self.state,
            repository=github.repository,
        )
        return {}

    def whoami(self) -> bool:
        """Initialize identity once and retain a service bound to that observation.

        :returns: True after the observed repository role matches the declaration.
        :raises APIError: Native identity or selected repository read-back is invalid.
        :raises ValueError: Required role or retained provider binding is invalid.
        :raises OSError: A native provider command cannot be launched.
        """  # noqa: DOC502,DOC503 - The shared identity initializer propagates these failures.
        if self.action.target.github != self.state.github_target:
            raise ValueError("github_configuration_binding_mismatch")
        self.state.github = self._service()
        return True

    def run(self) -> JSONObject:
        """Read repository runners using the identity retained by whoami.

        :returns: Safe repository, login, and credential-source evidence.
        :raises APIError: Identity, repository, or runner access read-back fails.
        :raises OSError: A native provider command cannot be launched.
        """  # noqa: DOC502 - Provider transport and response validation propagate errors.
        service = self.github
        service.runners()
        evidence: JSONObject = {
            "host": service.host,
            "identity": service.identity,
            "repository": service.repository,
            "repository_id": service.repository_id,
            "credential_source": service.credential_source,
        }
        self.state.access_evidence[GITHUB_SERVICE_KEY] = evidence
        return evidence
