"""Agent acceptance checker: null snapshot resources and live Agent receipts."""

from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Callable
from http import HTTPStatus
from typing import Final, Union

import pytest
import test_gitlab_agent_credentials as CREDENTIALS_TEST
from conftest import REPO_ROOT, import_script_module

EVIDENCE = import_script_module("lib.python.core.gitlab.cluster_agent.evidence")
KUBE_AGENT = import_script_module("lib.python.core.kubernetes.gitlab_agent")
RECOVERY = import_script_module("lib.python.core.gitlab.cluster_agent.recovery")
KINDS = import_script_module("lib.python.core.kubernetes.kinds")
SCHEMA = import_script_module("lib.python.core.schema")
STATUS = import_script_module("lib.python.sys.status")
TERMS = import_script_module("lib.python.core.gitlab.cluster_agent.terms")

PROJECT_ID: Final = CREDENTIALS_TEST.PROJECT_ID
AGENT_ID: Final = 17
AGENT_NAME: Final = CREDENTIALS_TEST.AGENT_NAME
TOKEN_ID: Final = CREDENTIALS_TEST.TOKEN_ID
NAMESPACE: Final = CREDENTIALS_TEST.NAMESPACE
NAMESPACE_UID: Final = CREDENTIALS_TEST.NAMESPACE_UID
RELEASE: Final = "consumer-agent-release"
RELEASE_REVISION: Final = 1
RELEASE_STORAGE_UID: Final = "77777777-8888-9999-0000-111111111111"
SECRET_NAME: Final = CREDENTIALS_TEST.SECRET_NAME
SECRET_UID: Final = CREDENTIALS_TEST.SECRET_UID
OTHER_UID: Final = "22222222-3333-4444-5555-666666666666"
CONNECTION_ID: Final = CREDENTIALS_TEST.CONNECTION_ID
API_VERSION: Final = "v1"
NAMESPACE_KIND: Final = KINDS.NAMESPACE_KIND
SECRET_KIND: Final = KINDS.SECRET_KIND
RESOURCES_FIELD: Final = CREDENTIALS_TEST.RESOURCES_FIELD
INSTALLATION_FIELD: Final = CREDENTIALS_TEST.INSTALLATION_FIELD
RECORDS_FIELD: Final = "records"
OWNERSHIP_FIELD: Final = "ownership"
AFTER_FIELD: Final = "after"
BEFORE_FIELD: Final = "before"
OPERATION_FIELD: Final = "operation"
ACTION_FIELD: Final = "action"
KIND_FIELD: Final = "kind"
UID_FIELD: Final = "uid"
STATE_FIELD: Final = "state"
POD_KIND: Final = KINDS.POD_KIND
CHANGED_POD_UID: Final = "00000000-0000-0000-0000-000000000000"

# Stable findings and schema diagnostics that check_agent_receipt in evidence.py
# returns or raises for the matrix rows below.
CONNECTION_UNVERIFIED: Final = "agent_connection_unverified"
NOOP_IDENTITY_CHANGED: Final = "agent_connect_noop_identity_changed"
INSTALLATION_ROWS_REJECTED: Final = (
    "invalid_specification:records/0/installation:additionalProperties"
)
NULL_UID_REJECTED: Final = (
    "invalid_specification:records/0/ownership/resources/0/uid:type"
)
DELETE_IDENTITY_UNVERIFIED: Final = "agent_delete_identity_unverified"
# The delete APPLIED branch of the result schema requires after.agent_http_status 404
# and absence mode TRACKED_IDENTITIES with exact_ids_checked true.
AFTER_AGENT_STATUS_REJECTED: Final = (
    "invalid_specification:records/0/after/agent_http_status:const"
)
EXACT_IDS_REJECTED: Final = (
    "invalid_specification:records/0/absence/exact_ids_checked:const"
)
ABSENCE_MODE_REJECTED: Final = "invalid_specification:records/0/absence/mode:const"
ABSENCE_FIELD: Final = "absence"
MODE_FIELD: Final = "mode"
AGENT_STATUS_FIELD: Final = "agent_http_status"
AGENT_ID_FIELD: Final = "agent_id"
NAME_FIELD: Final = "name"
NAMESPACE_FIELD: Final = "namespace"
API_VERSION_FIELD: Final = "api_version"
CREATED_FIELD: Final = "created"
MUTATED_FIELD: Final = "mutated"
CREDENTIALS_FIELD: Final = CREDENTIALS_TEST.CREDENTIALS_FIELD
AGENT_CREDENTIAL_FIELD: Final = "agent_credential"
REPLICA_SET_KIND: Final = KINDS.REPLICA_SET_KIND
LEASE_KIND: Final = KINDS.LEASE_KIND

