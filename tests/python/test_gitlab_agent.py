"""Offline native GitLab Agent provider contracts."""

from __future__ import annotations

import json
from enum import StrEnum
from types import SimpleNamespace
from typing import Final
from unittest.mock import Mock, call, create_autospec

import pytest
from conftest import import_script_module

AGENT = import_script_module("lib.python.core.gitlab.cluster_agent.provider")
API = import_script_module("lib.python.core.gitlab.gitlab_api")
SESSION = import_script_module("lib.python.core.gitlab.gitlab_session")

PROJECT_ID: Final = 42
AGENT_ID: Final = 17
TOKEN_ID: Final = 29
PROJECT_PATH: Final = "consumer/project"
AGENT_NAME: Final = "consumer-agent"
TOKEN_NAME: Final = "owned-agent-token"
TOKEN_SECRET: Final = "unit-credential-from-creation-response"
PROJECT_ROUTE: Final = f"projects/{PROJECT_ID}"
AGENTS_ROUTE: Final = f"{PROJECT_ROUTE}/cluster_agents"
AGENT_ROUTE: Final = f"{AGENTS_ROUTE}/{AGENT_ID}"
TOKENS_ROUTE: Final = f"{AGENT_ROUTE}/tokens"
TOKEN_ROUTE: Final = f"{TOKENS_ROUTE}/{TOKEN_ID}"
TOKEN_LIST_ROUTE: Final = f"{TOKENS_ROUTE}?per_page=100&page=1"
GRAPHQL_ROUTE: Final = "graphql"
NOT_FOUND: Final = "provider_404"
INVALID_SHAPE: Final = "invalid_shape"
PROJECT_MISMATCH: Final = "agent_project_identity_mismatch"
AGENT_MISMATCH: Final = "agent_registration_identity_mismatch"
TOKEN_MISMATCH: Final = "agent_token_identity_mismatch"
SECRET_UNAVAILABLE: Final = "agent_token_secret_unavailable"
NATIVE_ID: Final = "id"
NATIVE_NAME: Final = "name"
CONFIG_PROJECT: Final = "config_project"
PROJECT_PATH_FIELD: Final = "path_with_namespace"
TOKEN_AGENT: Final = "agent_id"
TOKEN_FIELD: Final = "token"
PROJECT_GID: Final = f"gid://gitlab/Project/{PROJECT_ID}"
AGENT_GID: Final = f"gid://gitlab/Clusters::Agent/{AGENT_ID}"
CONNECTION_PATH: Final = ("data", "project", "clusterAgent", "connections")
METADATA_PATH: Final = (*CONNECTION_PATH, "nodes", 0, "metadata")
CONNECTED_AT: Final = "2026-01-01T01:02:03Z"
CONNECTION_ID: Final = -103
POD_NAME: Final = "consumer-agent-pod"
POD_NAMESPACE: Final = "consumer-agent-system"
AGENT_VERSION: Final = "v18.5.2"
AGENT_COMMIT: Final = "a" * 40

PROJECT = AGENT.AgentProject(PROJECT_ID, PROJECT_PATH)
REGISTRATION = AGENT.AgentRegistration(AGENT_ID, AGENT_NAME, PROJECT_ID)
TOKEN = AGENT.AgentToken(TOKEN_ID, TOKEN_NAME, AGENT_ID)


class Operation(StrEnum):
    """Native provider requests exercised through their public methods."""

    PROJECT = "project"
    DELETE_AGENT = "delete_agent"
    DELETE_TOKEN = "delete_token"
    CONNECT = "connections"


# Each test decodes its own wire payloads. The shared source cannot be mutated.
WIRE_BASELINE: Final = json.dumps(
    {
        Operation.PROJECT: {NATIVE_ID: PROJECT_ID, PROJECT_PATH_FIELD: PROJECT_PATH},
        Operation.DELETE_AGENT: {
            NATIVE_ID: AGENT_ID,
            NATIVE_NAME: AGENT_NAME,
            CONFIG_PROJECT: {NATIVE_ID: PROJECT_ID},
        },
        Operation.DELETE_TOKEN: {
            NATIVE_ID: TOKEN_ID,
            NATIVE_NAME: TOKEN_NAME,
            TOKEN_AGENT: AGENT_ID,
            "status": "active",
            TOKEN_FIELD: TOKEN_SECRET,
        },
        Operation.CONNECT: {
            "data": {
                "project": {
                    NATIVE_ID: PROJECT_GID,
                    "fullPath": PROJECT_PATH,
                    "clusterAgent": {
                        NATIVE_ID: AGENT_GID,
                        NATIVE_NAME: AGENT_NAME,
                        "connections": {
                            "nodes": [
                                {
                                    "connectionId": CONNECTION_ID,
                                    "connectedAt": CONNECTED_AT,
                                    "metadata": {
                                        "podName": POD_NAME,
                                        "podNamespace": POD_NAMESPACE,
                                        "version": AGENT_VERSION,
                                        "commit": AGENT_COMMIT,
                                    },
                                }
                            ],
                            "pageInfo": {"hasNextPage": False},
                        },
                    },
                }
            }
        },
    }
)


