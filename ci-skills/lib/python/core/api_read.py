"""Run bounded Git API reads through an explicit shared-state workflow.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from abc import abstractmethod
from collections.abc import Mapping, MutableSequence
from dataclasses import dataclass, field
from dataclasses import field as action_field
from enum import StrEnum
from pathlib import Path
from typing import Optional, TypeVar

from lib.python.access.sources import _token_source
from lib.python.core.abstract.callback import LocalCallback
from lib.python.core.abstract.context import ExecutionContext
from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.catalog import (
    GITHUB_DOTCOM_HOST,
    GITHUB_TOKEN_VARIABLES,
    GITLAB_VARIABLES,
    github_variables,
)
from lib.python.core.cli_api import (
    APIError,
    CLIAPIClient,
    ReadSession,
    response_decoder,
    validate_endpoint,
)
from lib.python.core.github.github import GitHubAPIClient
from lib.python.core.gitlab.gitlab_api import GlabAPIClient
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.project_binding import SMOKE_ENV, resolve_target_file
from lib.python.core.read_tools import (
    MAX_READ_BYTES,
    MAX_READ_LINES,
    MAX_READ_RETRY_AFTER,
    ReadExit,
    ReadToolError,
)
from lib.python.core.target import TargetError, load_target


class GitProvider(StrEnum):
    """Native Git providers supported by the installed read command.

    .. attribute :: GITHUB

        Select the authenticated ``gh api`` transport.

    .. attribute :: GITLAB

        Select the authenticated ``glab api`` transport.
    """

    GITHUB = SkillPluginType.GITHUB.value
    GITLAB = SkillPluginType.GITLAB.value


class ApiOutput(StrEnum):
    """Representations supported by an authenticated API action.

    .. attribute :: JSON

        Emit a JSON document.

    .. attribute :: YAML

        Emit a YAML document.

    .. attribute :: TEXT

        Emit the selected scalar, identity name, or raw endpoint text.
    """

    JSON = "json"
    YAML = "yaml"
    TEXT = "text"


@dataclass(frozen=True)
class ApiProviderAction(SkillAction):
    """Share provider and output selection between concrete API operations.

    :param provider: Native GitHub or GitLab client to use.
    :param host: Explicit provider hostname, or ``None`` for target resolution.
    :param token_file: Token file override, or ``None`` for shared resolution.
    :param output: Selected JSON, YAML, or text representation.

    .. attribute :: provider
        :type: GitProvider

        Native GitHub or GitLab client to use.

    .. attribute :: host
        :type: Optional[str]

        Explicit provider hostname, or ``None`` for target resolution.

    .. attribute :: token_file
        :type: Optional[Path]

        Token file override, or ``None`` for shared resolution.

    .. attribute :: output
        :type: ApiOutput

        Selected JSON, YAML, or text representation.
    """

    provider: GitProvider
    host: Optional[str] = None
    token_file: Optional[Path] = None
    output: ApiOutput = ApiOutput.JSON

    @abstractmethod
    def validate(self) -> None:
        """Require named provider and output choices before access is resolved.

        :raises ReadToolError: If provider or output is not a supported enum member.
        """
        if not isinstance(self.provider, GitProvider):
            raise ReadToolError("invalid provider", ReadExit.USAGE)
        if not isinstance(self.output, ApiOutput):
            raise ReadToolError("invalid output mode", ReadExit.USAGE)


@dataclass(frozen=True)
class ApiCheckAction(ApiProviderAction):
    """Request the authenticated identity for the selected provider.

    ApiProviderAction defines provider, host, credential, and output inputs.
    Identity checks have no endpoint or response-transformation inputs.
    """

    def validate(self) -> None:
        """Check the provider and representation for the identity request.

        :raises ReadToolError: If provider or output is not a supported enum member.
        """  # noqa: DOC502 - The shared provider validation rejects unsupported choices.
        super().validate()


@dataclass(frozen=True, init=False)
class ApiReadAction(ApiProviderAction):
    """Read one required endpoint with optional response transformations.

    ApiProviderAction defines the shared provider and output fields.

    .. attribute :: endpoint
        :type: str

        Required provider-relative GET endpoint.

    .. attribute :: field
        :type: Optional[str]

        Dotted JSON field path; ``None`` selects the entire response.

    .. attribute :: decode_base64
        :type: bool

        Whether to decode the selected field as UTF-8 Base64.

    .. attribute :: lines
        :type: Optional[str]

        Inclusive, one-based ``start:end`` range; ``None`` selects all text.

    .. attribute :: paginate
        :type: bool

        Whether to collect array pages through the native CLI.
    """

    endpoint: str = action_field(init=False)
    field: Optional[str] = action_field(init=False)
    decode_base64: bool = action_field(init=False)
    lines: Optional[str] = action_field(init=False)
    paginate: bool = action_field(init=False)

    def __init__(
        self,
        provider: GitProvider,
        endpoint: str,
        host: Optional[str] = None,
        token_file: Optional[Path] = None,
        field: Optional[str] = None,
        decode_base64: bool = False,
        lines: Optional[str] = None,
        paginate: bool = False,
        output: ApiOutput = ApiOutput.JSON,
    ):
        """Retain one immutable GET request with a required endpoint.

        :param provider: Native GitHub or GitLab client to use.
        :param endpoint: Required provider-relative GET endpoint.
        :param host: Provider hostname, or ``None`` for target resolution.
        :param token_file: Token file override, or ``None`` for shared resolution.
        :param field: Dotted JSON field path, or ``None`` for the entire response.
        :param decode_base64: Whether to decode the selected field as UTF-8 Base64.
        :param lines: Inclusive ``start:end`` range, or ``None`` for all text.
        :param paginate: Whether to collect array pages through the native CLI.
        :param output: Selected JSON, YAML, or text representation.
        """
        super().__init__(provider, host, token_file, output)
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "field", field)
        object.__setattr__(self, "decode_base64", decode_base64)
        object.__setattr__(self, "lines", lines)
        object.__setattr__(self, "paginate", paginate)

    @property
    def raw(self) -> bool:
        """Identify a plain-text endpoint rather than a JSON selection.

        :returns: Whether the response should be treated as plain text.
        """
        return self.output == ApiOutput.TEXT and self.field is None

    def validate(self) -> None:
        """Reject invalid GET inputs and contradictory transformations.

        :raises ReadToolError: If the provider or output mode is unsupported,
            the GET endpoint or dotted field path is invalid, Base64 decoding has
            no selected field,
            raw text requests pagination or decoding, or a line range is malformed,
            reversed, too large, or applied to a nontext response.
        """
        super().validate()
        try:
            validate_endpoint(self.endpoint)
        except APIError as exc:
            raise ReadToolError(
                "a relative GET endpoint without a leading slash is required, "
                "such as projects?membership=true",
                ReadExit.USAGE,
            ) from exc
        if self.field is not None and not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", self.field
        ):
            raise ReadToolError("invalid dotted field path", ReadExit.USAGE)
        if self.decode_base64 and self.field is None:
            raise ReadToolError("base64 decoding requires a field", ReadExit.USAGE)
        if self.raw and (self.paginate or self.decode_base64):
            raise ReadToolError(
                "raw text cannot use pagination or decoding", ReadExit.USAGE
            )
        if self.lines is not None:
            if not re.fullmatch(r"[1-9][0-9]{0,5}:[1-9][0-9]{0,5}", self.lines):
                raise ReadToolError("invalid line range", ReadExit.USAGE)
            start, end = (int(value) for value in self.lines.split(":"))
            if end < start or end - start >= MAX_READ_LINES:
                raise ReadToolError(
                    "line range exceeds the output limit", ReadExit.USAGE
                )
            if not (self.raw or self.decode_base64):
                raise ReadToolError("line selection requires text", ReadExit.USAGE)


@dataclass(frozen=True)
class ApiProviderSelection:
    """Resolved provider inputs before any credential material is read.

    :param provider: Native provider selected by the caller.
    :param host: Validated hostname from runtime configuration or the explicit option.
    :param token_file: Selected credential-file reference; absent uses shared native
        credential resolution when the binding callback runs.

    .. attribute :: provider
        :type: GitProvider

        Native provider selected by the caller.

    .. attribute :: host
        :type: str

        Validated hostname from runtime configuration or the explicit option.

    .. attribute :: token_file
        :type: Optional[Path]

        Selected credential-file reference; absent uses shared native credential resolution when
        the binding callback runs.
    """

    provider: GitProvider
    host: str
    token_file: Optional[Path] = None

    @classmethod
    def from_action(cls, action: ApiProviderAction) -> ApiProviderSelection:
        """Resolve the default target and existing host/file overrides.

        :param action: Validated concrete API inputs containing provider selection.
        :returns: Concrete host and credential reference, without reading the token.
        :raises TargetError: If an explicitly selected target, smoke target, or
            configured provider is missing or invalid; no fallback follows that error.
        :raises ReadToolError: If the provider hostname is missing or malformed.
        """
        host, token_file = action.host, action.token_file
        try:
            path, _source = resolve_target_file(None)
        except TargetError as exc:
            if os.getenv(SMOKE_ENV) or not str(exc).startswith("target_missing:"):
                raise
        else:
            target = load_target(path, required_surfaces=(action.provider.value,))
            configured = (
                target.github
                if action.provider == GitProvider.GITHUB
                else target.gitlab
            )
            if configured is None:
                raise TargetError("provider_target_missing")
            if host is None or host.lower() == configured.host:
                host = configured.host
                token_file = token_file or configured.token_file
        if host is None and action.provider == GitProvider.GITHUB:
            host = GITHUB_DOTCOM_HOST
        if not host or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host):
            raise ReadToolError("select a provider host in target.toml", ReadExit.USAGE)
        return cls(action.provider, host.lower(), token_file)


@dataclass(frozen=True)
class ApiReadService:
    """Native transport bound to one resolved provider credential source.

    :param client: Shared native CLI transport with read-command response limits.
    :param session: Exact provider hostname and private child credential environment.

    .. attribute :: client
        :type: CLIAPIClient

        Shared native CLI transport with read-command response limits.

    .. attribute :: session
        :type: ReadSession

        Exact provider hostname and private child credential environment.
    """

    client: CLIAPIClient
    session: ReadSession = field(repr=False)

    @classmethod
    def bind(cls, selection: ApiProviderSelection) -> ApiReadService:
        """Read the selected credential source and construct its native transport.

        :param selection: Provider and host already resolved by the selection step.
        :returns: Client and exact-host credential session for authenticated reads.
        :raises TargetError: If a selected credential file or environment value is invalid.
        """  # noqa: DOC502 - Credential-source binding propagates TargetError.
        github = selection.provider == GitProvider.GITHUB
        source = _token_source(
            selection.token_file,
            github_variables(selection.host) if github else GITLAB_VARIABLES,
            f"{'gh' if github else 'glab'}-credential-store:{selection.host}",
            clear_names=GITHUB_TOKEN_VARIABLES if github else GITLAB_VARIABLES,
        )
        client_type = GitHubAPIClient if github else GlabAPIClient
        client = client_type(
            max_response_bytes=MAX_READ_BYTES,
            max_retry_after=MAX_READ_RETRY_AFTER,
        )
        return cls(
            client, ReadSession(selection.host, source.environment, source.reference)
        )

    def read(self, action: ApiReadAction) -> JSONValue:
        """Read one GET response and select the caller's bounded native value.

        :param action: Validated endpoint, pagination, and response-selection inputs.
        :returns: JSON value or text selected from the successful response.
        :raises APIError: If the provider, retry policy, or response-size bound fails.
        :raises ReadToolError: If the response or requested transformation is invalid.
        :raises OSError: If the native provider process cannot be started.
        """  # noqa: DOC502 - Native reads and response selection propagate failures.
        action.validate()
        text = self.client.get_text(
            self.session, action.endpoint, paginate=action.paginate
        ).rstrip("\n")
        return ApiResponseSelector(action, self.session.host).select(text)

    def check(self, action: ApiCheckAction) -> JSONValue:
        """Read the authenticated provider identity without response transforms.

        :param action: Validated identity-check provider and representation.
        :returns: Identity name for text, otherwise a provider identity object.
        :raises APIError: If authentication, transport, retry, or response bounds fail.
        :raises ReadToolError: If the action is invalid or the response is not one
            JSON object containing a nonempty identity name.
        :raises OSError: If the native provider process cannot be started.
        """  # noqa: DOC502 - Native reads and identity selection propagate failures.
        action.validate()
        text = self.client.get_text(self.session, "user").rstrip("\n")
        value = ApiJSONDecoder(action.provider).decode(text)
        return ApiIdentitySelector(action, self.session.host).select(value)


@dataclass(frozen=True)
class ApiIdentitySelector:
    """Select the public identity fields from an authenticated user response.

    :param action: Identity provider and output representation.
    :param host: Exact provider host included in the identity record.

    .. attribute :: action
        :type: ApiCheckAction

        Concrete identity request whose representation controls the result.

    .. attribute :: host
        :type: str

        Exact provider host included in the identity record.
    """

    action: ApiCheckAction
    host: str

    def select(self, value: JSONValue) -> JSONValue:
        """Select a reportable identity from the authenticated user endpoint.

        :param value: Decoded provider identity response.
        :returns: Identity name for text output, otherwise provider, host, user, and ID.
        :raises ReadToolError: If the response is not an object naming an identity.
        """
        if not isinstance(value, Mapping):
            raise ReadToolError("invalid identity response")
        user = value.get("login") or value.get("username") or value.get("name")
        if not isinstance(user, str) or not user:
            raise ReadToolError("invalid identity response")
        if self.action.output == ApiOutput.TEXT:
            return user
        return {
            "provider": self.action.provider.value,
            "host": self.host,
            "authenticated": True,
            "user": user,
            "id": value.get("id"),
        }


@dataclass(frozen=True)
class ApiResponseSelector:
    """Decode and transform one bounded provider response independently of I/O.

    :param action: Validated read and response-selection options.
    :param host: Exact provider host included in decoded-file records.

    .. attribute :: action
        :type: ApiReadAction

        Validated read and response-selection options.

    .. attribute :: host
        :type: str

        Exact provider host included in decoded-file records.
    """

    action: ApiReadAction
    host: str

    def select(self, text: str) -> JSONValue:
        """Apply the declared raw-text, field, and decoding selection.

        :param text: Native response text without trailing newline separators.
        :returns: Selected native JSON value or requested text representation.
        :raises ReadToolError: If JSON, selected field, Base64, or scalar shape is invalid.
        """
        if self.action.raw:
            return self.lines(text)
        selected = ApiJSONDecoder(self.action.provider, self.action.paginate).decode(
            text
        )
        parts = self.action.field.split(".") if self.action.field is not None else ()
        for part in parts:
            if (
                not isinstance(selected, Mapping)
                or part not in selected
                or selected[part] is None
            ):
                raise ReadToolError("selected field is missing or invalid")
            selected = selected[part]
        if self.action.decode_base64:
            return self.decoded_file(selected)
        if self.action.output == ApiOutput.TEXT:
            if not isinstance(selected, (str, int, float, bool)):
                raise ReadToolError("selected field is not scalar")
            return selected if isinstance(selected, str) else json.dumps(selected)
        return selected

    def decoded_file(self, value: JSONValue) -> JSONValue:
        """Decode one selected Base64 field and apply the requested line range.

        :param value: Field expected to contain a UTF-8 Base64 string.
        :returns: Selected text or a decoded-file record containing that same text.
        :raises ReadToolError: If the value is not valid Base64 UTF-8 text or exceeds
            the response bound after decoding.
        """
        if not isinstance(value, str):
            raise ReadToolError("selected field is not base64 text")
        try:
            decoded = base64.b64decode("".join(value.split()), validate=True)
            text = decoded.decode("utf-8").rstrip("\n")
        except (ValueError, UnicodeError, binascii.Error) as exc:
            raise ReadToolError("selected field is not valid base64 text") from exc
        content = self.lines(text)
        if self.action.output == ApiOutput.TEXT:
            return content
        return {
            "provider": self.action.provider.value,
            "host": self.host,
            "endpoint": self.action.endpoint,
            "field": self.action.field if self.action.field is not None else "",
            "lines": self.action.lines if self.action.lines is not None else "",
            "content": content,
        }

    def lines(self, text: str) -> str:
        """Bound UTF-8 text and select an inclusive numbered line range.

        :param text: Raw response or decoded file content.
        :returns: Original text or selected lines prefixed with one-based line numbers.
        :raises ReadToolError: If the selected text exceeds one MiB.
        :raises ValueError: If called with an action whose line range was not validated.
        """  # noqa: DOC503 - Unvalidated line bounds can fail int conversion.
        if len(text.encode("utf-8")) > MAX_READ_BYTES:
            raise ReadToolError("API response exceeds 1 MiB")
        if self.action.lines is None:
            return text
        start, end = (int(value) for value in self.action.lines.split(":"))
        return "\n".join(
            f"{index}:{line}"
            for index, line in enumerate(text.splitlines(), 1)
            if start <= index <= end
        )


@dataclass(frozen=True)
class ApiJSONDecoder:
    """Decode bounded native CLI JSON with the provider's pagination framing.

    :param provider: Native provider that produced the response stream.
    :param paginate: Whether the response contains paginated collection arrays.

    .. attribute :: provider
        :type: GitProvider

        Native provider that produced the response stream.

    .. attribute :: paginate
        :type: bool

        Whether the response contains paginated collection arrays.
    """

    provider: GitProvider
    paginate: bool = False

    def decode(self, text: str) -> JSONValue:
        """Decode one JSON response or flatten native array pages.

        :param text: Provider JSON response stream.
        :returns: One native value, or a flat list containing all collection pages.
        :raises ReadToolError: If JSON is empty, malformed, nonfinite, too deeply
            nested, or pagination does not contain the expected array shape.
        """
        decoder = response_decoder()
        values: MutableSequence[JSONValue] = []
        remaining = text.lstrip()
        try:
            while remaining:
                value, end = decoder.raw_decode(remaining)
                values.append(value)
                remaining = remaining[end:].lstrip()
        except (ValueError, RecursionError) as exc:
            raise ReadToolError("provider returned invalid JSON") from exc
        if not self.paginate:
            if len(values) != 1:
                raise ReadToolError("provider returned invalid JSON")
            return values[0]
        if not values:
            raise ReadToolError("provider returned invalid paginated JSON")
        if self.provider == GitProvider.GITHUB:
            if len(values) != 1 or not isinstance(values[0], list):
                raise ReadToolError("provider returned invalid paginated JSON")
            values = values[0]
        records: MutableSequence[JSONValue] = []
        for page in values:
            if not isinstance(page, list):
                raise ReadToolError("provider returned invalid paginated JSON")
            records.extend(page)
        return records


@dataclass
class ApiReadState(ExecutionContext):
    """Share resolved access and the native selected value across ordered callbacks.

    ExecutionContext and SkillState define inherited invocation and lifecycle fields.
    Private access fields are assigned by their preceding callbacks; they are excluded
    from representations and the inherited reportable state dictionary.

    :ivar selection: Provider selection assigned by SelectAPI.
    :ivar service: Credential-bound transport assigned by BindAPI.
    :param payload: Native value assigned by ReadAPI or CheckAPI, or the request
        plan assigned by PlanAPI or PlanAPICheck. The initial empty mapping
        contains no read evidence.

    .. attribute :: selection
        :type: ApiProviderSelection

        Provider and host assigned by SelectAPI before credential binding.

    .. attribute :: service
        :type: ApiReadService

        Private transport assigned by BindAPI before the read runs.

    .. attribute :: payload
        :type: JSONValue

        Selected endpoint value, identity, or nonexecuted request plan. The
        initial empty mapping contains no read evidence.
    """

    selection: ApiProviderSelection = field(init=False, repr=False)
    service: ApiReadService = field(init=False, repr=False)
    payload: JSONValue = field(default_factory=dict)


ApiActionT_co = TypeVar("ApiActionT_co", bound=ApiProviderAction, covariant=True)


class ApiSelectionCallback(LocalCallback[ApiReadState, ApiActionT_co]):
    """Share validated host selection with planning and native API callbacks."""

    def read_config(self) -> JSONObject:
        """Resolve the action's target once without reading credentials.

        :returns: Empty setup evidence; the selected host remains in shared state.
        :raises ReadToolError: The action or provider host is invalid.
        :raises TargetError: The selected runtime target is unavailable or invalid.
        """  # noqa: DOC502 - Shared action and target readers validate their inputs.
        self.action.validate()
        if not hasattr(self.state, "selection"):
            self.state.selection = ApiProviderSelection.from_action(self.action)
        return {}

    def is_configured(self) -> bool:
        """Check that the retained selection belongs to this action's provider.

        :returns: Whether the action has a concrete, matching provider selection.
        """
        return (
            hasattr(self.state, "selection")
            and self.state.selection.provider == self.action.provider
        )


class ApiProviderCallback(ApiSelectionCallback[ApiActionT_co]):
    """Share credential binding across reads using the same native provider."""

    @property
    def plugin_type(self) -> SkillPluginType:
        """Use the provider enum as the invocation's configuration cache key.

        :returns: GitHub or GitLab plugin identity selected by this action.
        """
        return SkillPluginType(self.action.provider.value)

    def read_config(self) -> JSONObject:
        """Bind one native service or retain the caller's already bound service.

        :returns: Empty setup evidence; credentials remain in private shared state.
        :raises ReadToolError: The action or provider host is invalid.
        :raises TargetError: The selected target or credential source is unavailable.
        """  # noqa: DOC502 - Shared selection and credential readers propagate failures.
        if not hasattr(self.state, "service"):
            super().read_config()
            self.state.service = ApiReadService.bind(self.state.selection)
        return {}

    def is_configured(self) -> bool:
        """Check the concrete service retained before any read starts.

        :returns: Whether this invocation has a credential-bound native transport.
        """
        return hasattr(self.state, "service")


class SelectAPI(ApiSelectionCallback[ApiProviderAction]):
    """Validate read inputs and resolve their host before credential binding."""

    def run(self) -> JSONObject:
        """Store the provider selection without reading a credential or endpoint.

        :returns: Provider and exact host selected for the following callback.
        :raises ReadToolError: If read options or provider host are invalid.
        :raises TargetError: If selected runtime configuration is unavailable or invalid.
        :raises AttributeError: If the executor has not bound shared state.
        """  # noqa: DOC502 - Action validation and target selection can fail.
        return {
            "provider": self.state.selection.provider.value,
            "host": self.state.selection.host,
        }


class BindAPI(ApiProviderCallback[ApiProviderAction]):
    """Bind the selected provider's existing credentials before its read callback."""

    def run(self) -> JSONObject:
        """Store a private native service and report only its credential reference.

        :returns: Provider host and nonsecret source reference; no credential value.
        :raises TargetError: If the selected credential source is invalid.
        :raises AttributeError: If shared state or its preceding selection is absent.
        """  # noqa: DOC502 - Credential binding and unassigned state can fail.
        return {
            "host": self.state.service.session.host,
            "credential_source": self.state.service.session.credential_source,
        }