FIXTURE_DIR: Final = REPO_ROOT / "tests" / "fixtures" / "gitlab-agent"

# Receipt the real `gitlab_agent.py connect` wrote for a repeat connect (NO_OP) at
# commit 6fa270e. Hosts, the project path and the local target path are
# replaced with example values; UIDs, IDs and digests are the observed values.
# Each case decodes its own copy of this text.
CONNECT_NO_OP_RECEIPT: Final = (FIXTURE_DIR / "connect-no-op.json").read_text(
    encoding="utf-8"
)

# Receipts the real `gitlab_agent.py` wrote at commit 0f68918 for one run: connect
# (APPLIED, records the ReplicaSet and Lease rows), confirmed delete (APPLIED) and
# a repeat delete (NO_OP). The same values as connect-no-op.json are replaced with
# example values, and the token Secret UID that gitleaks flags as generic-api-key
# is replaced with a low-entropy UUID everywhere it appears.
CONNECT_APPLIED_RECEIPT: Final = (FIXTURE_DIR / "connect-applied.json").read_text(
    encoding="utf-8"
)
DELETE_APPLIED_RECEIPT: Final = (FIXTURE_DIR / "delete-applied.json").read_text(
    encoding="utf-8"
)
DELETE_NO_OP_RECEIPT: Final = (FIXTURE_DIR / "delete-no-op.json").read_text(
    encoding="utf-8"
)
# Delete APPLIED at commit 0f68918 that resumed a DELETING record of the run in
# connect-no-op.json: an earlier attempt had already removed the Agent, its token
# and every object except the Namespace and the ConfigMap. Its token Secret UID uses
# the same replacement as connect-no-op.json.
DELETE_RESUMED_RECEIPT: Final = (FIXTURE_DIR / "delete-resumed.json").read_text(
    encoding="utf-8"
)

JSONRow = CREDENTIALS_TEST.JSONRow


def _row(kind: str, name: str, namespace: str, uid: str) -> dict[str, object]:
    """Build one present, run-created resource row as ownership.resources records it.

    :param kind: Kubernetes kind of the resource.
    :param name: Resource name.
    :param namespace: Resource namespace; empty for a cluster-scoped kind.
    :param uid: Native UID observed for the resource.
    :returns: Resource row with API version, identity, creation marker and state.
    """
    return {
        "api_version": API_VERSION,
        "kind": kind,
        "name": name,
        "namespace": namespace,
        "uid": uid,
        "created": True,
        "state": TERMS.PRESENT,
    }


OWNED_ROWS: Final = json.dumps(
    [
        _row(NAMESPACE_KIND, NAMESPACE, "", NAMESPACE_UID),
        _row(SECRET_KIND, SECRET_NAME, NAMESPACE, SECRET_UID),
    ]
)
# The same rows with the Secret UID replaced.
CHANGED_ROWS: Final = json.dumps(
    [
        _row(NAMESPACE_KIND, NAMESPACE, "", NAMESPACE_UID),
        _row(SECRET_KIND, SECRET_NAME, NAMESPACE, OTHER_UID),
    ]
)
SCALARS: Final = json.dumps(
    {
        "project_id": PROJECT_ID,
        "agent_id": AGENT_ID,
        "agent_name": AGENT_NAME,
        "namespace": NAMESPACE,
        "namespace_uid": NAMESPACE_UID,
        "release": RELEASE,
        "release_revision": RELEASE_REVISION,
        "release_storage_uid": RELEASE_STORAGE_UID,
    }
)

# A connected snapshot and the ownership it was read from. The snapshot writes
# resources: null, which means "equal to ownership.resources". Each case
# copies this pair and changes only the entry it names.
CONNECTED_BASELINE: Final = json.dumps(
    {
        "owner": {
            **json.loads(SCALARS),
            RECOVERY.CREDENTIAL_ID: TOKEN_ID,
            RESOURCES_FIELD: json.loads(OWNED_ROWS),
        },
        "snapshot": {
            **json.loads(SCALARS),
            "agent_http_status": HTTPStatus.OK.value,
            EVIDENCE.CREDENTIAL_IDS: [TOKEN_ID],
            EVIDENCE.CREDENTIAL_HTTP_STATUS: HTTPStatus.OK.value,
            RESOURCES_FIELD: None,
            "connection_ids": [CONNECTION_ID],
        },
    }
)


