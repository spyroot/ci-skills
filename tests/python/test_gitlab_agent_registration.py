"""Offline tests of durable Agent registration and one-time Secret handoff."""

from __future__ import annotations

import json
from copy import deepcopy
from enum import IntEnum, auto
from types import SimpleNamespace
from typing import Final
from unittest.mock import Mock, create_autospec

import pytest
import test_gitlab_agent_recovery as RECOVERY_TEST
from conftest import import_script_module
from test_gitlab_agent_recovery import (
    recovery_baseline as recovery_baseline,  # noqa: PLC0414 - shared pytest fixture
)

REGISTER = import_script_module("lib.python.core.gitlab.cluster_agent.registration")
PROVIDER = import_script_module("lib.python.core.gitlab.cluster_agent.provider")
SELECTION = RECOVERY_TEST.SELECTION
OWNERSHIP = RECOVERY_TEST.RECOVERY
KUBERNETES = RECOVERY_TEST.KUBERNETES

CHOICE: Final = RECOVERY_TEST.SELECTION_CASE
AGENT: Final = PROVIDER.AgentRegistration(
    RECOVERY_TEST.AGENT_ID, CHOICE.agent_name, CHOICE.project.id
)
TOKEN_ID: Final = 29
SECRET_UID: Final = "native-agent-secret-uid"
SECRET_VALUE: Final = "synthetic-one-time-credential"
RECORD_UID: Final = RECOVERY_TEST.RECORD_UID
AGENT_ID_FIELD: Final = "agent_id"
TOKEN_ID_FIELD: Final = "registration_credential_id"
SECRET_UID_FIELD: Final = "credential_resource_uid"
READBACK_ERROR: Final = "agent_registration_readback_mismatch"
CREDENTIAL_ERROR: Final = "agent_registration_credential_readback_mismatch"
SCOPE_ERROR: Final = "agent_registration_scope_mismatch"
PERSIST_ERROR: Final = "agent_registration_persistence_unconfirmed"
SCOPE_UIDS: Final = (RECOVERY_TEST.CLUSTER_UID, RECOVERY_TEST.NAMESPACE_UID)


class Step(IntEnum):
    """Observable dependency boundaries for a fresh registration."""

    PROJECT = auto()
    FIND_AGENT = auto()
    INITIAL_SCOPE = auto()
    FIND_SECRET = auto()
    PENDING = auto()
    CREATE_AGENT = auto()
    PERSIST_AGENT = auto()
    READ_AGENT = auto()
    INITIAL_TOKENS = auto()
    CREATE_TOKEN = auto()
    PERSIST_TOKEN = auto()
    READ_TOKEN = auto()
    FINAL_TOKENS = auto()
    FINAL_SCOPE = auto()
    CREATE_SECRET = auto()
    PERSIST_SECRET = auto()


CALLS: Final = (
    "provider.project",
    "provider.find_agent",
    "scope",
    "secret.read",
    "store.create_pending",
    "provider.create_agent",
    "store.replace",
    "provider.read_agent",
    "provider.list_tokens",
    "provider.create_token",
    "store.replace",
    "provider.read_token",
    "provider.list_tokens",
    "scope",
    "secret.create",
    "store.replace",
)


