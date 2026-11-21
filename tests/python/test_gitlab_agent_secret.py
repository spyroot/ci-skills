"""Offline tests of one-time Agent credentials and metadata-only Secret proof."""

from __future__ import annotations

import json
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from unittest.mock import Mock, call, create_autospec

import pytest
from conftest import import_script_module

SECRET = import_script_module("lib.python.core.gitlab.cluster_agent.secret")
PROVIDER = import_script_module("lib.python.core.gitlab.cluster_agent.provider")
SELECTION = import_script_module("lib.python.core.gitlab.cluster_agent.selection")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
SCOPE = import_script_module("lib.python.core.kubernetes.run_scope")
RESOURCES = import_script_module("lib.python.core.kubernetes.resources")
RUNTIME = import_script_module("lib.python.core.runtime")

NAMESPACE: Final = "consumer-agent-system"
AGENT_NAME: Final = "consumer-agent"
RUN_ID: Final = "owned-agent-run"
SECRET_UID: Final = "native-secret-uid"
REPLACEMENT_UID: Final = "replacement-secret-uid"
CREDENTIAL: Final = "synthetic-unit-test-credential"
TOKEN_KEY: Final = "token"
IDENTITY_MISMATCH: Final = "agent_secret_identity_mismatch"
READ_UNCONFIRMED: Final = "agent_secret_read_unconfirmed"
WRITE_UNCONFIRMED: Final = "agent_secret_write_unconfirmed"
READBACK_MISMATCH: Final = "agent_secret_readback_mismatch"
TOKEN_UNAVAILABLE: Final = "agent_token_secret_unavailable"
PROJECTION_PREFIX: Final = "go-template="
SELECTION_CASE = SELECTION.ConnectSelection(
    project=SELECTION.AgentProject(42, "consumer/native-agent"),
    agent_name=AGENT_NAME,
    scope=SCOPE.RunScope(RUN_ID, NAMESPACE, False),
    release="owned-agent-release",
)
SECRET_NAME: Final = SELECTION_CASE.scope.bounded_name(
    "agent-token", limit=SCOPE.DNS_LABEL_LIMIT, prefix=AGENT_NAME
)
LABELS: Final = MappingProxyType(
    {
        RESOURCES.MANAGER_LABEL: RESOURCES.MANAGER_VALUE,
        RESOURCES.OPERATION_LABEL: RUN_ID,
        SCOPE.RUN_ID_LABEL: RUN_ID,
    }
)


class Field(StrEnum):
    """Nonsecret native fields in their selected wire order."""

    API_VERSION = "api-version"
    KIND = "kind"
    NAMESPACE = "namespace"
    NAME = "name"
    UID = "uid"
    MANAGER = "manager"
    OPERATION = "operation"
    RUN = "run"
    TYPE = "type"
    IMMUTABLE = "immutable"
    LIFETIME = "lifetime"
    KEY_MARKER = "key-marker"


BASELINE: Final = MappingProxyType(
    {
        Field.API_VERSION: "v1",
        Field.KIND: "Secret",
        Field.NAMESPACE: NAMESPACE,
        Field.NAME: SECRET_NAME,
        Field.UID: SECRET_UID,
        Field.MANAGER: LABELS[RESOURCES.MANAGER_LABEL],
        Field.OPERATION: LABELS[RESOURCES.OPERATION_LABEL],
        Field.RUN: LABELS[SCOPE.RUN_ID_LABEL],
        Field.TYPE: "Opaque",
        Field.IMMUTABLE: "true",
        Field.LIFETIME: "live",
        Field.KEY_MARKER: TOKEN_KEY,
    }
)


def _result(stdout: str, exit_code: int = 0, stderr: str = "") -> RUNTIME.CommandResult:
    """Return the scripted native projection without providing a token value."""
    return RUNTIME.CommandResult((), exit_code, stdout, stderr)


def _projection_response(field: Field | None = None, value: str = "") -> str:
    """Copy one immutable native projection and optionally vary one field."""
    fields = dict(BASELINE)
    if field is not None:
        fields[field] = value
    return "\n".join(fields.values()) + "\n"


def _read_args(projection: str) -> tuple[str, ...]:
    """Require the exact selected resource and a bounded output projection."""
    return (
        "-n",
        NAMESPACE,
        "get",
        f"secret/{SECRET_NAME}",
        "--ignore-not-found",
        "-o",
        projection,
    )


@pytest.fixture
def secret_store() -> tuple[SECRET.AgentSecretStore, Mock]:
    """Bind an isolated transport to the same recovery scope as the Agent."""
    service = create_autospec(KUBERNETES.KubernetesService, instance=True)
    return SECRET.AgentSecretStore(service, SELECTION_CASE), service


@pytest.fixture
def credential() -> PROVIDER.CreatedAgentToken:
    """Use the real single-consumption response shape with harmless plaintext."""
    return PROVIDER.CreatedAgentToken(
        PROVIDER.AgentToken(17, "owned-agent-credential", 23), CREDENTIAL
    )


