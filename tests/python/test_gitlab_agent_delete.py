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
REGISTRATION = ORIGINAL.REGISTRATION
KUBE_AGENT = ORIGINAL.CONNECT_TEST.POD

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
REPLICA_SET_KIND: Final = KUBE_AGENT.REPLICA_SET_KIND
LEASE_KIND: Final = KUBE_AGENT.LEASE_KIND
LEASE_ROW: Final = (LEASE_KIND, KUBE_AGENT.agent_lease_name(AGENT_ID), NAMESPACE)
REPLICA_SET_NAME: Final = "agent-workload-replica-{index}"
REPLICA_SET_UID: Final = "replica-set-uid-{index}"
LEASE_UID: Final = "lease-uid-{index}"
MAX_REPLICA_SETS: Final = DELETE._MAX_REPLICA_SETS
PENDING: Final = "PENDING"

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


def _own_namespace(case: SimpleNamespace) -> None:
    """Select a run-owned namespace and record the Namespace row as created.

    :param case: Tracked delete harness to change.
    """
    case.selection = replace(
        SELECTED, scope=replace(SELECTED.scope, owns_namespace=True)
    )
    case.record.document["created"]["namespace"] = True
    case.rows[NAMESPACE_ROW]["created"] = True
    case.journal = deepcopy(case.record)


@pytest.mark.parametrize("owns_namespace", (False, True), ids=("borrowed", "owned"))
def test_tracked_delete_removes_only_original_native_ids(
    tracked: SimpleNamespace, owns_namespace: bool
) -> None:
    """A fresh delete proves all old identities absent and protects borrowed state."""
    case = tracked
    if owns_namespace:
        _own_namespace(case)
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
    reads = {key: 2 for key in case.rows}
    if owns_namespace:
        # The record has no Lease row, so delete reads the Agent's Lease once.
        reads[LEASE_ROW] = 1
    assert Counter(item.args for item in case.kube.read.call_args_list) == reads
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
RECORD_MISMATCH: Final = "agent_delete_record_mismatch"


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


def _replica_set(index: int) -> dict[str, object]:
    """Build the ownership row connect writes for one controlling ReplicaSet.

    :param index: Distinguishes the name and UID of each ReplicaSet.
    :returns: Created, present ReplicaSet row in the Agent namespace.
    """
    return REGISTRATION.observed_resource(
        REPLICA_SET_KIND,
        REPLICA_SET_NAME.format(index=index),
        NAMESPACE,
        REPLICA_SET_UID.format(index=index),
        created=True,
        api_version=KUBE_AGENT.REPLICA_SET_API_VERSION,
    )


def _lease(index: int) -> dict[str, object]:
    """Build the ownership row connect writes for an agentk Lease.

    :param index: 0 names this Agent's Lease; another value names the Lease of
        Agent ID ``AGENT_ID + index``, so each row has its own name and UID.
    :returns: Created, present Lease row in the Agent namespace.
    """
    return REGISTRATION.observed_resource(
        LEASE_KIND,
        KUBE_AGENT.agent_lease_name(AGENT_ID + index),
        NAMESPACE,
        LEASE_UID.format(index=index),
        created=True,
        api_version=KUBE_AGENT.LEASE_API_VERSION,
    )


def _runtime_rows(replica_sets: int, leases: int) -> list[dict[str, object]]:
    """Build fresh ReplicaSet and Lease rows.

    :param replica_sets: Number of ReplicaSet rows.
    :param leases: Number of Lease rows.
    :returns: New row objects; no two cases share one.
    """
    return [
        *(_replica_set(index) for index in range(replica_sets)),
        *(_lease(index) for index in range(leases)),
    ]


def _record_rows(
    case: SimpleNamespace, rows: list[dict[str, object]], *, native: bool
) -> None:
    """Add rows to the ownership record and the journal read back for it.

    :param case: Tracked delete harness to change.
    :param rows: Ownership rows to append.
    :param native: Whether the cluster still holds each recorded object.
    """
    for row in rows:
        case.record.document[RESOURCES].append(row)
        case.rows[_identity(row)] = row
        if native:
            case.objects[_identity(row)] = _native(row)
    case.journal = deepcopy(case.record)