@pytest.fixture
def registration(
    monkeypatch: pytest.MonkeyPatch, recovery_baseline: str
) -> SimpleNamespace:
    """Reuse the immutable recovery baseline and wire isolated native boundaries."""
    pending = json.loads(recovery_baseline)[RECOVERY_TEST.PENDING_ROW]
    token = PROVIDER.AgentToken(
        TOKEN_ID, pending["registration_credential_name"], AGENT.id
    )
    credential = PROVIDER.CreatedAgentToken(token, SECRET_VALUE)
    gitlab = SimpleNamespace(
        target_id=CHOICE.project.id,
        session=SimpleNamespace(origin=RECOVERY_TEST.ORIGIN),
    )
    kubernetes = create_autospec(KUBERNETES.KubernetesService, instance=True)
    provider = create_autospec(PROVIDER.AgentProvider, instance=True)
    store = create_autospec(OWNERSHIP.AgentRecoveryStore, instance=True)
    store.name = RECOVERY_TEST.RECORD_NAME
    secret = create_autospec(REGISTER.AgentSecretStore, instance=True)
    secret.name = pending["credential_resource_name"]
    scope = Mock()
    journal = Mock()
    for name, mock in (
        ("provider", provider),
        ("store", store),
        ("secret", secret),
        ("scope", scope),
    ):
        journal.attach_mock(mock, name)
    monkeypatch.setattr(REGISTER, "AgentProvider", Mock(return_value=provider))
    monkeypatch.setattr(REGISTER, "AgentSecretStore", Mock(return_value=secret))
    monkeypatch.setattr(REGISTER, "AgentRecoveryStore", Mock(return_value=store))
    monkeypatch.setattr(REGISTER, "read_agent_cluster_scope", scope)

    stages = iter(Step)
    updates = iter((Step.PERSIST_AGENT, Step.PERSIST_TOKEN, Step.PERSIST_SECRET))
    scopes = iter((Step.INITIAL_SCOPE, Step.FINAL_SCOPE))
    inventories = iter((Step.INITIAL_TOKENS, Step.FINAL_TOKENS))
    case = SimpleNamespace(
        pending=pending,
        step=None,
        result=None,
        journal=journal,
        store=store,
        provider=provider,
        credential=credential,
        secret=secret,
        token=token,
        kubernetes=kubernetes,
        gitlab=gitlab,
        snapshots=[],
    )

    def observed(step: Step, value: object) -> object:
        """Allow exactly one changed native response at a selected boundary."""
        assert next(stages) is step
        reply = case.result if case.step is step else value
        if isinstance(reply, Exception):
            raise reply
        return reply

    def persist(step: Step, document: dict[str, object]) -> OWNERSHIP.RecoveryRecord:
        """Return the independent durable snapshot only after the write succeeds."""
        version = str(len(case.snapshots) + 1)
        saved = OWNERSHIP.RecoveryRecord(RECORD_UID, version, deepcopy(document))
        result = observed(step, saved)
        assert isinstance(result, OWNERSHIP.RecoveryRecord)
        case.snapshots.append(result)
        return result

    def replace(
        previous: OWNERSHIP.RecoveryRecord, document: dict[str, object]
    ) -> OWNERSHIP.RecoveryRecord:
        """Require the previous independently confirmed recovery record."""
        assert previous == case.snapshots[-1]
        return persist(next(updates), document)

    def create_secret(presented: PROVIDER.CreatedAgentToken) -> object:
        """Consume the one-time credential only at the Secret boundary."""
        assert presented is credential
        assert presented.take_secret() == SECRET_VALUE
        return observed(Step.CREATE_SECRET, SECRET_UID)

    def read_tokens(_agent_id: int) -> object:
        """Return an empty initial inventory and the exact issued token later."""
        stage = next(inventories)
        return observed(stage, () if stage is Step.INITIAL_TOKENS else (token,))

    provider.project.side_effect = lambda: observed(Step.PROJECT, CHOICE.project)
    provider.find_agent.side_effect = lambda _: observed(Step.FIND_AGENT, None)
    scope.side_effect = lambda *_: observed(next(scopes), SCOPE_UIDS)
    secret.read.side_effect = lambda: observed(Step.FIND_SECRET, None)
    store.create_pending.side_effect = lambda document: persist(Step.PENDING, document)
    provider.create_agent.side_effect = lambda _: observed(Step.CREATE_AGENT, AGENT)
    store.replace.side_effect = replace
    provider.read_agent.side_effect = lambda _: observed(Step.READ_AGENT, AGENT)
    provider.list_tokens.side_effect = read_tokens
    provider.create_token.side_effect = lambda *_: observed(
        Step.CREATE_TOKEN, credential
    )
    provider.read_token.side_effect = lambda *_: observed(Step.READ_TOKEN, token)
    secret.create.side_effect = create_secret
    return case


def _register(case: SimpleNamespace) -> OWNERSHIP.RecoveryRecord:
    """Call the shipped operation with the selected project and owned scope."""
    return REGISTER.register_fresh_agent(
        case.gitlab, case.kubernetes, CHOICE, case.pending
    )


def test_agent_registration_persists_each_identity_before_the_next_write(
    registration: SimpleNamespace,
) -> None:
    """Keep the pending and native identities durable at every dependency boundary."""
    initial = deepcopy(registration.pending)

    result = _register(registration)

    assert tuple(item[0] for item in registration.journal.mock_calls) == CALLS
    pending, agent, token, secret = [item.document for item in registration.snapshots]
    assert result == registration.snapshots[-1]
    assert result.uid == RECORD_UID
    assert registration.pending == initial
    assert [
        (row[AGENT_ID_FIELD], row[TOKEN_ID_FIELD], row[SECRET_UID_FIELD])
        for row in (pending, agent, token, secret)
    ] == [
        (None, None, None),
        (AGENT.id, None, None),
        (AGENT.id, TOKEN_ID, None),
        (AGENT.id, TOKEN_ID, SECRET_UID),
    ]
    assert [
        [(obj["kind"], obj["uid"], obj["created"]) for obj in row["resources"]]
        for row in (pending, agent, token, secret)
    ] == [
        [("Namespace", RECOVERY_TEST.NAMESPACE_UID, False)],
        [
            ("Namespace", RECOVERY_TEST.NAMESPACE_UID, False),
            ("ConfigMap", RECORD_UID, True),
        ],
        [
            ("Namespace", RECOVERY_TEST.NAMESPACE_UID, False),
            ("ConfigMap", RECORD_UID, True),
        ],
        [
            ("Namespace", RECOVERY_TEST.NAMESPACE_UID, False),
            ("ConfigMap", RECORD_UID, True),
            ("Secret", SECRET_UID, True),
        ],
    ]
    assert all(
        row["ownership_record_uid"] == RECORD_UID for row in (agent, token, secret)
    )
    assert SECRET_VALUE not in json.dumps(secret)
    registration.provider.create_agent.assert_called_once_with(CHOICE.agent_name)
    registration.provider.create_token.assert_called_once_with(
        AGENT.id, initial["registration_credential_name"]
    )
    registration.provider.read_token.assert_called_once_with(AGENT.id, TOKEN_ID)
    registration.secret.create.assert_called_once_with(registration.credential)