def test_agent_secret_create_hands_off_plaintext_only_via_stdin(
    secret_store: tuple[SECRET.AgentSecretStore, Mock],
    credential: PROVIDER.CreatedAgentToken,
) -> None:
    """Create and read back safely without exposing native diagnostics to tracing."""
    store, service = secret_store
    trace = Mock()
    restored_event = "test.after_secret"

    def native_command(*argv: str, **_kwargs: object) -> RUNTIME.CommandResult:
        """Use the real native trace path with a simulated object diagnostic."""
        return RUNTIME._traced_result(
            argv, 0, _projection_response(), f"template object data: {CREDENTIAL}"
        )

    service.command.side_effect = native_command

    assert store.name == SECRET_NAME
    assert store.labels == LABELS
    with RUNTIME.execution_trace(trace):
        assert store.create(credential) == SECRET_UID
        RUNTIME.trace_event(restored_event, {})
    trace.assert_called_once_with(restored_event, {})

    create, read = service.command.call_args_list
    projection = create.args[-1]
    assert projection.startswith(PROJECTION_PREFIX)
    assert create.args == ("create", "-f", "-", "-o", projection)
    assert read == call(*_read_args(projection))
    assert CREDENTIAL not in repr(create.args)
    assert json.loads(create.kwargs["input_data"]) == {
        "apiVersion": BASELINE[Field.API_VERSION],
        "kind": BASELINE[Field.KIND],
        "metadata": {"name": SECRET_NAME, "namespace": NAMESPACE, "labels": LABELS},
        "type": BASELINE[Field.TYPE],
        "immutable": True,
        "stringData": {TOKEN_KEY: CREDENTIAL},
    }
    assert service.mock_calls == [
        call.command(*create.args, **create.kwargs),
        call.command(*read.args),
    ]
    with pytest.raises(PROVIDER.GitLabAPIError, match=f"^{TOKEN_UNAVAILABLE}$"):
        credential.take_secret()


@pytest.mark.parametrize("present", (False, True), ids=("absent", "owned"))
def test_agent_secret_read_returns_only_uid_or_verified_absence(
    secret_store: tuple[SECRET.AgentSecretStore, Mock], present: bool
) -> None:
    """Expose a selected UID without reading or emitting Secret contents."""
    store, service = secret_store
    service.command.return_value = _result(_projection_response() if present else "")

    assert store.read() == (SECRET_UID if present else None)
    service.command.assert_called_once()
    native = service.command.call_args
    assert native.args[-1].startswith(PROJECTION_PREFIX)
    assert native == call(*_read_args(native.args[-1]))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        (Field.API_VERSION, "v2"),
        (Field.KIND, "ConfigMap"),
        (Field.NAMESPACE, "other-namespace"),
        (Field.NAME, "other-secret"),
        (Field.UID, ""),
        (Field.MANAGER, "other-manager"),
        (Field.OPERATION, "other-operation"),
        (Field.RUN, "other-run"),
        (Field.TYPE, "other-type"),
        (Field.IMMUTABLE, "false"),
        (Field.LIFETIME, "deleting"),
        (Field.KEY_MARKER, ""),
    ),
)
def test_agent_secret_read_refuses_one_unverified_projected_field(
    secret_store: tuple[SECRET.AgentSecretStore, Mock], field: Field, value: str
) -> None:
    """Reject changed metadata without another request or a native mutation."""
    store, service = secret_store
    service.command.return_value = _result(_projection_response(field, value))

    with pytest.raises(KUBERNETES.KubernetesError, match=f"^{IDENTITY_MISMATCH}$"):
        store.read()

    service.command.assert_called_once()
    native = service.command.call_args
    assert service.mock_calls == [call.command(*_read_args(native.args[-1]))]


def test_agent_secret_read_failure_never_becomes_absence(
    secret_store: tuple[SECRET.AgentSecretStore, Mock],
) -> None:
    """Treat a failed native GET as unconfirmed instead of a missing Secret."""
    store, service = secret_store
    service.command.return_value = _result("", 1, "native read failed")

    with pytest.raises(KUBERNETES.KubernetesError, match=f"^{READ_UNCONFIRMED}$"):
        store.read()

    service.command.assert_called_once()


def test_agent_secret_create_collision_consumes_once_without_retry(
    secret_store: tuple[SECRET.AgentSecretStore, Mock],
    credential: PROVIDER.CreatedAgentToken,
) -> None:
    """Preserve the pre-existing Secret and keep provider diagnostics out."""
    store, service = secret_store
    service.command.return_value = _result("", 1, f"AlreadyExists: {CREDENTIAL}")

    with pytest.raises(KUBERNETES.KubernetesError, match=f"^{WRITE_UNCONFIRMED}$"):
        store.create(credential)

    service.command.assert_called_once()
    assert service.command.call_args.args[0] == "create"
    with pytest.raises(PROVIDER.GitLabAPIError, match=f"^{TOKEN_UNAVAILABLE}$"):
        credential.take_secret()


@pytest.mark.parametrize(
    "readback",
    ("", _projection_response(Field.UID, REPLACEMENT_UID)),
    ids=("missing", "replaced"),
)
def test_agent_secret_create_requires_exact_uid_on_independent_readback(
    secret_store: tuple[SECRET.AgentSecretStore, Mock],
    credential: PROVIDER.CreatedAgentToken,
    readback: str,
) -> None:
    """A successful write acknowledgement alone cannot prove the owned object."""
    store, service = secret_store
    service.command.side_effect = [
        _result(_projection_response()),
        _result(readback),
    ]

    with pytest.raises(KUBERNETES.KubernetesError, match=f"^{READBACK_MISMATCH}$"):
        store.create(credential)

    create, read = service.command.call_args_list
    assert create.args[0] == "create"
    assert read == call(*_read_args(create.args[-1]))