@pytest.fixture
def native() -> tuple[AGENT.AgentProvider, Mock, SimpleNamespace]:
    """Supply a bound numeric project with a signature-checked API double."""
    session = SimpleNamespace(target_kind=Operation.PROJECT)
    api = create_autospec(API.GlabAPIClient, instance=True)
    service = SESSION.GitLabService(api, session, PROJECT_ID)
    return AGENT.AgentProvider(service), api, session


def test_provider_reads_project_and_exact_active_connection(
    native: tuple[AGENT.AgentProvider, Mock, SimpleNamespace],
) -> None:
    """Bind the actual path, IDs, and Pod metadata to one GraphQL observation."""
    provider, api, session = native
    wire = json.loads(WIRE_BASELINE)
    api.get_json.return_value = wire[Operation.PROJECT]
    api.post_json.return_value = wire[Operation.CONNECT]

    assert provider.project() == PROJECT
    assert provider.connections(PROJECT, REGISTRATION) == (
        AGENT.AgentConnection(
            str(CONNECTION_ID),
            CONNECTED_AT,
            POD_NAME,
            POD_NAMESPACE,
            AGENT_VERSION,
            AGENT_COMMIT,
        ),
    )

    api.get_json.assert_called_once_with(session, PROJECT_ROUTE)
    api.post_json.assert_called_once()
    arguments = api.post_json.call_args.args
    assert arguments[:2] == (session, GRAPHQL_ROUTE)
    assert arguments[2]["variables"] == {
        "project": PROJECT_PATH,
        NATIVE_NAME: AGENT_NAME,
    }
    api.request_no_content.assert_not_called()


@pytest.mark.parametrize(
    ("operation", "field", "replacement", "reason"),
    (
        (Operation.PROJECT, (NATIVE_ID,), True, INVALID_SHAPE),
        (Operation.PROJECT, (NATIVE_ID,), str(PROJECT_ID), INVALID_SHAPE),
        (Operation.PROJECT, (NATIVE_ID,), 0, INVALID_SHAPE),
        (Operation.PROJECT, (NATIVE_ID,), PROJECT_ID + 1, PROJECT_MISMATCH),
        (
            Operation.DELETE_AGENT,
            (CONFIG_PROJECT, NATIVE_ID),
            PROJECT_ID + 1,
            PROJECT_MISMATCH,
        ),
        (Operation.DELETE_AGENT, (NATIVE_ID,), AGENT_ID + 1, AGENT_MISMATCH),
        (Operation.DELETE_AGENT, (NATIVE_NAME,), "replacement-agent", AGENT_MISMATCH),
        (Operation.DELETE_TOKEN, (TOKEN_AGENT,), AGENT_ID + 1, TOKEN_MISMATCH),
        (Operation.DELETE_TOKEN, (NATIVE_ID,), TOKEN_ID + 1, TOKEN_MISMATCH),
        (Operation.DELETE_TOKEN, (NATIVE_NAME,), "replacement-token", TOKEN_MISMATCH),
        (
            Operation.CONNECT,
            ("data", "project", "fullPath"),
            "other/project",
            PROJECT_MISMATCH,
        ),
        (
            Operation.CONNECT,
            ("data", "project", "clusterAgent", NATIVE_ID),
            f"{AGENT_GID}0",
            AGENT_MISMATCH,
        ),
        (
            Operation.CONNECT,
            (*CONNECTION_PATH, "pageInfo", "hasNextPage"),
            True,
            "agent_connection_collection_incomplete",
        ),
        (Operation.CONNECT, (*METADATA_PATH, "podName"), None, INVALID_SHAPE),
        (
            Operation.CONNECT,
            ("errors",),
            [{"message": "query failed"}],
            "agent_graphql_failed",
        ),
    ),
)
def test_provider_rejects_one_native_identity_or_response_change(
    native: tuple[AGENT.AgentProvider, Mock, SimpleNamespace],
    operation: Operation,
    field: tuple[str | int, ...],
    replacement: object,
    reason: str,
) -> None:
    """Refuse untrusted observations before a write can affect stale identities."""
    provider, api, session = native
    reply = json.loads(WIRE_BASELINE)[operation]
    parent = reply
    for key in field[:-1]:
        parent = parent[key]
    parent[field[-1]] = replacement

    if operation is Operation.CONNECT:
        api.post_json.return_value = reply
        with pytest.raises(API.GitLabAPIError, match=f"^{reason}$"):
            provider.connections(PROJECT, REGISTRATION)
        api.get_json.assert_not_called()
        api.post_json.assert_called_once()
        assert api.post_json.call_args.args[:2] == (session, GRAPHQL_ROUTE)
    else:
        api.get_json.return_value = reply
        route, invoke = {
            Operation.PROJECT: (PROJECT_ROUTE, provider.project),
            Operation.DELETE_AGENT: (
                AGENT_ROUTE,
                lambda: provider.delete_agent(REGISTRATION),
            ),
            Operation.DELETE_TOKEN: (
                TOKEN_ROUTE,
                lambda: provider.delete_token(AGENT_ID, TOKEN),
            ),
        }[operation]
        with pytest.raises(API.GitLabAPIError, match=f"^{reason}$"):
            invoke()
        api.get_json.assert_called_once_with(session, route)
        api.post_json.assert_not_called()
    api.request_no_content.assert_not_called()


