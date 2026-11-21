"""Offline tests for live Agent Pod proof and the guarded CONNECTED transition."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from types import SimpleNamespace
from typing import Final
from unittest.mock import Mock, call, create_autospec

import pytest
import test_gitlab_agent_connect as INSTALL_TEST
import test_gitlab_agent_installation as CHART_TEST
import test_gitlab_agent_recovery as RECOVERY_TEST
from conftest import import_script_module
from test_gitlab_agent_recovery import (
    recovery_baseline as recovery_baseline,  # noqa: PLC0414 - shared pytest fixture
)

CONNECTION = import_script_module("lib.python.core.gitlab.cluster_agent.connection")
POD = import_script_module("lib.python.core.kubernetes.gitlab_agent")
OCI = import_script_module("lib.python.core.oci")
CLI_API = import_script_module("lib.python.core.cli_api")
PROVIDER = INSTALL_TEST.PROVIDER
RECOVERY = RECOVERY_TEST.RECOVERY
KUBERNETES = RECOVERY_TEST.KUBERNETES

SELECTION: Final = INSTALL_TEST.SELECTED
NAMESPACE: Final = INSTALL_TEST.NAMESPACE
RECORD_UID: Final = RECOVERY_TEST.RECORD_UID
PREVIOUS_VERSION: Final = "140"
DEPLOYMENT_UID: Final = "deployment-uid"
NEW_VERSION: Final = "141"
POD_NAME: Final = "agent-pod-original"
POD_UID: Final = "owned-agent-pod"
OTHER_UID: Final = "replacement-agent-pod"
APP_VERSION: Final = RECOVERY_TEST.SELECTION.APP_VERSION
PLATFORM_DIGEST: Final = "sha256:" + "e" * 64
TIMEOUT: Final = 30.0
POLL_INTERVAL: Final = 5.0
INSTALL_SECONDS: Final = 7.0
PENDING: Final = "PENDING"
CONNECTED: Final = "CONNECTED"
AGENT: Final = PROVIDER.AgentRegistration(
    RECOVERY_TEST.AGENT_ID, SELECTION.agent_name, SELECTION.project.id
)
RECORD_MISMATCH: Final = "agent_connection_record_mismatch"
SCOPE_MISMATCH: Final = "agent_connection_scope_mismatch"
REGISTER_MISMATCH: Final = "agent_connection_registration_mismatch"
METADATA_MISMATCH: Final = "agent_connection_metadata_mismatch"
POD_MISMATCH: Final = "agent_connection_pod_identity_mismatch"
ABSENT: Final = "agent_connection_absent"
OBSERVATION: Final = POD.AgentPodObservation(POD_UID, PLATFORM_DIGEST)
NOW: Final = datetime(2026, 1, 1, 12, tzinfo=UTC)
NATIVE: Final = PROVIDER.AgentConnection(
    "42",
    (NOW - timedelta(seconds=2)).isoformat(),
    POD_NAME,
    NAMESPACE,
    APP_VERSION,
    "a" * 40,
)
REPLICA_SET_NAME: Final = "agent-workload-replica"
REPLICA_SET_UID: Final = "agent-replica-set-uid"
OTHER_REPLICA_SET_NAME: Final = "agent-workload-previous"
OTHER_REPLICA_SET_UID: Final = "agent-previous-replica-set-uid"
LEASE_NAME: Final = POD.agent_lease_name(AGENT.id)
LEASE_UID: Final = "agent-lease-uid"
SECOND_POD_NAME: Final = "agent-pod-second"
SECOND_POD_UID: Final = "second-agent-pod"
THIRD_POD_NAME: Final = "agent-pod-third"
THIRD_POD_UID: Final = "third-agent-pod"
OTHER_POD_NAME: Final = "agent-pod-foreign"
# connection._held_by accepts the Pod name alone or followed by "_<suffix>".
HOLDER_SEPARATOR: Final = "_"
HOLDER_SUFFIX: Final = "holder-suffix"
HELD: Final = (f"{POD_NAME}{HOLDER_SEPARATOR}{HOLDER_SUFFIX}", LEASE_UID)
POLL_READS: Final = int(TIMEOUT / POLL_INTERVAL)


class FixedDatetime(datetime):
    """Provide one wall-clock reading without changing ISO timestamp parsing."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        """Return the fixed clock using the caller's timezone convention."""
        return NOW.replace(tzinfo=None) if tz is None else NOW.astimezone(tz)