class ReadAPI(ApiProviderCallback[ApiReadAction]):
    """Read through the already bound service and retain one native selected value."""

    def run(self) -> JSONObject:
        """Store the selected payload and return its stable callback envelope.

        :returns: A mapping with exactly ``value`` containing the selected JSON value,
            including an empty object, array, scalar, or text. CLI rendering reads
            the same native value from shared state.
        :raises ReadToolError: If read options or response transformations are invalid.
        :raises APIError: If the provider read, retry, or output-size bound fails.
        :raises OSError: If the native provider process cannot be started.
        :raises AttributeError: If state or its preceding service binding is absent.
        """  # noqa: DOC502 - Native reads and response selection propagate failures.
        self.state.payload = self.state.service.read(self.action)
        return {"value": self.state.payload}


class CheckAPI(ApiProviderCallback[ApiCheckAction]):
    """Read one authenticated identity through the already bound provider service."""

    def run(self) -> JSONObject:
        """Store the selected identity and return its stable callback envelope.

        :returns: A mapping whose ``value`` is the identity name or identity object.
        :raises ReadToolError: If identity inputs or the provider identity are invalid.
        :raises APIError: If authentication, transport, retries, or response bounds fail.
        :raises OSError: If the native provider process cannot be started.
        :raises AttributeError: If state or its preceding service binding is absent.
        """  # noqa: DOC502 - The bound service and state enforce these conditions.
        self.state.payload = self.state.service.check(self.action)
        return {"value": self.state.payload}