def test_provider_creates_registration_and_one_time_token_separately(
    native: tuple[AGENT.AgentProvider, Mock, SimpleNamespace],
) -> None:
    """Prove the returned identity is usable before consuming the secret once."""
    provider, api, session = native
    wire = json.loads(WIRE_BASELINE)
    api.post_json.side_effect = [
        wire[Operation.DELETE_AGENT],
        wire[Operation.DELETE_TOKEN],
    ]

    assert provider.create_agent(AGENT_NAME) == REGISTRATION
    created = provider.create_token(AGENT_ID, TOKEN_NAME)
    assert created.identity == TOKEN
    assert TOKEN_SECRET not in repr(created)
    assert TOKEN_SECRET not in repr(created.identity)
    assert created.take_secret() == TOKEN_SECRET
    with pytest.raises(API.GitLabAPIError, match=f"^{SECRET_UNAVAILABLE}$"):
        created.take_secret()

    assert api.post_json.call_args_list == [
        call(session, AGENTS_ROUTE, {NATIVE_NAME: AGENT_NAME}),
        call(session, TOKENS_ROUTE, {NATIVE_NAME: TOKEN_NAME}),
    ]
    api.get_json.assert_not_called()
    api.request_no_content.assert_not_called()


def test_provider_retains_created_token_identity_without_plaintext(
    native: tuple[AGENT.AgentProvider, Mock, SimpleNamespace],
) -> None:
    """Keep the known native ID for reconciliation when plaintext is unavailable."""
    provider, api, session = native
    reply = json.loads(WIRE_BASELINE)[Operation.DELETE_TOKEN]
    del reply[TOKEN_FIELD]
    api.post_json.return_value = reply

    created = provider.create_token(AGENT_ID, TOKEN_NAME)
    assert created.identity == TOKEN
    with pytest.raises(API.GitLabAPIError, match=f"^{SECRET_UNAVAILABLE}$"):
        created.take_secret()

    api.post_json.assert_called_once_with(
        session, TOKENS_ROUTE, {NATIVE_NAME: TOKEN_NAME}
    )
    api.get_json.assert_not_called()
    api.request_no_content.assert_not_called()


@pytest.mark.parametrize("resource", (Operation.DELETE_AGENT, Operation.DELETE_TOKEN))
def test_provider_propagates_uncertain_creation_without_retry(
    native: tuple[AGENT.AgentProvider, Mock, SimpleNamespace], resource: Operation
) -> None:
    """Return the original uncertainty to workflow without inventing ownership."""
    provider, api, session = native
    uncertain = API.GitLabAPIError("timeout")
    api.post_json.side_effect = uncertain

    route, name, create = {
        Operation.DELETE_AGENT: (AGENTS_ROUTE, AGENT_NAME, provider.create_agent),
        Operation.DELETE_TOKEN: (
            TOKENS_ROUTE,
            TOKEN_NAME,
            lambda name: provider.create_token(AGENT_ID, name),
        ),
    }[resource]
    with pytest.raises(API.GitLabAPIError) as caught:
        create(name)

    assert caught.value is uncertain
    assert API.uncertain_write(caught.value)
    api.post_json.assert_called_once_with(session, route, {NATIVE_NAME: name})
    api.get_json.assert_not_called()
    api.request_no_content.assert_not_called()


def test_provider_deletes_exact_owned_identities_and_repeats_without_writes(
    native: tuple[AGENT.AgentProvider, Mock, SimpleNamespace],
) -> None:
    """Verify both native absences and keep the second invocation write-free."""
    provider, api, session = native
    wire = json.loads(WIRE_BASELINE)
    api.get_json.side_effect = [
        wire[Operation.DELETE_TOKEN],
        API.GitLabAPIError(NOT_FOUND),
        wire[Operation.DELETE_AGENT],
        [],
        API.GitLabAPIError(NOT_FOUND),
        API.GitLabAPIError(NOT_FOUND),
        API.GitLabAPIError(NOT_FOUND),
    ]

    assert provider.delete_token(AGENT_ID, TOKEN) is True
    assert provider.delete_agent(REGISTRATION) is True
    assert provider.delete_token(AGENT_ID, TOKEN) is False
    assert provider.delete_agent(REGISTRATION) is False
    assert api.mock_calls == [
        call.get_json(session, TOKEN_ROUTE),
        call.request_no_content(session, "DELETE", TOKEN_ROUTE),
        call.get_json(session, TOKEN_ROUTE),
        call.get_json(session, AGENT_ROUTE),
        call.get_json(session, TOKEN_LIST_ROUTE),
        call.request_no_content(session, "DELETE", AGENT_ROUTE),
        call.get_json(session, AGENT_ROUTE),
        call.get_json(session, TOKEN_ROUTE),
        call.get_json(session, AGENT_ROUTE),
    ]