@pytest.mark.parametrize(
    ("change", "accepted"),
    (
        pytest.param(lambda pair: None, True, id="null-resolves-to-owned-rows"),
        pytest.param(
            lambda pair: pair["owner"][RESOURCES_FIELD][1].update(
                {"state": TERMS.ABSENT}
            ),
            False,
            id="null-with-owned-row-not-present",
        ),
        pytest.param(
            lambda pair: pair["snapshot"].update({"agent_id": AGENT_ID + 1}),
            False,
            id="null-with-other-agent",
        ),
        pytest.param(
            lambda pair: pair["snapshot"].update(
                {RESOURCES_FIELD: json.loads(OWNED_ROWS)}
            ),
            True,
            id="explicit-rows-equal-to-owned",
        ),
        pytest.param(
            lambda pair: pair["snapshot"].update(
                {RESOURCES_FIELD: json.loads(CHANGED_ROWS)}
            ),
            False,
            id="explicit-row-with-other-uid",
        ),
        pytest.param(
            lambda pair: pair["snapshot"].update({RESOURCES_FIELD: []}),
            False,
            id="explicit-empty-rows",
        ),
    ),
)
def test_connected_snapshot_resolves_null_resources_to_owned_rows(
    change: Callable[[JSONRow], object], accepted: bool
) -> None:
    """Compare a null snapshot resource list as the owner's rows and keep explicit lists.

    :param change: Edits one entry of the snapshot/owner copy.
    :param accepted: Whether the checker must accept the snapshot as connected.
    """
    pair = json.loads(CONNECTED_BASELINE)
    change(pair)
    unchanged = copy.deepcopy(pair)

    result = EVIDENCE._connected_snapshot(pair["snapshot"], pair["owner"])

    assert result is accepted
    assert pair == unchanged


def _record(receipt: JSONRow) -> JSONRow:
    """Return the one record of a decoded Agent receipt.

    :param receipt: Decoded receipt copy.
    :returns: ``records[0]``, the row the checker reads.
    """
    (record,) = receipt[RECORDS_FIELD]
    return record


def _owned_rows(record: JSONRow) -> list[JSONRow]:
    """Return the record's ``ownership.resources`` list, the one place rows live.

    :param record: Record copy.
    :returns: The owned resource rows, by reference.
    """
    return record[OWNERSHIP_FIELD][RESOURCES_FIELD]


def _outcome(receipt: JSONRow) -> Union[tuple[str, ...], str]:
    """Run the receipt checker and keep a schema rejection as its message.

    :param receipt: Decoded receipt copy.
    :returns: The checker's findings, or the ``SpecificationError`` message when
        its first step, result schema validation, rejects the receipt.
    """
    try:
        return EVIDENCE.check_agent_receipt(receipt)
    except SCHEMA.SpecificationError as error:
        return str(error)


def _after_rows_unknown(record: JSONRow) -> None:
    """Write the owned rows into ``after`` with state UNKNOWN.

    :param record: Record copy to change.
    """
    record[AFTER_FIELD][RESOURCES_FIELD] = [
        {**item, STATE_FIELD: STATUS.UNKNOWN} for item in _owned_rows(record)
    ]


def _owned_pods_removed(record: JSONRow) -> None:
    """Drop the Pod rows from ``ownership.resources``.

    :param record: Record copy to change.
    """
    record[OWNERSHIP_FIELD][RESOURCES_FIELD] = [
        item for item in _owned_rows(record) if item[KIND_FIELD] != POD_KIND
    ]


def _owned_pod_uid_changed(record: JSONRow) -> None:
    """Give the first owned Pod row a UID that no connection item names.

    :param record: Record copy to change.
    """
    pod = next(item for item in _owned_rows(record) if item[KIND_FIELD] == POD_KIND)
    pod[UID_FIELD] = CHANGED_POD_UID


def _installation_rows_added(record: JSONRow) -> None:
    """Copy the owned rows into ``installation.resources``, the removed field.

    :param record: Record copy to change.
    """
    record[INSTALLATION_FIELD][RESOURCES_FIELD] = copy.deepcopy(_owned_rows(record))