def _owned_resource(kind: str, name: str, uid: str) -> dict[str, object]:
    """Construct a native namespaced fixture identity with no credential data."""
    return {
        "api_version": "v1",
        "kind": kind,
        "name": name,
        "namespace": NAMESPACE,
        "uid": uid,
        "state": "PRESENT",
        "created": True,
    }


@pytest.fixture(scope="module")
def installed_ownership(recovery_baseline: str) -> str:
    """Record immutable release and chart observations before connection proof."""
    record = json.loads(recovery_baseline)[RECOVERY_TEST.UPDATED_ROW]
    chart_resources = [
        {
            **_owned_resource(kind, CHART_TEST.WORKLOAD, f"{kind.lower()}-uid"),
            "api_version": version,
            "namespace": scope,
        }
        for version, kind, scope, _ in CHART_TEST.IDENTITIES
    ]
    borrowed = {
        **_owned_resource("Namespace", NAMESPACE, INSTALL_TEST.NAMESPACE_UID),
        "namespace": "",
        "created": False,
    }
    record.update(
        registration_credential_id=INSTALL_TEST.TOKEN_ID,
        credential_resource_uid=INSTALL_TEST.SECRET_UID,
        release_revision=INSTALL_TEST.DEPLOYED.revision,
        release_storage_name=INSTALL_TEST.STORAGE,
        release_storage_uid=INSTALL_TEST.STORAGE_UID,
        image_digest=INSTALL_TEST.IMAGE_DIGEST,
        created={
            **record["created"],
            "registration_credential": True,
            "credential_resource": True,
            "release": True,
        },
        resources=[
            borrowed,
            _owned_resource("ConfigMap", RECOVERY_TEST.RECORD_NAME, RECORD_UID),
            _owned_resource(
                "Secret", record["credential_resource_name"], INSTALL_TEST.SECRET_UID
            ),
            _owned_resource("Secret", INSTALL_TEST.STORAGE, INSTALL_TEST.STORAGE_UID),
            *chart_resources,
        ],
    )
    return json.dumps(record)


@pytest.fixture
def connectivity(
    monkeypatch: pytest.MonkeyPatch, installed_ownership: str
) -> SimpleNamespace:
    """Bind read-only native queries and an independently acknowledged journal."""
    document = json.loads(installed_ownership)
    gitlab = SimpleNamespace(
        target_id=SELECTION.project.id,
        session=SimpleNamespace(origin=RECOVERY_TEST.ORIGIN),
    )
    kube = create_autospec(KUBERNETES.KubernetesService, instance=True)
    provider = create_autospec(PROVIDER.AgentProvider, instance=True)
    provider.project.return_value = SELECTION.project
    provider.read_agent.return_value = AGENT
    provider.connections.return_value = (NATIVE,)
    scope = Mock(return_value=INSTALL_TEST.SCOPE_UIDS)
    correlate = Mock(return_value=OBSERVATION)
    replica_set = Mock(return_value=(REPLICA_SET_NAME, REPLICA_SET_UID))
    lease = Mock(return_value=HELD)
    store = create_autospec(RECOVERY.AgentRecoveryStore, instance=True)
    store.replace.side_effect = lambda _old, new: RECOVERY.RecoveryRecord(
        RECORD_UID, NEW_VERSION, deepcopy(new)
    )
    journal = Mock()
    for name, mock in (
        ("scope", scope),
        ("provider", provider),
        ("correlate", correlate),
        ("replica_set", replica_set),
        ("lease", lease),
        ("store", store),
    ):
        journal.attach_mock(mock, name)

    elapsed = 0.0

    def sleep(seconds: float) -> None:
        """Advance elapsed time without delaying the test."""
        nonlocal elapsed
        elapsed += seconds

    delay = Mock(side_effect=sleep)
    monkeypatch.setattr(
        CONNECTION, "time", SimpleNamespace(monotonic=lambda: elapsed, sleep=delay)
    )
    monkeypatch.setattr(CONNECTION, "datetime", FixedDatetime)
    monkeypatch.setattr(CONNECTION, "AgentProvider", Mock(return_value=provider))
    monkeypatch.setattr(CONNECTION, "AgentRecoveryStore", Mock(return_value=store))
    monkeypatch.setattr(CONNECTION, "read_agent_cluster_scope", scope)
    monkeypatch.setattr(CONNECTION, "correlate_agent_pod", correlate)
    monkeypatch.setattr(CONNECTION, "read_agent_replica_set", replica_set)
    monkeypatch.setattr(CONNECTION, "read_agent_lease", lease)
    return SimpleNamespace(
        gitlab=gitlab,
        kube=kube,
        provider=provider,
        scope=scope,
        correlate=correlate,
        replica_set=replica_set,
        lease=lease,
        store=store,
        journal=journal,
        delay=delay,
        image=OCI.ImageManifestIdentity(
            INSTALL_TEST.IMAGE_DIGEST,
            frozenset((INSTALL_TEST.IMAGE_DIGEST, PLATFORM_DIGEST)),
        ),
        record=RECOVERY.RecoveryRecord(RECORD_UID, PREVIOUS_VERSION, document),
    )