def _assert_nothing_read_or_written(case: SimpleNamespace) -> None:
    """Check that a rejected record caused no native read and no write.

    :param case: Tracked delete harness after the rejected call.
    """
    case.scope.assert_not_called()
    case.kube.read.assert_not_called()
    case.kube.delete.assert_not_called()
    case.store.replace.assert_not_called()
    case.provider.delete_agent.assert_not_called()
    case.provider.delete_token.assert_not_called()


@pytest.mark.parametrize(
    ("pending", "replica_sets", "leases"),
    (
        pytest.param(False, MAX_REPLICA_SETS + 1, 0, id="replica-sets-over-bound"),
        pytest.param(False, 0, 2, id="two-leases"),
        pytest.param(True, 1, 0, id="installation-only-replica-set"),
        pytest.param(True, 0, 1, id="installation-only-lease"),
    ),
)
def test_delete_refuses_record_with_runtime_rows_out_of_bounds(
    tracked: SimpleNamespace, pending: bool, replica_sets: int, leases: int
) -> None:
    """Refuse an ownership record whose ReplicaSet or Lease rows connect cannot write.

    ``confirm_fresh_agent_connection`` writes one row per distinct ReplicaSet of at
    most 10 connected Pods and one Lease row, in the same update that sets the state
    CONNECTED, so a PENDING install journal has neither. The passing neighbours are
    ``test_tracked_delete_removes_runtime_rows_at_bound`` and the PENDING
    compensation without runtime rows in
    ``test_pending_installation_compensation_preserves_strict_public_delete``.

    :param tracked: Tracked delete harness.
    :param pending: Use the PENDING journal and ``compensate_installed_agent``.
    :param replica_sets: ReplicaSet rows added to the record.
    :param leases: Lease rows added to the record.
    """
    case = tracked
    operation = DELETE.delete_tracked_agent
    if pending:
        case.record.document[STATE] = PENDING
        case.record.document[RESOURCES].remove(case.rows.pop(POD_ROW))
        del case.objects[POD_ROW]
        operation = DELETE.compensate_installed_agent
    _record_rows(case, _runtime_rows(replica_sets, leases), native=True)

    with pytest.raises(KUBERNETES.KubernetesError, match=f"^{RECORD_MISMATCH}$"):
        operation(case.gitlab, case.kube, case.selection, case.record, timeout=TIMEOUT)

    _assert_nothing_read_or_written(case)


def test_tracked_delete_removes_runtime_rows_at_bound(
    tracked: SimpleNamespace,
) -> None:
    """Delete the maximum recorded ReplicaSets and the Lease by UID, in kind order.

    The order is ``_DELETE_ORDER`` in delete.py: Deployment, then ReplicaSet, then
    Pod, then Lease.

    :param tracked: Tracked delete harness.
    """
    case = tracked
    _record_rows(case, _runtime_rows(MAX_REPLICA_SETS, 1), native=True)
    expected = {key: row[UID] for key, row in case.rows.items() if row["created"]}

    result = _delete(case)

    deleted = case.kube.delete.call_args_list
    assert len(deleted) == len(expected)
    assert {item.args: item.kwargs["expected_uid"] for item in deleted} == expected
    kinds = [item.args[0] for item in deleted]
    assert (
        kinds.index("Deployment")
        < kinds.index(REPLICA_SET_KIND)
        < kinds.index("Pod")
        < kinds.index(LEASE_KIND)
    )
    assert {_identity(row): row[STATE] for row in result.resources} == {
        key: ABSENT if row["created"] else PRESENT for key, row in case.rows.items()
    }
    assert result.present == frozenset(row[UID] for row in case.rows.values())


@pytest.mark.parametrize(
    "gone",
    (
        pytest.param((REPLICA_SET_KIND,), id="replica-set-gone"),
        pytest.param((LEASE_KIND,), id="lease-gone"),
        pytest.param((REPLICA_SET_KIND, LEASE_KIND), id="both-gone"),
    ),
)
def test_tracked_delete_accepts_recorded_runtime_rows_already_gone(
    tracked: SimpleNamespace, gone: tuple[str, ...]
) -> None:
    """A first delete does not require recorded ReplicaSet or Lease objects to exist.

    delete.py excludes ``_RUNTIME_KINDS`` from the rows that must be present before
    the first write; it deletes the ones still present and reports every one ABSENT.
    ``AgentCleanup.present`` names only the objects read present before the writes.

    :param tracked: Tracked delete harness.
    :param gone: Kinds whose recorded objects no longer exist at the first read.
    """
    case = tracked
    _record_rows(case, _runtime_rows(1, 1), native=True)
    missing = {key: row for key, row in case.rows.items() if key[0] in gone}
    for key in missing:
        del case.objects[key]

    result = _delete(case)

    deleted = {item.args for item in case.kube.delete.call_args_list}
    assert deleted.isdisjoint(missing)
    for key, row in case.rows.items():
        if key[0] in (REPLICA_SET_KIND, LEASE_KIND) and key not in missing:
            case.kube.delete.assert_any_call(*key, expected_uid=row[UID])
    assert {_identity(row): row[STATE] for row in result.resources} == {
        key: ABSENT if row["created"] else PRESENT for key, row in case.rows.items()
    }
    assert result.present == frozenset(
        row[UID] for key, row in case.rows.items() if key not in missing
    )
    assert (result.agent_present, result.token_present) == (True, True)


