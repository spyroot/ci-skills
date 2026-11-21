"""Check exact tracked cleanup and refusal to delete a changed Agent identity."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Final
from unittest.mock import Mock, call, create_autospec

import pytest
import test_gitlab_agent_existing as ORIGINAL
from conftest import import_script_module
from test_gitlab_agent_connection import (
    installed_ownership as installed_ownership,  # noqa: PLC0414 - shared pytest fixture
)
from test_gitlab_agent_existing import (
    existing as existing,  # noqa: PLC0414 - shared pytest fixture
)
from test_gitlab_agent_recovery import (
    recovery_baseline as recovery_baseline,  # noqa: PLC0414 - shared pytest fixture
)

DELETE = import_script_module("lib.python.core.gitlab.cluster_agent.delete")
ABSENCE = import_script_module("lib.python.core.gitlab.cluster_agent.absence")
RECOVERY = ORIGINAL.RECOVERY
RECOVERY_TEST = ORIGINAL.RECOVERY_TEST
PROVIDER = ORIGINAL.PROVIDER
KUBERNETES = ORIGINAL.KUBERNETES
HELM = ORIGINAL.HELM
INSTALL = ORIGINAL.INSTALL_TEST

NAMESPACE: Final = ORIGINAL.NAMESPACE
CLUSTER_UID: Final = RECOVERY_TEST.CLUSTER_UID
NAMESPACE_UID: Final = RECOVERY_TEST.NAMESPACE_UID
ORIGIN: Final = RECOVERY_TEST.ORIGIN
AGENT_ID: Final = RECOVERY_TEST.AGENT_ID
TOKEN_ID: Final = ORIGINAL.TOKEN_ID
SELECTED: Final = replace(
    ORIGINAL.SELECTION,
    release=RECOVERY_TEST.SELECTION.release_name(
        ORIGINAL.SELECTION.scope, ORIGINAL.SELECTION.agent_name
    ),
)
RELEASE: Final = replace(INSTALL.DEPLOYED, name=SELECTED.release)
STORAGE: Final = replace(
    INSTALL.STORAGE_IDENTITY, name=f"sh.helm.release.v1.{SELECTED.release}.v1"
)
AGENT: Final = ORIGINAL.CONNECT_TEST.AGENT
ABSENT: Final = "ABSENT"
PRESENT: Final = "PRESENT"
DELETING: Final = "DELETING"
CHANGED_UID: Final = "different-native-uid"
NEW_VERSION: Final = "142"
TIMEOUT: Final = ORIGINAL.TIMEOUT
STATE: Final = ORIGINAL.STATE
RESOURCES: Final = ORIGINAL.RESOURCES
UID: Final = ORIGINAL.RESOURCE_UID
NAMESPACE_ROW: Final = ("Namespace", NAMESPACE, "")
CONFIGMAP_ROW: Final = ("ConfigMap", RECOVERY_TEST.RECORD_NAME, NAMESPACE)
POD_ROW: Final = ("Pod", ORIGINAL.CONNECT_TEST.POD_NAME, NAMESPACE)
STORAGE_ROW: Final = ("Secret", STORAGE.name, NAMESPACE)
ABSENT_SCOPE: Final = ABSENCE.LogicalAgentAbsence(
    SELECTED.project, SELECTED.release, CLUSTER_UID
)

Identity = tuple[str, str, str]


def _identity(row: dict[str, object]) -> Identity:
    """Extract the original kind, name and namespace as one exact key."""
    return str(row["kind"]), str(row["name"]), str(row["namespace"])


def _native(row: dict[str, object]) -> dict[str, object]:
    """Encode the native metadata required for exact return-to-owner checks."""
    kind, name, namespace = _identity(row)
    return {
        "apiVersion": row["api_version"],
        "kind": kind,
        "metadata": {
            "name": name,
            **({"namespace": namespace} if namespace else {}),
            "uid": row[UID],
            "labels": dict(RECOVERY_TEST.LABELS),
            "annotations": {
                ORIGINAL.CHART_TEST.RELEASE_NAME: SELECTED.release,
                ORIGINAL.CHART_TEST.RELEASE_NAMESPACE: NAMESPACE,
            },
        },
    }


@pytest.fixture
def tracked(
    existing: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    """Start with every original identity present and simulate only exact writes."""
    record = deepcopy(existing.record)
    document = record.document
    document["release"] = RELEASE.name
    document["release_storage_name"] = STORAGE.name
    for row in document[RESOURCES]:
        if row[UID] == STORAGE.uid:
            row["name"] = STORAGE.name
    rows = {_identity(row): row for row in document[RESOURCES]}
    case = SimpleNamespace(
        selection=SELECTED,
        gitlab=existing.gitlab,
        record=record,
        journal=deepcopy(record),
        rows=rows,
        objects={identity: _native(row) for identity, row in rows.items()},
        agent=AGENT,
        token=existing.token,
        tokens=(existing.token,),
        release=RELEASE,
        storage=STORAGE,
    )

    def delete_object(
        kind: str, name: str, namespace: str, *, expected_uid: str
    ) -> None:
        """Model a guarded native deletion, including namespace finalization."""
        identity = (kind, name, namespace)
        assert case.objects[identity]["metadata"][UID] == expected_uid
        del case.objects[identity]
        if identity == CONFIGMAP_ROW:
            case.journal = None
        if identity == STORAGE_ROW:
            case.release = case.storage = None
        if identity == NAMESPACE_ROW:
            assert CONFIGMAP_ROW in case.objects
            del case.objects[CONFIGMAP_ROW]
            case.journal = None

    def delete_token(agent_id: int, expected: object) -> bool:
        """Retire only the original token and preserve independent read paths."""
        assert (agent_id, expected) == (AGENT_ID, case.token)
        case.token = None
        case.tokens = ()
        return True

    def delete_agent(expected: object) -> bool:
        """Retire the original registration."""
        assert expected == case.agent
        case.agent = None
        return True

    def transition(_prior: object, document: dict[str, object]) -> object:
        """Record the native journals returned by a conditional replacement."""
        case.journal = RECOVERY.RecoveryRecord(
            record.uid, NEW_VERSION, deepcopy(document)
        )
        return deepcopy(case.journal)

    case.kube = create_autospec(KUBERNETES.KubernetesService, instance=True)
    case.kube.read.side_effect = lambda kind, name, namespace: deepcopy(
        case.objects.get((kind, name, namespace))
    )
    case.kube.delete.side_effect = delete_object
    case.provider = create_autospec(PROVIDER.AgentProvider, instance=True)
    case.provider.project.return_value = SELECTED.project
    case.provider.read_agent.side_effect = lambda *_: case.agent
    case.provider.find_agent.side_effect = lambda *_: case.agent
    case.provider.read_token.side_effect = lambda *_: case.token
    case.provider.list_tokens.side_effect = lambda *_: case.tokens
    case.provider.delete_agent.side_effect = delete_agent
    case.provider.delete_token.side_effect = delete_token
    case.helm = create_autospec(HELM.HelmClient, instance=True)
    case.helm.status.side_effect = lambda *_args, **_kwargs: case.release
    case.store = create_autospec(RECOVERY.AgentRecoveryStore, instance=True)
    case.store.name = RECOVERY_TEST.RECORD_NAME
    case.store.read.side_effect = lambda: deepcopy(case.journal)
    case.store.replace.side_effect = transition
    case.scope = Mock(return_value=(CLUSTER_UID, NAMESPACE_UID))
    case.helm_storage = Mock(side_effect=lambda *_args, **_kwargs: case.storage)
    case.namespace_guard = Mock()
    case.logical = Mock(return_value=ABSENT_SCOPE)
    for name, mock in (
        ("AgentProvider", case.provider),
        ("HelmClient", case.helm),
        ("AgentRecoveryStore", case.store),
    ):
        monkeypatch.setattr(DELETE, name, Mock(return_value=mock))
    for name, mock in (
        ("read_agent_cluster_scope", case.scope),
        ("read_helm_storage", case.helm_storage),
        ("verify_agent_namespace_cleanup", case.namespace_guard),
        ("verify_agent_logical_absence", case.logical),
    ):
        monkeypatch.setattr(DELETE, name, mock)
    trace = Mock()
    for name, mock in (
        ("gitlab", case.provider),
        ("kube", case.kube),
        ("journal", case.store),
        ("guard", case.namespace_guard),
        ("absence", case.logical),
    ):
        trace.attach_mock(mock, name)
    case.trace = trace
    return case


def _delete(case: SimpleNamespace) -> DELETE.AgentCleanup:
    """Delete only the exact native owner record supplied by the caller."""
    return DELETE.delete_tracked_agent(
        case.gitlab, case.kube, case.selection, case.record, timeout=TIMEOUT
    )


@pytest.mark.parametrize("owns_namespace", (False, True), ids=("borrowed", "owned"))
def test_tracked_delete_removes_only_original_native_ids(
    tracked: SimpleNamespace, owns_namespace: bool
) -> None:
    """A fresh delete proves all old identities absent and protects borrowed state."""
    case = tracked
    if owns_namespace:
        case.selection = replace(
            SELECTED, scope=replace(SELECTED.scope, owns_namespace=True)
        )
        case.record.document["created"]["namespace"] = True
        case.rows[NAMESPACE_ROW]["created"] = True
        case.journal = deepcopy(case.record)
    before = deepcopy(case.record)
    original_token = case.token

    result = _delete(case)

    assert result.ownership == before
    assert result.absence == ABSENT_SCOPE
    assert {_identity(row): (row[UID], row[STATE]) for row in result.resources} == {
        key: (row[UID], ABSENT if row["created"] else PRESENT)
        for key, row in case.rows.items()
    }
    assert case.record == before
    case.store.replace.assert_called_once()
    replaced, updated = case.store.replace.call_args.args
    assert replaced == before
    assert updated == {**before.document, STATE: DELETING}
    case.provider.delete_token.assert_called_once_with(AGENT_ID, original_token)
    case.provider.delete_agent.assert_called_once_with(AGENT)

    deleted = case.kube.delete.call_args_list
    expected = {key: row[UID] for key, row in case.rows.items() if row["created"]}
    if owns_namespace:
        del expected[CONFIGMAP_ROW]
    assert len(deleted) == len(expected)
    assert {item.args: item.kwargs.get("expected_uid") for item in deleted} == expected
    kinds = [item.args[0] for item in deleted]
    assert kinds.index("Deployment") < kinds.index("Pod")

    events = [item[0] for item in case.trace.mock_calls]
    assert events.index("journal.replace") < events.index("gitlab.delete_token")
    assert events.index("gitlab.delete_token") < events.index("gitlab.delete_agent")
    assert events.index("gitlab.delete_agent") < events.index("kube.delete")
    assert events[-1] == "absence"
    assert case.provider.read_agent.call_args_list == [call(AGENT_ID)] * 2
    assert case.provider.read_token.call_args_list == [call(AGENT_ID, TOKEN_ID)] * 2
    assert Counter(item.args for item in case.kube.read.call_args_list) == {
        key: 2 for key in case.rows
    }
    if owns_namespace:
        case.namespace_guard.assert_called_once()
        assert case.namespace_guard.call_args.args == (
            case.kube,
            NAMESPACE,
            NAMESPACE_UID,
            SELECTED.scope.run_id,
            frozenset(row[UID] for row in case.rows.values()),
        )
        assert deleted[-1].args == NAMESPACE_ROW
        assert case.objects == {}
    else:
        case.namespace_guard.assert_not_called()
        assert case.objects == {NAMESPACE_ROW: _native(case.rows[NAMESPACE_ROW])}


def test_tracked_delete_resume_skips_old_objects_already_absent(
    tracked: SimpleNamespace,
) -> None:
    """An exact DELETING record resumes without issuing repeat native deletes."""
    case = tracked
    case.record.document[STATE] = DELETING
    case.journal = deepcopy(case.record)
    case.token = None
    case.tokens = ()
    del case.objects[POD_ROW]

    result = _delete(case)

    case.store.replace.assert_not_called()
    case.provider.delete_token.assert_not_called()
    case.provider.delete_agent.assert_called_once_with(AGENT)
    assert all(item.args != POD_ROW for item in case.kube.delete.call_args_list)
    assert {_identity(row): row[STATE] for row in result.resources}[POD_ROW] == ABSENT
    assert result.absence == ABSENT_SCOPE


SCOPE_MISMATCH: Final = "agent_delete_scope_mismatch"
JOURNAL_CHANGED: Final = "agent_delete_journal_changed"
PROJECT_MISMATCH: Final = "agent_delete_project_mismatch"
REGISTRATION_MISMATCH: Final = "agent_delete_registration_mismatch"
TOKEN_MISMATCH: Final = "agent_delete_token_mismatch"
RELEASE_MISMATCH: Final = "agent_delete_release_mismatch"
RESOURCE_MISMATCH: Final = "agent_delete_resource_mismatch"


def _replace_pod(case: SimpleNamespace) -> None:
    """Present a replacement Kubernetes object at the original native address."""
    case.objects[POD_ROW]["metadata"][UID] = CHANGED_UID


def _named_replacement(case: SimpleNamespace) -> None:
    """Present another Agent for the exact logical name while keeping the old ID."""
    case.provider.find_agent.side_effect = None
    case.provider.find_agent.return_value = replace(AGENT, id=AGENT_ID + 1)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    (
        (
            lambda case: setattr(
                case.scope, "return_value", (CHANGED_UID, NAMESPACE_UID)
            ),
            SCOPE_MISMATCH,
        ),
        (
            lambda case: setattr(
                case, "journal", replace(case.journal, resource_version=NEW_VERSION)
            ),
            JOURNAL_CHANGED,
        ),
        (
            lambda case: setattr(
                case.provider.project,
                "return_value",
                replace(SELECTED.project, id=SELECTED.project.id + 1),
            ),
            PROJECT_MISMATCH,
        ),
        (_named_replacement, REGISTRATION_MISMATCH),
        (
            lambda case: setattr(
                case, "tokens", (case.token, replace(case.token, id=TOKEN_ID + 1))
            ),
            TOKEN_MISMATCH,
        ),
        (
            lambda case: setattr(
                case, "release", replace(RELEASE, revision=ORIGINAL.SECOND_REVISION)
            ),
            RELEASE_MISMATCH,
        ),
        (
            lambda case: setattr(case, "storage", replace(STORAGE, uid=CHANGED_UID)),
            RELEASE_MISMATCH,
        ),
        (_replace_pod, RESOURCE_MISMATCH),
        (lambda case: case.objects.pop(NAMESPACE_ROW), RESOURCE_MISMATCH),
    ),
    ids=(
        "cluster-replaced",
        "journal-advanced",
        "provider-project-replaced",
        "same-name-agent-replaced",
        "unexpected-token-id",
        "helm-revision-replaced",
        "helm-storage-replaced",
        "pod-replaced",
        "borrowed-namespace-missing",
    ),
)
def test_tracked_delete_refuses_one_changed_observation_before_writing(
    tracked: SimpleNamespace,
    mutate: Callable[[SimpleNamespace], object],
    reason: str,
) -> None:
    """One inconsistent preflight observation must prevent every mutation."""
    case = tracked
    mutate(case)

    with pytest.raises(KUBERNETES.KubernetesError, match=f"^{reason}$"):
        _delete(case)

    case.store.replace.assert_not_called()
    case.provider.delete_agent.assert_not_called()
    case.provider.delete_token.assert_not_called()
    case.kube.delete.assert_not_called()
    case.logical.assert_not_called()


@pytest.mark.parametrize(
    ("changed", "reason"),
    (
        (None, None),
        ("public_entry_point", "agent_delete_record_mismatch"),
        ("journal_replaced", "agent_delete_journal_changed"),
        ("missing_chart_uid", "agent_delete_record_mismatch"),
    ),
)
def test_pending_installation_compensation_preserves_strict_public_delete(
    tracked: SimpleNamespace, changed: str | None, reason: str | None
) -> None:
    """One fixture varies a single post-install recovery boundary per rejection."""
    case = tracked
    case.record.document[STATE] = "PENDING"
    case.record.document[RESOURCES].remove(case.rows.pop(POD_ROW))
    del case.objects[POD_ROW]
    case.journal = deepcopy(case.record)
    operation = DELETE.compensate_installed_agent
    if changed == "public_entry_point":
        operation = DELETE.delete_tracked_agent
    elif changed == "journal_replaced":
        case.journal = replace(case.journal, resource_version=NEW_VERSION)
    elif changed == "missing_chart_uid":
        rows = case.record.document[RESOURCES]
        rows.remove(next(row for row in rows if row["kind"] == "Deployment"))

    args = (case.gitlab, case.kube, case.selection, case.record)
    if reason is None:
        result = operation(*args, timeout=TIMEOUT)
        assert result.absence == ABSENT_SCOPE
        assert set(case.objects) == {NAMESPACE_ROW}
        assert case.journal is None
        assert case.agent is None
        assert case.token is None
        case.store.replace.assert_called_once()
        case.namespace_guard.assert_not_called()
    else:
        with pytest.raises(KUBERNETES.KubernetesError, match=reason):
            operation(*args, timeout=TIMEOUT)
        case.store.replace.assert_not_called()
        case.kube.delete.assert_not_called()
        case.provider.delete_agent.assert_not_called()
        case.provider.delete_token.assert_not_called()


@pytest.mark.parametrize("changed", (None, "journal_version", "candidate_revision"))
def test_tracked_delete_confirmation_gates_all_writes(
    tracked: SimpleNamespace, changed: str | None
) -> None:
    """Reobserve the same fixture before deletion, varying one digest input."""
    case = tracked
    case.gitlab.session.skill = {
        "revision": {"value": "a" * 40},
        "functional_digest": "c" * 64,
    }
    args = (case.gitlab, case.kube, case.selection, case.record)
    digest = DELETE.plan_tracked_delete(*args, timeout=TIMEOUT)
    case.store.replace.assert_not_called()
    case.kube.delete.assert_not_called()
    case.provider.delete_agent.assert_not_called()
    case.provider.delete_token.assert_not_called()
    if changed == "journal_version":
        case.record = replace(case.record, resource_version=NEW_VERSION)
        case.journal = deepcopy(case.record)
    elif changed == "candidate_revision":
        case.gitlab.session.skill["revision"]["value"] = "b" * 40

    current = (case.gitlab, case.kube, case.selection, case.record)
    if changed is None:
        result = DELETE.delete_tracked_agent(
            *current, timeout=TIMEOUT, confirmation=digest
        )
        assert result.absence == ABSENT_SCOPE
        case.store.replace.assert_called_once()
    else:
        with pytest.raises(KUBERNETES.KubernetesError, match="confirm_plan_mismatch"):
            DELETE.delete_tracked_agent(*current, timeout=TIMEOUT, confirmation=digest)
        case.store.replace.assert_not_called()
        case.kube.delete.assert_not_called()
        case.provider.delete_agent.assert_not_called()
        case.provider.delete_token.assert_not_called()