@pytest.mark.parametrize(
    ("step", "reply", "error", "known_field"),
    (
        (
            Step.PROJECT,
            SELECTION.AgentProject(CHOICE.project.id + 1, CHOICE.project.path),
            "agent_registration_project_mismatch",
            None,
        ),
        (Step.FIND_AGENT, AGENT, "agent_registration_already_present", None),
        (
            Step.INITIAL_SCOPE,
            (SCOPE_UIDS[0], "replaced-namespace-uid"),
            SCOPE_ERROR,
            None,
        ),
        (
            Step.FIND_SECRET,
            SECRET_UID,
            "agent_registration_credential_already_present",
            None,
        ),
        (
            Step.PENDING,
            KUBERNETES.KubernetesError("pending-write-unconfirmed"),
            "pending-write-unconfirmed",
            None,
        ),
        (Step.CREATE_AGENT, PROVIDER.GitLabAPIError("timeout"), "timeout", None),
        (
            Step.PERSIST_AGENT,
            OSError("native write failed"),
            PERSIST_ERROR,
            AGENT_ID_FIELD,
        ),
        (Step.READ_AGENT, None, READBACK_ERROR, None),
        (
            Step.INITIAL_TOKENS,
            (PROVIDER.AgentToken(TOKEN_ID + 1, "existing-token", AGENT.id),),
            READBACK_ERROR,
            None,
        ),
        (Step.CREATE_TOKEN, PROVIDER.GitLabAPIError("timeout"), "timeout", None),
        (
            Step.PERSIST_TOKEN,
            OSError("native write failed"),
            PERSIST_ERROR,
            TOKEN_ID_FIELD,
        ),
        (Step.READ_TOKEN, None, CREDENTIAL_ERROR, None),
        (Step.FINAL_TOKENS, (), CREDENTIAL_ERROR, None),
        (
            Step.FINAL_SCOPE,
            (SCOPE_UIDS[0], "replaced-namespace-uid"),
            SCOPE_ERROR,
            None,
        ),
        (
            Step.CREATE_SECRET,
            KUBERNETES.KubernetesError("agent_secret_write_unconfirmed"),
            "agent_secret_write_unconfirmed",
            None,
        ),
        (
            Step.PERSIST_SECRET,
            OSError("native write failed"),
            PERSIST_ERROR,
            SECRET_UID_FIELD,
        ),
    ),
    ids=tuple(step.name.lower() for step in Step),
)
def test_agent_registration_halts_at_one_unverified_boundary(
    registration: SimpleNamespace,
    step: Step,
    reply: object,
    error: str,
    known_field: str | None,
) -> None:
    """Stop without retry, adoption, or writes after one changed native response."""
    registration.step = step
    registration.result = reply

    errors = (KUBERNETES.KubernetesError, PROVIDER.GitLabAPIError)
    with pytest.raises(errors) as rejected:
        _register(registration)

    assert str(rejected.value) == error
    assert tuple(item[0] for item in registration.journal.mock_calls) == CALLS[:step]
    if step < Step.CREATE_SECRET:
        registration.secret.create.assert_not_called()
        assert registration.credential.take_secret() == SECRET_VALUE
    if known_field is not None:
        assert isinstance(rejected.value, REGISTER.RegistrationPersistenceError)
        expected = {
            AGENT_ID_FIELD: AGENT.id,
            TOKEN_ID_FIELD: TOKEN_ID,
            SECRET_UID_FIELD: SECRET_UID,
        }
        assert rejected.value.unconfirmed[known_field] == expected[known_field]
        assert SECRET_VALUE not in json.dumps(rejected.value.unconfirmed)
        assert registration.snapshots[-1].document[known_field] is None


def test_agent_registration_passes_owned_journal_to_compensation(
    registration: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed token journal write passes its exact proposed update for reconciliation."""
    registration.step = Step.PERSIST_TOKEN
    registration.result = KUBERNETES.KubernetesError("fixture_write_failed")
    compensate = Mock()
    monkeypatch.setattr(REGISTER, "compensate_registration", compensate)
    deadline = REGISTER.RequestDeadline(30, monotonic=lambda: 0)
    with pytest.raises(REGISTER.RegistrationPersistenceError) as failure:
        REGISTER.register_fresh_agent(
            registration.gitlab,
            registration.kubernetes,
            CHOICE,
            registration.pending,
            deadline=deadline,
        )
    assert failure.value.unconfirmed["registration_credential_id"] == TOKEN_ID
    compensate.assert_called_once_with(
        registration.gitlab,
        registration.kubernetes,
        CHOICE,
        registration.store,
        journal_uid=RECORD_UID,
        unconfirmed=failure.value.unconfirmed,
        deadline=deadline,
    )
    registration.secret.create.assert_not_called()