class PlanAPI(ApiSelectionCallback[ApiReadAction]):
    """Describe a resolved request without constructing credential-bound access."""

    def run(self) -> JSONObject:
        """Store the nonexecuted request plan for the installed CLI and later callbacks.

        :returns: Existing request-plan fields with the selected host and transformations.
        :raises AttributeError: If state or its preceding provider selection is absent.
        """  # noqa: DOC502 - Unassigned execution state raises AttributeError.
        action = self.action
        plan: JSONObject = {
            "mode": "dry-run",
            "operation": "get",
            "provider": action.provider.value,
            "host": self.state.selection.host,
            "endpoint": action.endpoint,
            "raw": action.raw,
            "field": action.field if action.field is not None else "",
            "decodeBase64": action.decode_base64,
            "lines": action.lines if action.lines is not None else "",
            "paginate": action.paginate,
        }
        self.state.payload = plan
        return plan


class PlanAPICheck(ApiSelectionCallback[ApiCheckAction]):
    """Describe the identity request without reading credentials or provider data."""

    def run(self) -> JSONObject:
        """Store the existing wire-format plan for an authenticated identity check.

        :returns: Identity request plan with fixed endpoint and no transformations.
        :raises AttributeError: If state or its preceding provider selection is absent.
        """  # noqa: DOC502 - Unassigned execution state raises AttributeError.
        plan: JSONObject = {
            "mode": "dry-run",
            "operation": "check",
            "provider": self.action.provider.value,
            "host": self.state.selection.host,
            "endpoint": "user",
            "raw": False,
            "field": "",
            "decodeBase64": False,
            "lines": "",
            "paginate": False,
        }
        self.state.payload = plan
        return plan