def _args(case: SimpleNamespace) -> tuple[object, ...]:
    """Use the same exact scope selection for connect and read-only repeat."""
    return case.gitlab, case.kube, SELECTION, case.record, case.image


def _connected(record: RECOVERY.RecoveryRecord) -> RECOVERY.RecoveryRecord:
    """Copy the immutable baseline with one previously verified Pod identity."""
    document = deepcopy(record.document)
    document["state"] = CONNECTED
    document["resources"].append(_owned_resource("Pod", POD_NAME, POD_UID))
    return replace(record, document=document)


def _runtime_resource(
    kind: str, name: str, uid: str, api_version: str
) -> dict[str, object]:
    """Build the created ReplicaSet or Lease row that connect records.

    :param kind: ``ReplicaSet`` or ``Lease``.
    :param name: Native object name.
    :param uid: Native object UID.
    :param api_version: API version owned by ``kubernetes/gitlab_agent.py``.
    :returns: Ownership row in the Agent namespace, created and present.
    """
    return {**_owned_resource(kind, name, uid), "api_version": api_version}


def _replica_set_row(name: str, uid: str) -> dict[str, object]:
    """Build one recorded ReplicaSet row.

    :param name: ReplicaSet name.
    :param uid: ReplicaSet UID.
    :returns: Row with the ReplicaSet kind and API version.
    """
    return _runtime_resource(
        POD.REPLICA_SET_KIND, name, uid, POD.REPLICA_SET_API_VERSION
    )


LEASE_RESOURCE: Final = json.dumps(
    _runtime_resource(POD.LEASE_KIND, LEASE_NAME, LEASE_UID, POD.LEASE_API_VERSION)
)


def test_confirm_agent_connection_paces_reads_and_persists_exact_pod_uid(
    connectivity: SimpleNamespace,
) -> None:
    """Poll only after an authenticated empty result, then persist the native UIDs.

    The Pod row is followed by the ReplicaSet that controls the Pod and the agentk
    Lease held by that Pod, both read after the connection is proved.
    """
    case = connectivity
    case.provider.connections.side_effect = ((), (NATIVE,))
    before = deepcopy(case.record.document)

    confirmed = CONNECTION.confirm_fresh_agent_connection(*_args(case), timeout=TIMEOUT)

    assert confirmed.connections == (CONNECTION.ConnectedAgentPod(NATIVE, OBSERVATION),)
    assert case.record.document == before
    assert confirmed.record.uid == RECORD_UID
    assert confirmed.record.resource_version == NEW_VERSION
    assert confirmed.record.document["state"] == CONNECTED
    assert confirmed.record.document["resources"] == [
        *before["resources"],
        _owned_resource("Pod", POD_NAME, POD_UID),
        _replica_set_row(REPLICA_SET_NAME, REPLICA_SET_UID),
        json.loads(LEASE_RESOURCE),
    ]
    assert confirmed.record.document["created"]["namespace"] is False
    assert case.provider.connections.call_args_list == [
        call(SELECTION.project, AGENT),
        call(SELECTION.project, AGENT),
    ]
    case.delay.assert_called_once_with(POLL_INTERVAL)
    case.store.replace.assert_called_once_with(case.record, confirmed.record.document)
    assert [item[0] for item in case.journal.mock_calls] == [
        "scope",
        "provider.project",
        "provider.read_agent",
        "provider.connections",
        "provider.connections",
        "correlate",
        "replica_set",
        "lease",
        "store.replace",
    ]
    expected = case.correlate.call_args.args[2]
    assert expected.deployment_uid == DEPLOYMENT_UID
    assert expected.namespace == NAMESPACE
    assert expected.image.endswith(f"@{INSTALL_TEST.IMAGE_DIGEST}")
    assert expected.runtime_digests == case.image.runtime_digests
    case.replica_set.assert_called_once_with(case.kube, POD_NAME, expected)
    case.lease.assert_called_once_with(case.kube, NAMESPACE, AGENT.id)
    assert case.kube.mock_calls == []


