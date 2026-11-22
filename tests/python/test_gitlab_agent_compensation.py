"""Exercise Agent registration compensation using verified native identities."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
import test_gitlab_agent_recovery as RECOVERY
from conftest import import_script_module
from test_gitlab_agent_recovery import (
    recovery_baseline as recovery_baseline,  # noqa: PLC0414
)

COMP = import_script_module("lib.python.core.gitlab.cluster_agent.compensation")
SELECTION = RECOVERY.SELECTION_CASE
AGENT = COMP.AgentRegistration(RECOVERY.AGENT_ID, RECOVERY.AGENT, RECOVERY.PROJECT_ID)
TOKEN = COMP.AgentToken(89, "fixture-credential", AGENT.id)
SECRET_UID = "fixture-credential-uid"


@pytest.mark.parametrize(
    ("changed", "reason"),
    (
        (None, None),
        ("owned", None),
        ("journal", "agent_compensation_journal_changed"),
        ("unconfirmed_write", "agent_compensation_journal_changed"),
        ("unrecorded_token", "agent_compensation_untracked_identity"),
        ("secret_uid", "agent_compensation_credential_changed"),
        ("unknown_agent", "agent_compensation_untracked_identity"),
        ("helm_owned", "agent_compensation_journal_changed"),
    ),
)
def test_registration_compensates_only_tracked_objects(
    recovery_baseline: str,
    monkeypatch: pytest.MonkeyPatch,
    changed: str | None,
    reason: str | None,
) -> None:
    """Vary only one observation from the shared immutable journal fixture."""
    document = json.loads(recovery_baseline)[RECOVERY.UPDATED_ROW]
    document.update(
        registration_credential_id=TOKEN.id,
        registration_credential_name=TOKEN.name,
        credential_resource_uid=SECRET_UID,
    )
    document["created"].update(registration_credential=True, credential_resource=True)
    owned = changed == "owned"
    selection = replace(SELECTION, scope=replace(SELECTION.scope, owns_namespace=owned))
    document["created"]["namespace"] = owned
    journal_uid = RECOVERY.RECORD_UID
    secret_uid = SECRET_UID
    tokens = (TOKEN,)
    unconfirmed = None
    if changed == "journal":
        journal_uid = RECOVERY.OTHER_UID
    elif changed == "unconfirmed_write":
        unconfirmed = {**document, "credential_resource_uid": "not-durable"}
    elif changed == "unrecorded_token":
        tokens = (TOKEN, COMP.AgentToken(90, "foreign", AGENT.id))
    elif changed == "secret_uid":
        secret_uid = "replacement-secret"
    elif changed == "unknown_agent":
        document["agent_id"] = None
        document["created"]["agent"] = None
    elif changed == "helm_owned":
        document["created"]["release"] = True

    record = RECOVERY.RECOVERY.RecoveryRecord(journal_uid, "4", document)
    store = Mock()
    store.name = RECOVERY.RECORD_NAME
    store.read.return_value = record
    provider = Mock()
    provider.project.return_value = SELECTION.project
    provider.find_agent.side_effect = (AGENT, None)
    provider.read_agent.return_value = AGENT
    provider.read_token.return_value = TOKEN
    provider.list_tokens.return_value = tokens
    kubernetes = Mock()
    gitlab = SimpleNamespace(target_id=RECOVERY.PROJECT_ID)
    secret = Mock()
    secret.name = document["credential_resource_name"]
    secret.read.side_effect = (secret_uid, None)
    namespace_guard = Mock()
    monkeypatch.setattr(COMP, "AgentProvider", Mock(return_value=provider))
    monkeypatch.setattr(COMP, "AgentSecretStore", Mock(return_value=secret))
    monkeypatch.setattr(COMP, "verify_agent_namespace_cleanup", namespace_guard)
    monkeypatch.setattr(
        COMP,
        "read_agent_cluster_scope",
        Mock(return_value=(RECOVERY.CLUSTER_UID, RECOVERY.NAMESPACE_UID)),
    )
    options = {
        "journal_uid": RECOVERY.RECORD_UID,
        "unconfirmed": unconfirmed,
        "deadline": COMP.RequestDeadline(30, monotonic=lambda: 0),
    }
    if reason is None:
        COMP.compensate_registration(gitlab, kubernetes, selection, store, **options)
        provider.delete_token.assert_called_once_with(AGENT.id, TOKEN)
        provider.delete_agent.assert_called_once_with(AGENT)
        namespace = selection.scope.namespace
        if owned:
            namespace_guard.assert_called_once_with(
                kubernetes,
                namespace,
                RECOVERY.NAMESPACE_UID,
                selection.scope.run_id,
                frozenset((journal_uid,)),
                timeout=30,
            )
            final = call(
                "Namespace", namespace, "", expected_uid=RECOVERY.NAMESPACE_UID
            )
        else:
            namespace_guard.assert_not_called()
            final = call("ConfigMap", store.name, namespace, expected_uid=journal_uid)
        assert kubernetes.delete.call_args_list == [
            call("Secret", secret.name, namespace, expected_uid=SECRET_UID),
            final,
        ]
    else:
        with pytest.raises(COMP.KubernetesError, match=reason):
            COMP.compensate_registration(
                gitlab, kubernetes, selection, store, **options
            )
        provider.delete_token.assert_not_called()
        provider.delete_agent.assert_not_called()
        kubernetes.delete.assert_not_called()
        namespace_guard.assert_not_called()