def test_tracked_delete_refuses_recorded_pod_already_gone(
    tracked: SimpleNamespace,
) -> None:
    """A first delete still requires every recorded install object, here the Pod.

    Control for the runtime-row exception: only ReplicaSet and Lease rows may be
    absent before the first write.

    :param tracked: Tracked delete harness.
    """
    case = tracked
    del case.objects[POD_ROW]

    with pytest.raises(KUBERNETES.KubernetesError, match=f"^{RESOURCE_MISMATCH}$"):
        _delete(case)

    case.store.replace.assert_not_called()
    case.kube.delete.assert_not_called()
    case.provider.delete_agent.assert_not_called()
    case.provider.delete_token.assert_not_called()


@pytest.mark.parametrize(
    ("recorded", "native", "lease_reads"),
    (
        pytest.param(False, True, 1, id="unrecorded-lease-present"),
        pytest.param(False, False, 1, id="unrecorded-lease-absent"),
        pytest.param(True, True, 2, id="recorded-lease"),
    ),
)
def test_owned_namespace_delete_removes_agent_lease_by_uid(
    tracked: SimpleNamespace, recorded: bool, native: bool, lease_reads: int
) -> None:
    """Delete ``agent-<agent_id>-lock`` by UID before the run-owned namespace inventory.

    A record written before connect recorded the Lease has no Lease row; delete reads
    the Lease named after the recorded Agent ID, deletes it by the UID it read and
    adds that UID to the UIDs the namespace inventory accepts. A record with a Lease
    row deletes it as a recorded row, with no extra read.

    :param tracked: Tracked delete harness.
    :param recorded: Whether the ownership record has the Lease row.
    :param native: Whether the Lease exists in the namespace.
    :param lease_reads: Native reads of the Lease during the delete.
    """
    case = tracked
    _own_namespace(case)
    lease = _lease(0)
    if recorded:
        _record_rows(case, [lease], native=native)
    elif native:
        case.objects[LEASE_ROW] = _native(lease)
    recorded_uids = frozenset(row[UID] for row in case.rows.values())

    _delete(case)

    lease_deletes = [
        item for item in case.kube.delete.call_args_list if item.args[0] == LEASE_KIND
    ]
    assert lease_deletes == (
        [call(*LEASE_ROW, expected_uid=lease[UID])] if native else []
    )
    permitted = case.namespace_guard.call_args.args[-1]
    assert permitted == recorded_uids | (
        {lease[UID]} if native and not recorded else frozenset()
    )
    reads = Counter(item.args for item in case.kube.read.call_args_list)
    assert reads[LEASE_ROW] == lease_reads
    events = case.trace.mock_calls
    names = [item[0] for item in events]
    if native:
        removal = call.kube.delete(*LEASE_ROW, expected_uid=lease[UID])
        assert events.index(removal) < names.index("guard")
    assert case.objects == {}


def test_borrowed_namespace_delete_leaves_unrecorded_lease_untouched(
    tracked: SimpleNamespace,
) -> None:
    """Never read or delete an unrecorded Lease in a namespace the run did not create.

    :param tracked: Tracked delete harness with a borrowed namespace.
    """
    case = tracked
    native = _native(_lease(0))
    case.objects[LEASE_ROW] = deepcopy(native)

    _delete(case)

    assert all(item.args != LEASE_ROW for item in case.kube.read.call_args_list)
    assert all(item.args[0] != LEASE_KIND for item in case.kube.delete.call_args_list)
    case.namespace_guard.assert_not_called()
    assert case.objects[LEASE_ROW] == native