@pytest.mark.parametrize(
    ("holder", "held"),
    (
        pytest.param(POD_NAME, True, id="pod-name"),
        pytest.param(HELD[0], True, id="pod-name-with-suffix"),
        pytest.param(
            f"{SECOND_POD_NAME}{HOLDER_SEPARATOR}{HOLDER_SUFFIX}",
            True,
            id="second-pod-with-suffix",
        ),
        pytest.param(OTHER_POD_NAME, False, id="other-pod"),
        pytest.param(
            f"{OTHER_POD_NAME}{HOLDER_SEPARATOR}{HOLDER_SUFFIX}",
            False,
            id="other-pod-with-suffix",
        ),
        pytest.param(f"{POD_NAME}-{HOLDER_SUFFIX}", False, id="longer-pod-name"),
        pytest.param(POD_NAME[:-1], False, id="prefix-of-pod-name"),
        pytest.param("", False, id="unheld"),
    ),
)
def test_lease_holder_matches_only_a_connected_pod(holder: str, held: bool) -> None:
    """Accept a holder that is a connected Pod name, alone or followed by ``_<suffix>``.

    ``longer-pod-name`` and ``prefix-of-pod-name`` are other Pods in the same
    namespace whose names share characters with a connected Pod; ``unheld`` is the
    empty holder ``read_agent_lease`` returns for a Lease without a holder.

    :param holder: Holder identity read from the Lease.
    :param held: Whether a connected Pod holds the Lease.
    """
    pods = {POD_NAME: POD_UID, SECOND_POD_NAME: SECOND_POD_UID}

    assert CONNECTION._held_by(holder, pods) is held


@pytest.mark.parametrize(
    ("replies", "reads"),
    (
        pytest.param((HELD,), 1, id="held-at-first-read"),
        pytest.param((None, HELD), 2, id="absent-then-held"),
        pytest.param((("", LEASE_UID), HELD), 2, id="unheld-then-held"),
        pytest.param(
            ((OTHER_POD_NAME, LEASE_UID), (POD_NAME, LEASE_UID)),
            2,
            id="other-pod-then-connected-pod",
        ),
    ),
)
def test_confirm_agent_connection_waits_for_lease_held_by_connected_pod(
    connectivity: SimpleNamespace,
    replies: tuple[object, ...],
    reads: int,
) -> None:
    """Persist the Lease only once a connected Pod holds it, pacing each re-read.

    Rows: the Lease is held at the first read; it does not exist yet; it has no
    holder; it is held by a Pod that is not connected and then by a connected Pod.

    :param connectivity: Connection harness with a shared fake clock.
    :param replies: ``read_agent_lease`` results in call order.
    :param reads: Lease reads before the connect persists.
    """
    case = connectivity
    case.lease.side_effect = replies

    confirmed = CONNECTION.confirm_fresh_agent_connection(*_args(case), timeout=TIMEOUT)

    assert confirmed.record.document["resources"][-1] == json.loads(LEASE_RESOURCE)
    assert case.lease.call_count == reads
    assert case.delay.call_args_list == [call(POLL_INTERVAL)] * (reads - 1)
    case.store.replace.assert_called_once()


def test_confirm_agent_connection_never_records_lease_held_by_another_pod(
    connectivity: SimpleNamespace,
) -> None:
    """Keep re-reading a Lease held by a Pod that is not connected until the deadline.

    The connect fails with the shared deadline error and writes no ownership.

    :param connectivity: Connection harness with a shared fake clock.
    """
    case = connectivity
    case.lease.return_value = (OTHER_POD_NAME, LEASE_UID)

    with pytest.raises(
        CLI_API.APIError, match=f"^{CLI_API.REQUEST_DEADLINE_EXCEEDED}$"
    ):
        CONNECTION.confirm_fresh_agent_connection(*_args(case), timeout=TIMEOUT)

    assert case.lease.call_count == POLL_READS
    assert case.delay.call_args_list == [call(POLL_INTERVAL)] * POLL_READS
    case.store.replace.assert_not_called()


