"""Agent acceptance checker: null snapshot resources and the live connect NO_OP receipt."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from http import HTTPStatus
from typing import Final, Union

import pytest
import test_gitlab_agent_credentials as CREDENTIALS_TEST
from conftest import REPO_ROOT, import_script_module

EVIDENCE = import_script_module("lib.python.core.gitlab.cluster_agent.evidence")
RECOVERY = import_script_module("lib.python.core.gitlab.cluster_agent.recovery")
RUNNER_SCOPE = import_script_module("lib.python.core.kubernetes.runner_scope")
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
NAMESPACE_KIND: Final = RUNNER_SCOPE.NAMESPACE_KIND
SECRET_KIND: Final = RUNNER_SCOPE.SECRET_KIND
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
POD_KIND: Final = "Pod"
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

# Receipt the real `gitlab_agent.py connect` wrote for a repeat connect (NO_OP) at
# commit 6fa270e. Hosts, the project path and the local target path are
# replaced with example values; UIDs, IDs and digests are the observed values.
# Each case decodes its own copy of this text.
CONNECT_NO_OP_RECEIPT: Final = (
    REPO_ROOT / "tests" / "fixtures" / "gitlab-agent" / "connect-no-op.json"
).read_text(encoding="utf-8")

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