def test_live_connect_no_op_receipt_passes_with_null_snapshot_resources() -> None:
    """Accept the recorded repeat-connect receipt whose snapshots hold ``resources: null``.

    The command writes ``resources: null`` in ``after`` and ``before`` for rows
    equal to ``ownership.resources`` and writes no ``installation.resources``. The
    checker must resolve the null rows to the owned rows, find each connected Pod
    there, and leave the receipt unchanged.
    """
    receipt = json.loads(CONNECT_NO_OP_RECEIPT)
    record = _record(receipt)

    findings = EVIDENCE.check_agent_receipt(receipt)

    assert findings == ()
    assert (receipt[OPERATION_FIELD], record[ACTION_FIELD]) == (
        TERMS.CONNECT,
        STATUS.NO_OP,
    )
    assert record[AFTER_FIELD][RESOURCES_FIELD] is None
    assert record[BEFORE_FIELD][RESOURCES_FIELD] is None
    assert RESOURCES_FIELD not in record[INSTALLATION_FIELD]
    assert receipt == json.loads(CONNECT_NO_OP_RECEIPT)


@pytest.mark.parametrize(
    ("change", "expected"),
    (
        pytest.param(
            lambda record: record[AFTER_FIELD].update({RESOURCES_FIELD: []}),
            (CONNECTION_UNVERIFIED,),
            id="after-rows-empty",
        ),
        pytest.param(
            _after_rows_unknown,
            (CONNECTION_UNVERIFIED,),
            id="after-rows-state-unknown",
        ),
        pytest.param(
            _owned_pods_removed,
            (CONNECTION_UNVERIFIED,),
            id="owned-pod-rows-removed",
        ),
        pytest.param(
            _owned_pod_uid_changed,
            (CONNECTION_UNVERIFIED,),
            id="owned-pod-uid-changed",
        ),
        pytest.param(
            lambda record: record[BEFORE_FIELD].update({RESOURCES_FIELD: []}),
            (NOOP_IDENTITY_CHANGED,),
            id="before-rows-empty",
        ),
        pytest.param(
            _installation_rows_added,
            INSTALLATION_ROWS_REJECTED,
            id="installation-rows-added",
        ),
        pytest.param(
            lambda record: _owned_rows(record)[0].update({UID_FIELD: None}),
            NULL_UID_REJECTED,
            id="owned-uid-null",
        ),
    ),
)
def test_live_connect_no_op_receipt_rejects_one_broken_invariant(
    change: Callable[[JSONRow], object], expected: Union[tuple[str, ...], str]
) -> None:
    """Reject a copy of the live receipt that differs from it in one invariant.

    Each row is a shape a producer or a hand-edited receipt can carry:

    - ``after-rows-empty`` and ``before-rows-empty``: the fresh snapshot that
      ``_snapshot`` in receipt.py writes, where the connected snapshot belongs;
    - ``after-rows-state-unknown``: the rows ``_recorded_snapshot`` writes for a
      tracked delete plan;
    - ``owned-pod-rows-removed`` and ``owned-pod-uid-changed``: ownership that does
      not hold the Pod a connection item names, as after a Pod replacement;
    - ``installation-rows-added``: the result shape before ``installation.resources``
      was removed;
    - ``owned-uid-null``: an owned row whose UID was never observed.

    :param change: Edits one field of the record copy.
    :param expected: Findings returned, or the schema error message raised, by
        ``check_agent_receipt``.
    """
    assert _outcome(json.loads(CONNECT_NO_OP_RECEIPT)) == ()
    receipt = json.loads(CONNECT_NO_OP_RECEIPT)

    change(_record(receipt))

    assert _outcome(receipt) == expected


def _rows_of_kind(rows: list[JSONRow], kind: str) -> list[JSONRow]:
    """Select the resource rows of one Kubernetes kind.

    :param rows: Resource rows of a snapshot or of ``ownership.resources``.
    :param kind: Kubernetes kind to keep.
    :returns: The matching rows, by reference, in receipt order.
    """
    return [item for item in rows if item[KIND_FIELD] == kind]


