"""GitLab project Agent registration, tokens, and active connections."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional, TypeVar
from urllib.parse import urlsplit

from lib.python.core.gitlab.gitlab_api import GitLabAPIError
from lib.python.core.gitlab.gitlab_session import GitLabService
from lib.python.core.json_types import (
    JSONDocument,
    JSONObject,
    JSONValue,
    json_integer,
    json_object,
    json_objects,
    json_string,
)

_NOT_FOUND = "provider_404"
_PAGE_SIZE = 100
_MAX_PAGES = 20
_GRAPHQL = """
query AgentConnection($project: ID!, $name: String!) {
  project(fullPath: $project) {
    id
    fullPath
    clusterAgent(name: $name) {
      id
      name
      connections(first: 10) {
        nodes {
          connectedAt
          connectionId
          metadata { podName podNamespace version commit }
        }
        pageInfo { hasNextPage }
      }
    }
  }
}
"""
_Record = TypeVar("_Record")


@dataclass(frozen=True)
class AgentProject:
    """Nonsecret native identity of an access-verified project.

    .. attribute :: id
        :type: int

        Native numeric GitLab project ID.

    .. attribute :: path
        :type: str

        Canonical namespace-qualified GitLab project path.
    """

    id: int
    path: str


@dataclass(frozen=True)
class KASMetadata:
    """Native GitLab and Agent Server versions and advertised endpoints.

    .. attribute :: gitlab_version
        :type: str

        Version reported by the selected GitLab instance.

    .. attribute :: version
        :type: str

        KAS version reported by selected GitLab metadata.

    .. attribute :: address
        :type: str

        Advertised address used by the installed Agent.

    .. attribute :: proxy_url
        :type: str

        Advertised Kubernetes proxy URL used by CI.
    """

    gitlab_version: str
    version: str
    address: str
    proxy_url: str


@dataclass(frozen=True)
class AgentRegistration:
    """Native registration scoped to its configuration project.

    .. attribute :: id
        :type: int

        Native numeric registration ID.

    .. attribute :: name
        :type: str

        GitLab Agent name.

    .. attribute :: project_id
        :type: int

        Native ID of the registration's configuration project.
    """

    id: int
    name: str
    project_id: int


@dataclass(frozen=True)
class AgentToken:
    """Public identity of a GitLab Agent token.

    .. attribute :: id
        :type: int

        Native numeric token ID.

    .. attribute :: name
        :type: str

        Provider-visible token name.

    .. attribute :: agent_id
        :type: int

        Native numeric Agent ID that owns this token.
    """

    id: int
    name: str
    agent_id: int


class CreatedAgentToken:
    """Keep the one-time secret separate from serializable token identity."""

    __slots__ = ("_secret", "identity")

    def __init__(self, identity: AgentToken, secret: Optional[str]) -> None:
        """Store the native identity separately from the single-use secret.

        :param identity: Public token identity from the create response.
        :param secret: Token plaintext returned by GitLab, if available.
        """
        self.identity = identity
        self._secret = secret

    def take_secret(self) -> str:
        """Transfer the secret for Secret creation after persisting token identity.

        :returns: Single-use plaintext for the caller's Kubernetes Secret.
        :raises GitLabAPIError: If the plaintext was unavailable or already taken.
        """
        secret, self._secret = self._secret, None
        if secret is None:
            raise GitLabAPIError("agent_token_secret_unavailable")
        return secret


@dataclass(frozen=True)
class AgentConnection:
    """Native KAS connection correlated to a Kubernetes Pod by the caller.

    .. attribute :: connection_id
        :type: str

        GitLab connection ID in decimal form.

    .. attribute :: connected_at
        :type: str

        Timestamp reported by the active connection query.

    .. attribute :: pod_name
        :type: str

        Pod name reported by the connected Agent.

    .. attribute :: pod_namespace
        :type: str

        Pod namespace reported by the connected Agent.

    .. attribute :: agent_version
        :type: str

        Version reported by the connected Agent.

    .. attribute :: agent_commit
        :type: str

        Commit reported by the connected Agent.
    """

    connection_id: str
    connected_at: str
    pod_name: str
    pod_namespace: str
    agent_version: str
    agent_commit: str


def _text(value: JSONValue) -> str:
    text = json_string(value)
    if not text or any(char.isspace() or not char.isprintable() for char in text):
        raise ValueError("invalid_provider_text")
    return text


def _id(value: JSONValue) -> int:
    number = json_integer(value)
    if number <= 0:
        raise ValueError("invalid_provider_id")
    return number


def _url(value: JSONValue, schemes: tuple[str, ...]) -> str:
    address = _text(value)
    parsed = urlsplit(address)
    if (
        parsed.scheme not in schemes
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid_kas_endpoint")
    return address


def _decode(
    parse: Callable[[JSONDocument], _Record], document: JSONDocument
) -> _Record:
    try:
        return parse(document)
    except (TypeError, ValueError):
        raise GitLabAPIError("invalid_shape") from None


def _project(document: JSONDocument) -> AgentProject:
    payload = json_object(document)
    return AgentProject(
        _id(payload.get("id")), _text(payload.get("path_with_namespace"))
    )


def _kas(document: JSONDocument) -> KASMetadata:
    payload = json_object(document)
    kas = json_object(payload.get("kas"))
    if kas.get("enabled") is not True:
        raise GitLabAPIError("kas_not_enabled")
    return KASMetadata(
        _text(payload.get("version")),
        _text(kas.get("version")),
        _url(kas.get("externalUrl"), ("grpcs", "wss", "grpc", "ws")),
        _url(kas.get("externalK8sProxyUrl"), ("https",)),
    )


def _registration(document: JSONDocument) -> AgentRegistration:
    payload = json_object(document)
    config_project = json_object(payload.get("config_project"))
    return AgentRegistration(
        _id(payload.get("id")),
        _text(payload.get("name")),
        _id(config_project.get("id")),
    )


def _token(document: JSONDocument) -> AgentToken:
    payload = json_object(document)
    if payload.get("status") != "active":
        raise GitLabAPIError("agent_token_not_active")
    return AgentToken(
        _id(payload.get("id")), _text(payload.get("name")), _id(payload.get("agent_id"))
    )


def _connection(payload: JSONObject) -> AgentConnection:
    value = payload.get("connectionId")
    connection_id = str(value) if type(value) is int else _text(value)
    if re.fullmatch(r"-?[0-9]{1,20}", connection_id) is None:
        raise ValueError("invalid_agent_connection_id")
    metadata = json_object(payload.get("metadata"))
    return AgentConnection(
        connection_id,
        _text(payload.get("connectedAt")),
        _text(metadata.get("podName")),
        _text(metadata.get("podNamespace")),
        _text(metadata.get("version")),
        _text(metadata.get("commit")),
    )


def _connections(
    document: JSONDocument, project: AgentProject, agent: AgentRegistration
) -> tuple[AgentConnection, ...]:
    payload = json_object(document)
    if "errors" in payload:
        raise GitLabAPIError("agent_graphql_failed")
    observed_project = json_object(json_object(payload.get("data")).get("project"))
    if (
        observed_project.get("id") != f"gid://gitlab/Project/{project.id}"
        or observed_project.get("fullPath") != project.path
    ):
        raise GitLabAPIError("agent_project_identity_mismatch")
    observed = json_object(observed_project.get("clusterAgent"))
    if (
        observed.get("id") != f"gid://gitlab/Clusters::Agent/{agent.id}"
        or observed.get("name") != agent.name
    ):
        raise GitLabAPIError("agent_registration_identity_mismatch")
    connections = json_object(observed.get("connections"))
    if json_object(connections.get("pageInfo")).get("hasNextPage") is not False:
        raise GitLabAPIError("agent_connection_collection_incomplete")
    result = tuple(_connection(item) for item in json_objects(connections.get("nodes")))
    if len({item.connection_id for item in result}) != len(result):
        raise GitLabAPIError("agent_connection_identity_ambiguous")
    return result


class AgentProvider:
    """Operate on one existing bound GitLab project and native numeric IDs."""

    def __init__(self, service: GitLabService) -> None:
        """Bind the Agent API to the selected project.

        :param service: Existing access-verified GitLab service.
        :raises GitLabAPIError: If the authenticated target is not a project.
        """
        if service.session.target_kind != "project":
            raise GitLabAPIError("agent_requires_project")
        self._service = service
        self._project_endpoint = f"projects/{_id(service.target_id)}"
        self._agent_endpoint = f"{self._project_endpoint}/cluster_agents"

    def _read_optional(self, endpoint: str) -> Optional[JSONDocument]:
        service = self._service
        try:
            return service.api.get_json(service.session, endpoint)
        except GitLabAPIError as exc:
            if exc.reason != _NOT_FOUND:
                raise
            return None

    def _list(self, endpoint: str) -> tuple[JSONObject, ...]:
        service = self._service
        records: list[JSONObject] = []
        ids: set[int] = set()
        for page in range(1, _MAX_PAGES + 1):
            response = service.api.get_json(
                service.session, f"{endpoint}?per_page={_PAGE_SIZE}&page={page}"
            )
            current = _decode(json_objects, response)
            if len(current) > _PAGE_SIZE:
                raise GitLabAPIError("agent_provider_collection_invalid")
            for item in current:
                identity = _decode(
                    lambda value: _id(json_object(value).get("id")), item
                )
                if identity in ids:
                    raise GitLabAPIError("agent_provider_collection_duplicate")
                ids.add(identity)
                records.append(item)
            if len(current) < _PAGE_SIZE:
                return tuple(records)
        raise GitLabAPIError("agent_provider_collection_incomplete")

    def _agent_route(self, agent_id: int) -> str:
        return f"{self._agent_endpoint}/{_id(agent_id)}"

    def _token_route(self, agent_id: int, token_id: int) -> str:
        return f"{self._agent_route(agent_id)}/tokens/{_id(token_id)}"

    def _bound_agent(self, response: JSONDocument) -> AgentRegistration:
        agent = _decode(_registration, response)
        if agent.project_id != self._service.target_id:
            raise GitLabAPIError("agent_project_identity_mismatch")
        return agent

    def project(self) -> AgentProject:
        """Read the canonical project path from the selected numeric project.

        :returns: Project identity with its native numeric ID and canonical path.
        :raises GitLabAPIError: If the read fails or the native ID differs.
        """
        service = self._service
        project = _decode(
            _project, service.api.get_json(service.session, self._project_endpoint)
        )
        if project.id != service.target_id:
            raise GitLabAPIError("agent_project_identity_mismatch")
        return project

    def kas(self) -> KASMetadata:
        """Observe enabled KAS and its advertised endpoints from selected GitLab.

        :returns: Enabled KAS metadata with advertised endpoints and versions.
        """
        service = self._service
        return _decode(_kas, service.api.get_json(service.session, "metadata"))

    def find_agent(self, name: str) -> Optional[AgentRegistration]:
        """Resolve the logical name, rejecting incomplete or ambiguous inventories.

        :param name: Exact Agent name within the selected project.
        :returns: A unique matching registration, or None if none exists.
        :raises GitLabAPIError: If discovery fails, is incomplete, or finds duplicates.
        """
        agents = [self._bound_agent(item) for item in self._list(self._agent_endpoint)]
        matches = [agent for agent in agents if agent.name == name]
        if len(matches) > 1:
            raise GitLabAPIError("agent_registration_identity_ambiguous")
        return matches[0] if matches else None

    def read_agent(self, agent_id: int) -> Optional[AgentRegistration]:
        """Read the exact registration, distinguishing native 404 from failure.

        :param agent_id: Native numeric registration ID.
        :returns: Matching registration, or None after an exact 404.
        :raises GitLabAPIError: If the read fails or the returned identity differs.
        """
        response = self._read_optional(self._agent_route(agent_id))
        if response is None:
            return None
        agent = self._bound_agent(response)
        if agent.id != agent_id:
            raise GitLabAPIError("agent_registration_identity_mismatch")
        return agent

    def create_agent(self, name: str) -> AgentRegistration:
        """Create once; the caller persists this identity before independent read-back.

        :param name: Agent name to register with the selected project.
        :returns: Native create response identity to persist before read-back.
        :raises GitLabAPIError: If creation fails or returns an invalid identity.
        """
        service = self._service
        agent = self._bound_agent(
            service.api.post_json(service.session, self._agent_endpoint, {"name": name})
        )
        if agent.name != name:
            raise GitLabAPIError("invalid_shape")
        return agent

    def list_tokens(self, agent_id: int) -> tuple[AgentToken, ...]:
        """Read the active tokens belonging to the exact registration.

        :param agent_id: Native numeric registration ID.
        :returns: Complete active token identities for the selected Agent.
        :raises GitLabAPIError: If discovery fails, is incomplete, or identity differs.
        """
        endpoint = f"{self._agent_route(agent_id)}/tokens"
        records = tuple(_decode(_token, item) for item in self._list(endpoint))
        if any(token.agent_id != agent_id for token in records):
            raise GitLabAPIError("agent_token_identity_mismatch")
        return records

    def read_token(self, agent_id: int, token_id: int) -> Optional[AgentToken]:
        """Read one active token identity, with native 404 meaning absent.

        :param agent_id: Native numeric registration ID.
        :param token_id: Native numeric token ID.
        :returns: Matching active token, or None after an exact 404.
        :raises GitLabAPIError: If the read fails or the returned identity differs.
        """
        response = self._read_optional(self._token_route(agent_id, token_id))
        if response is None:
            return None
        token = _decode(_token, response)
        if token.id != token_id or token.agent_id != agent_id:
            raise GitLabAPIError("agent_token_identity_mismatch")
        return token

    def create_token(self, agent_id: int, name: str) -> CreatedAgentToken:
        """Create once and retain the known ID even if plaintext is unavailable.

        :param agent_id: Native numeric registration ID.
        :param name: Name for the token to create.
        :returns: Native token identity and a single-use plaintext accessor.
        :raises GitLabAPIError: If creation fails or returns an invalid identity.
        """
        service = self._service
        endpoint = f"{self._agent_route(agent_id)}/tokens"
        response = service.api.post_json(service.session, endpoint, {"name": name})
        token = _decode(_token, response)
        if token.agent_id != agent_id or token.name != name:
            raise GitLabAPIError("invalid_shape")
        secret = json_object(response).get("token")
        if (
            not isinstance(secret, str)
            or not secret
            or not all(char.isprintable() and not char.isspace() for char in secret)
        ):
            secret = None
        return CreatedAgentToken(token, secret)

    def _delete(self, endpoint: str) -> bool:
        service = self._service
        try:
            service.api.request_no_content(service.session, "DELETE", endpoint)
        except GitLabAPIError as exc:
            if exc.reason != _NOT_FOUND:
                raise
            return False
        return True

    def delete_token(self, agent_id: int, expected: AgentToken) -> bool:
        """Revoke only the reverified owned token, then prove native absence.

        :param agent_id: Native numeric registration ID.
        :param expected: Exact token identity from the run ownership record.
        :returns: Whether native deletion occurred before absence was verified.
        :raises GitLabAPIError: If identity, deletion, or read-back fails.
        """
        if expected.agent_id != agent_id:
            raise GitLabAPIError("agent_token_identity_mismatch")
        token = self.read_token(agent_id, expected.id)
        if token is None:
            return False
        if token != expected:
            raise GitLabAPIError("agent_token_identity_mismatch")
        deleted = self._delete(self._token_route(agent_id, expected.id))
        if self.read_token(agent_id, expected.id) is not None:
            raise GitLabAPIError("agent_token_delete_unverified")
        return deleted

    def delete_agent(self, expected: AgentRegistration) -> bool:
        """Delete only the reverified registration and refuse implicit token removal.

        :param expected: Exact registration from the run ownership record.
        :returns: Whether native deletion occurred before absence was verified.
        :raises GitLabAPIError: If identity, token inventory, deletion, or read-back fails.
        """
        if expected.project_id != self._service.target_id:
            raise GitLabAPIError("agent_project_identity_mismatch")
        agent = self.read_agent(expected.id)
        if agent is None:
            return False
        if agent != expected:
            raise GitLabAPIError("agent_registration_identity_mismatch")
        if self.list_tokens(agent.id):
            raise GitLabAPIError("agent_delete_active_tokens_present")
        deleted = self._delete(self._agent_route(expected.id))
        if self.read_agent(expected.id) is not None:
            raise GitLabAPIError("agent_registration_delete_unverified")
        return deleted

    def connections(
        self, project: AgentProject, expected: AgentRegistration
    ) -> tuple[AgentConnection, ...]:
        """Read active KAS connections for one exact native project and Agent.

        :param project: Access-verified project ID and path.
        :param expected: Exact Agent registration within that project.
        :returns: Complete active connections returned by GitLab GraphQL.
        :raises GitLabAPIError: If native identity, response, or completeness differs.
        """
        service = self._service
        if project.id != service.target_id or expected.project_id != project.id:
            raise GitLabAPIError("agent_project_identity_mismatch")
        response = service.api.post_json(
            service.session,
            "graphql",
            {
                "query": _GRAPHQL,
                "variables": {"project": project.path, "name": expected.name},
            },
        )
        return _decode(
            lambda payload: _connections(payload, project, expected), response
        )