def test_runtime_rows_record_each_replica_set_once_in_name_order(
    connectivity: SimpleNamespace,
) -> None:
    """Record one created row per distinct ReplicaSet, sorted by name, then the Lease.

    Two of the three connected Pods share a ReplicaSet, as the two Pods in
    tests/fixtures/gitlab-agent/connect-applied.json do; the third has another.

    :param connectivity: Connection harness with mocked Kubernetes readers.
    """
    case = connectivity
    controllers = {
        POD_NAME: (REPLICA_SET_NAME, REPLICA_SET_UID),
        SECOND_POD_NAME: (OTHER_REPLICA_SET_NAME, OTHER_REPLICA_SET_UID),
        THIRD_POD_NAME: (REPLICA_SET_NAME, REPLICA_SET_UID),
    }
    case.replica_set.side_effect = lambda _kube, name, _expected: controllers[name]
    case.lease.return_value = (
        f"{SECOND_POD_NAME}{HOLDER_SEPARATOR}{HOLDER_SUFFIX}",
        LEASE_UID,
    )
    pods = {
        THIRD_POD_NAME: THIRD_POD_UID,
        POD_NAME: POD_UID,
        SECOND_POD_NAME: SECOND_POD_UID,
    }
    _, correlation, _ = CONNECTION._installation(
        case.gitlab, SELECTION, case.record, case.image
    )
    deadline = CLI_API.RequestDeadline(TIMEOUT, CONNECTION.time.monotonic)

    rows = CONNECTION._runtime_rows(case.kube, correlation, AGENT.id, pods, deadline)

    assert rows == (
        _replica_set_row(OTHER_REPLICA_SET_NAME, OTHER_REPLICA_SET_UID),
        _replica_set_row(REPLICA_SET_NAME, REPLICA_SET_UID),
        json.loads(LEASE_RESOURCE),
    )
    assert case.replica_set.call_args_list == [
        call(case.kube, name, correlation) for name in sorted(pods)
    ]
    case.lease.assert_called_once_with(case.kube, NAMESPACE, AGENT.id)
    case.delay.assert_not_called()
    assert case.kube.mock_calls == []


def test_agent_connection_repeat_proves_original_pod_without_writes(
    connectivity: SimpleNamespace,
) -> None:
    """A fresh read must still prove the recorded native Pod, without a CAS write."""
    case = connectivity
    case.record = _connected(case.record)
    before = deepcopy(case.record.document)

    assert CONNECTION.read_agent_connectivity(*_args(case), timeout=TIMEOUT) == (
        CONNECTION.ConnectedAgentPod(NATIVE, OBSERVATION),
    )

    assert case.record.document == before
    case.delay.assert_not_called()
    case.store.replace.assert_not_called()
    assert [item[0] for item in case.journal.mock_calls] == [
        "scope",
        "provider.project",
        "provider.read_agent",
        "provider.connections",
        "correlate",
    ]