def test_live_connect_applied_receipt_records_one_replica_set_and_one_lease() -> None:
    """Accept the recorded fresh-connect receipt and its runtime ownership rows.

    Connect records the ReplicaSet that controls the connected Pods and the agentk
    Lease ``agent-<agent_id>-lock`` once the Lease is held by a connected Pod. Both
    rows are marked created by the run, so delete removes them by UID.
    """
    receipt = json.loads(CONNECT_APPLIED_RECEIPT)
    record = _record(receipt)
    ownership = record[OWNERSHIP_FIELD]
    rows = _owned_rows(record)

    findings = EVIDENCE.check_agent_receipt(receipt)

    assert findings == ()
    assert (receipt[OPERATION_FIELD], record[ACTION_FIELD]) == (
        TERMS.CONNECT,
        STATUS.APPLIED,
    )
    kinds = Counter(item[KIND_FIELD] for item in rows)
    assert (kinds[REPLICA_SET_KIND], kinds[LEASE_KIND]) == (1, 1)
    (replica_set,) = _rows_of_kind(rows, REPLICA_SET_KIND)
    (lease,) = _rows_of_kind(rows, LEASE_KIND)
    assert replica_set[API_VERSION_FIELD] == KINDS.APPS_API_VERSION
    assert lease[API_VERSION_FIELD] == KINDS.COORDINATION_API_VERSION
    assert lease[NAME_FIELD] == KUBE_AGENT.agent_lease_name(ownership[AGENT_ID_FIELD])
    for item in (replica_set, lease):
        assert item[CREATED_FIELD] is True
        assert item[STATE_FIELD] == TERMS.PRESENT
        assert item[NAMESPACE_FIELD] == ownership[NAMESPACE_FIELD]
    assert receipt == json.loads(CONNECT_APPLIED_RECEIPT)


def test_live_delete_applied_receipt_passes_with_tracked_identities() -> None:
    """Accept the recorded confirmed-delete receipt as delete APPLIED evidence.

    ``before`` is the connected snapshot (``resources: null``, every owned row
    present), ``after`` reads the Agent and token 404 and every created row ABSENT,
    including the ReplicaSet and Lease, and the token state is ABSENT.
    """
    receipt = json.loads(DELETE_APPLIED_RECEIPT)
    record = _record(receipt)

    findings = EVIDENCE.check_agent_receipt(receipt)

    assert findings == ()
    assert (receipt[OPERATION_FIELD], record[ACTION_FIELD]) == (
        TERMS.DELETE,
        STATUS.APPLIED,
    )
    assert receipt[MUTATED_FIELD] is True
    assert record[BEFORE_FIELD][RESOURCES_FIELD] is None
    after = record[AFTER_FIELD][RESOURCES_FIELD]
    assert {item[STATE_FIELD] for item in after} == {TERMS.ABSENT}
    for kind in (REPLICA_SET_KIND, LEASE_KIND):
        (row,) = _rows_of_kind(after, kind)
        assert row[CREATED_FIELD] is True
    token = record[CREDENTIALS_FIELD][AGENT_CREDENTIAL_FIELD]
    assert (token[STATE_FIELD], token[CREATED_FIELD]) == (TERMS.ABSENT, False)
    assert receipt == json.loads(DELETE_APPLIED_RECEIPT)


def _after_replica_set_present(record: JSONRow) -> None:
    """Report the created ReplicaSet as still present after the delete.

    :param record: Record copy to change.
    """
    (row,) = _rows_of_kind(record[AFTER_FIELD][RESOURCES_FIELD], REPLICA_SET_KIND)
    row[STATE_FIELD] = TERMS.PRESENT


def _after_lease_removed(record: JSONRow) -> None:
    """Drop the Lease row from ``after.resources``.

    :param record: Record copy to change.
    """
    after = record[AFTER_FIELD]
    after[RESOURCES_FIELD] = [
        item for item in after[RESOURCES_FIELD] if item[KIND_FIELD] != LEASE_KIND
    ]


def _logical_absence() -> JSONRow:
    """Return the absence block of the live repeat-delete (NO_OP) receipt.

    :returns: A LOGICAL_SCOPE absence with ``exact_ids_checked`` false, as the
        logical delete path writes it.
    """
    return _record(json.loads(DELETE_NO_OP_RECEIPT))[ABSENCE_FIELD]


@pytest.mark.parametrize(
    ("change", "expected"),
    (
        pytest.param(
            lambda record: record[BEFORE_FIELD].update(
                {AGENT_STATUS_FIELD: HTTPStatus.NOT_FOUND.value}
            ),
            (DELETE_IDENTITY_UNVERIFIED,),
            id="before-agent-404",
        ),
        pytest.param(
            _after_replica_set_present,
            (DELETE_IDENTITY_UNVERIFIED,),
            id="after-created-replica-set-present",
        ),
        pytest.param(
            lambda record: record[AFTER_FIELD].update(
                {AGENT_STATUS_FIELD: HTTPStatus.OK.value}
            ),
            AFTER_AGENT_STATUS_REJECTED,
            id="after-agent-200",
        ),
        pytest.param(
            _after_lease_removed,
            (DELETE_IDENTITY_UNVERIFIED,),
            id="after-lease-row-missing",
        ),
        pytest.param(
            lambda record: record[ABSENCE_FIELD].update(
                {MODE_FIELD: _logical_absence()[MODE_FIELD]}
            ),
            EXACT_IDS_REJECTED,
            id="absence-mode-logical-scope",
        ),
        pytest.param(
            lambda record: record.update({ABSENCE_FIELD: _logical_absence()}),
            ABSENCE_MODE_REJECTED,
            id="absence-from-logical-delete",
        ),
    ),
)
def test_live_delete_applied_receipt_rejects_one_broken_invariant(
    change: Callable[[JSONRow], object], expected: Union[tuple[str, ...], str]
) -> None:
    """Reject a copy of the live delete receipt that differs from it in one invariant.

    Each row is a shape a producer or a hand-edited receipt can carry:

    - ``before-agent-404``: the before read of a resumed delete, where an earlier
      attempt already removed the Agent; ``_delete_applied`` requires the connected
      snapshot;
    - ``after-created-replica-set-present`` and ``after-lease-row-missing``: a
      created runtime row that survived the delete or is not reported at all;
    - ``after-agent-200``: the Agent still registered; the result schema requires
      404 for delete APPLIED;
    - ``absence-mode-logical-scope``: only the mode changed; the schema's
      LOGICAL_SCOPE rule rejects ``exact_ids_checked`` true first;
    - ``absence-from-logical-delete``: the whole LOGICAL_SCOPE absence a logical
      delete writes; the delete APPLIED rule requires TRACKED_IDENTITIES.

    :param change: Edits one field of the record copy.
    :param expected: Findings returned, or the schema error message raised, by
        ``check_agent_receipt``.
    """
    assert _outcome(json.loads(DELETE_APPLIED_RECEIPT)) == ()
    receipt = json.loads(DELETE_APPLIED_RECEIPT)

    change(_record(receipt))

    assert _outcome(receipt) == expected


def test_live_resumed_delete_receipt_is_not_tracked_delete_evidence() -> None:
    """Reject the recorded resumed delete: its before read is not the connected state.

    The run returned PASS and removed every remaining object, but the Agent and token
    read 404 and most owned rows ABSENT before this invocation wrote anything, so the
    receipt cannot prove that this invocation deleted the recorded identities.
    """
    receipt = json.loads(DELETE_RESUMED_RECEIPT)
    record = _record(receipt)
    before = record[BEFORE_FIELD]

    findings = EVIDENCE.check_agent_receipt(receipt)

    assert findings == (DELETE_IDENTITY_UNVERIFIED,)
    assert (receipt[OPERATION_FIELD], record[ACTION_FIELD]) == (
        TERMS.DELETE,
        STATUS.APPLIED,
    )
    assert before[AGENT_STATUS_FIELD] == HTTPStatus.NOT_FOUND.value
    assert TERMS.ABSENT in {item[STATE_FIELD] for item in before[RESOURCES_FIELD]}
    assert receipt == json.loads(DELETE_RESUMED_RECEIPT)


def test_live_repeat_delete_receipt_passes_as_logical_no_op() -> None:
    """Accept the recorded repeat delete as NO_OP: no ownership, no write.

    The second delete of the same run found no ownership record, wrote nothing and
    proved the logical scope empty.
    """
    receipt = json.loads(DELETE_NO_OP_RECEIPT)
    record = _record(receipt)

    findings = EVIDENCE.check_agent_receipt(receipt)

    assert findings == ()
    assert (receipt[OPERATION_FIELD], record[ACTION_FIELD]) == (
        TERMS.DELETE,
        STATUS.NO_OP,
    )
    assert receipt[MUTATED_FIELD] is False
    assert record[OWNERSHIP_FIELD] is None
    assert receipt == json.loads(DELETE_NO_OP_RECEIPT)