@pytest.mark.parametrize(
    ("target", "field", "changed", "reason", "reads", "pods"),
    (
        ("record", "origin", "https://other.example.test", RECORD_MISMATCH, 0, 0),
        ("record", "image_digest", INSTALL_TEST.OTHER_IMAGE, RECORD_MISMATCH, 0, 0),
        (
            "scope",
            "return_value",
            (OTHER_UID, INSTALL_TEST.NAMESPACE_UID),
            SCOPE_MISMATCH,
            0,
            0,
        ),
        ("agent", "return_value", None, REGISTER_MISMATCH, 0, 0),
        ("query", "return_value", (), ABSENT, 1, 0),
        (
            "query",
            "return_value",
            (replace(NATIVE, agent_version="v1.0.0"),),
            METADATA_MISMATCH,
            1,
            0,
        ),
        (
            "query",
            "return_value",
            (replace(NATIVE, connected_at=(NOW + timedelta(hours=2)).isoformat()),),
            METADATA_MISMATCH,
            1,
            0,
        ),
        (
            "pod",
            "return_value",
            POD.AgentPodObservation(OTHER_UID, PLATFORM_DIGEST),
            POD_MISMATCH,
            1,
            1,
        ),
    ),
    ids=(
        "foreign-origin",
        "changed-image-pin",
        "changed-cluster-scope",
        "absent-original-agent",
        "empty-read-only-never-polls",
        "wrong-agent-version",
        "future-native-connection",
        "replaced-pod-uid",
    ),
)
def test_agent_connection_refuses_one_unverified_identity_without_mutation(
    connectivity: SimpleNamespace,
    target: str,
    field: str,
    changed: object,
    reason: str,
    reads: int,
    pods: int,
) -> None:
    """Reject one changed observation and leave the original ownership intact."""
    case = connectivity
    case.record = _connected(case.record)
    if target == "record":
        case.record.document[field] = changed
    else:
        endpoint = {
            "scope": case.scope,
            "agent": case.provider.read_agent,
            "query": case.provider.connections,
            "pod": case.correlate,
        }[target]
        setattr(endpoint, field, changed)

    with pytest.raises(KUBERNETES.KubernetesError, match=f"^{reason}$"):
        CONNECTION.read_agent_connectivity(*_args(case), timeout=TIMEOUT)

    assert case.provider.connections.call_count == reads
    assert case.correlate.call_count == pods
    case.delay.assert_not_called()
    case.store.replace.assert_not_called()
    case.provider.create_agent.assert_not_called()
    case.provider.create_token.assert_not_called()


def test_fresh_agent_connect_proves_the_installed_record_with_remaining_budget(
    connectivity: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Carry the installed native journal and exact image into bounded proof."""
    case = connectivity
    connect = INSTALL_TEST.CONNECT
    chart = INSTALL_TEST.HELM.VerifiedChart(
        tmp_path / INSTALL_TEST.CHART_ARCHIVE, RECOVERY_TEST.SELECTION.CHART_SHA256
    )
    scope = (case.gitlab, case.kube, SELECTION)
    confirmed = CONNECTION.AgentConnectivity(_connected(case.record), ())
    clock = Mock(side_effect=(0.0, 0.0, INSTALL_SECONDS))
    monkeypatch.setattr(connect, "time", SimpleNamespace(monotonic=clock))
    install = Mock(return_value=case.record)
    confirm = Mock(return_value=confirmed)
    monkeypatch.setattr(connect, "install_fresh_agent", install)
    monkeypatch.setattr(connect, "confirm_fresh_agent_connection", confirm)
    steps = Mock()
    steps.attach_mock(install, "install")
    steps.attach_mock(confirm, "confirm")
    pending = case.record.document

    result = connect.connect_fresh_agent(
        *scope, pending, INSTALL_TEST.KAS, chart, case.image, timeout=TIMEOUT
    )

    assert result is confirmed
    assert steps.mock_calls == [
        call.install(
            *scope, pending, INSTALL_TEST.KAS, chart, case.image.digest, timeout=TIMEOUT
        ),
        call.confirm(
            *scope, case.record, case.image, timeout=TIMEOUT - INSTALL_SECONDS
        ),
    ]


def test_failed_connection_attempts_compensation_of_exact_installed_journal(
    connectivity: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No failed KAS proof can become success after rollback of its installation."""
    case = connectivity
    connect = INSTALL_TEST.CONNECT
    chart = INSTALL_TEST.HELM.VerifiedChart(
        tmp_path / INSTALL_TEST.CHART_ARCHIVE, RECOVERY_TEST.SELECTION.CHART_SHA256
    )
    scope = (case.gitlab, case.kube, SELECTION)
    clock = Mock(side_effect=(0.0, 0.0, 2.0, 5.0))
    monkeypatch.setattr(connect, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(connect, "install_fresh_agent", Mock(return_value=case.record))
    monkeypatch.setattr(
        connect,
        "confirm_fresh_agent_connection",
        Mock(side_effect=KUBERNETES.KubernetesError("fixture_kas_disconnected")),
    )
    compensate = Mock()
    monkeypatch.setattr(connect, "compensate_installed_agent", compensate)
    with pytest.raises(KUBERNETES.KubernetesError, match="fixture_kas_disconnected"):
        connect.connect_fresh_agent(
            *scope,
            case.record.document,
            INSTALL_TEST.KAS,
            chart,
            case.image,
            timeout=TIMEOUT,
        )
    compensate.assert_called_once_with(*scope, case.record, timeout=TIMEOUT - 5.0)
