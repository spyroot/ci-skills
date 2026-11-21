"""Delete APPLIED result that receipt.py builds from the native delete observations."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from http import HTTPStatus
from types import SimpleNamespace
from typing import Final

import pytest
import test_gitlab_agent_credentials as CREDENTIALS_TEST
import test_gitlab_agent_evidence as EVIDENCE_TEST
from conftest import import_script_module

ABSENCE = import_script_module("lib.python.core.gitlab.cluster_agent.absence")
CLI_API = import_script_module("lib.python.core.cli_api")
DELETE = import_script_module("lib.python.core.gitlab.cluster_agent.delete")
EVIDENCE = import_script_module("lib.python.core.gitlab.cluster_agent.evidence")
KINDS = import_script_module("lib.python.core.kubernetes.kinds")
RECEIPT = import_script_module("lib.python.core.gitlab.cluster_agent.receipt")
RECOVERY = import_script_module("lib.python.core.gitlab.cluster_agent.recovery")
RUN_SCOPE = import_script_module("lib.python.core.kubernetes.run_scope")
SELECTION = import_script_module("lib.python.core.gitlab.cluster_agent.selection")
STATUS = import_script_module("lib.python.sys.status")
TARGET = import_script_module("lib.python.core.target")
TERMS = import_script_module("lib.python.core.gitlab.cluster_agent.terms")
WORKFLOW = import_script_module("lib.python.core.gitlab.cluster_agent.workflow")

# Live delete APPLIED receipt, sanitized; see DELETE_APPLIED_RECEIPT in
# test_gitlab_agent_evidence.py. Each case decodes its own copy of this text.
DELETE_APPLIED_RECEIPT: Final = EVIDENCE_TEST.DELETE_APPLIED_RECEIPT
JSONRow = CREDENTIALS_TEST.JSONRow

RECORD_DEFINITION: Final = CREDENTIALS_TEST.RECORD_DEFINITION
OWNERSHIP_FIELD: Final = EVIDENCE_TEST.OWNERSHIP_FIELD
BEFORE_FIELD: Final = EVIDENCE_TEST.BEFORE_FIELD
AFTER_FIELD: Final = EVIDENCE_TEST.AFTER_FIELD
RESOURCES_FIELD: Final = EVIDENCE_TEST.RESOURCES_FIELD
KIND_FIELD: Final = EVIDENCE_TEST.KIND_FIELD
UID_FIELD: Final = EVIDENCE_TEST.UID_FIELD
STATE_FIELD: Final = EVIDENCE_TEST.STATE_FIELD
AGENT_STATUS_FIELD: Final = EVIDENCE_TEST.AGENT_STATUS_FIELD
AGENT_ID_FIELD: Final = EVIDENCE_TEST.AGENT_ID_FIELD
CREDENTIAL_IDS: Final = EVIDENCE.CREDENTIAL_IDS
CREDENTIAL_HTTP_STATUS: Final = EVIDENCE.CREDENTIAL_HTTP_STATUS
RELEASE_REVISION_FIELD: Final = "release_revision"
RELEASE_STORAGE_UID_FIELD: Final = "release_storage_uid"
CAPTURED_AT_FIELD: Final = "captured_at"
CONFIG_MAP_KIND: Final = "ConfigMap"
# RecoveryRecord.resource_version of the ownership journal; the result never copies it.
JOURNAL_VERSION: Final = "1"
OTHER_CLUSTER_UID: Final = "00000000-0000-4000-8000-000000000099"
RESULT_MISMATCH: Final = "agent_receipt_native_observation_mismatch"

# Kinds of the recorded rows in the live receipt's ownership record.
OWNED_KINDS: Final = frozenset(
    row[KIND_FIELD]
    for row in EVIDENCE_TEST._owned_rows(
        EVIDENCE_TEST._record(json.loads(DELETE_APPLIED_RECEIPT))
    )
)
# What a resumed delete reads before its writes: an earlier attempt removed the
# Agent, its token, the Helm release and every object but the Namespace and the
# ConfigMap that holds the ownership record.
RESUMED_ABSENT_KINDS: Final = OWNED_KINDS - {KINDS.NAMESPACE_KIND, CONFIG_MAP_KIND}
GONE_BEFORE_FIELDS: Final = json.dumps(
    {
        AGENT_STATUS_FIELD: HTTPStatus.NOT_FOUND.value,
        CREDENTIAL_IDS: [],
        CREDENTIAL_HTTP_STATUS: HTTPStatus.NOT_FOUND.value,
        RELEASE_REVISION_FIELD: None,
        RELEASE_STORAGE_UID_FIELD: None,
    }
)


def _invocation(receipt: JSONRow) -> SimpleNamespace:
    """Rebuild the delete invocation that wrote a receipt, from that receipt's values.

    :param receipt: Decoded receipt copy; the ownership document is shared by
        reference with the returned journal and cleanup.
    :returns: Authorities, intent, selection, journal and an ``AgentCleanup`` that
        read every owned object, the Agent, its token and the release present.
    """
    record = EVIDENCE_TEST._record(receipt)
    owner = record[OWNERSHIP_FIELD]
    prerequisites = record["prerequisites"]
    project = SELECTION.AgentProject(owner["project_id"], owner["project_path"])
    selection = SELECTION.ConnectSelection(
        project,
        owner["agent_name"],
        RUN_SCOPE.RunScope(
            owner["run_id"], owner["namespace"], owner["created"]["namespace"]
        ),
        owner["release"],
    )
    calls = record[CREDENTIALS_TEST.CREDENTIALS_FIELD]["gitlab"]["calls"]
    authorities = SimpleNamespace(
        gitlab=SimpleNamespace(
            session=SimpleNamespace(
                origin=receipt["target"],
                target_reference=receipt["filters"]["project"],
                skill=receipt["skill"],
                execution_host=receipt["execution_host"],
                target_source=receipt["target_source"],
            ),
            api=SimpleNamespace(
                requests=tuple(
                    CLI_API.APIRequest(item["method"], item["endpoint"])
                    for item in calls
                )
            ),
        ),
        kas=SimpleNamespace(
            gitlab_version=prerequisites["gitlab_version"],
            version=prerequisites["kas_version"],
            address=prerequisites["kas_address"],
        ),
        cluster_uid=prerequisites["cluster_uid"],
        project=project,
        kubernetes=SimpleNamespace(
            target=SimpleNamespace(routing=TARGET.GitLabRoute(**receipt["routing"]))
        ),
    )
    journal = RECOVERY.RecoveryRecord(
        owner["ownership_record_uid"], JOURNAL_VERSION, owner
    )
    cleanup = DELETE.AgentCleanup(
        ownership=journal,
        resources=tuple(record[AFTER_FIELD][RESOURCES_FIELD]),
        absence=ABSENCE.LogicalAgentAbsence(
            project, selection.release, authorities.cluster_uid
        ),
        agent_present=True,
        token_present=True,
        present=frozenset(row[UID_FIELD] for row in owner[RESOURCES_FIELD]),
        release_present=True,
    )
    return SimpleNamespace(
        authorities=authorities,
        intent=receipt["filters"],
        digest=receipt["plan_digest"],
        selection=selection,
        journal=journal,
        cleanup=cleanup,
    )


def _result(invocation: SimpleNamespace) -> dict[str, object]:
    """Build the result through ``agent_result``, which validates the result schema.

    :param invocation: Delete invocation from ``_invocation``.
    :returns: Schema-validated delete APPLIED result.
    """
    outcome = WORKFLOW.AgentOutcome(
        STATUS.APPLIED,
        invocation.selection,
        invocation.digest,
        invocation.journal,
        invocation.cleanup,
    )
    return RECEIPT.agent_result(invocation.authorities, invocation.intent, outcome)


def test_delete_applied_result_reproduces_the_live_receipt() -> None:
    """Rebuild the recorded delete receipt from the observations its command made.

    The cleanup read every owned object, the Agent, its token and the release
    present before the writes, so ``before`` is the connected snapshot written once
    as ``resources: null`` with Agent and token 200, and the built result equals the
    recorded receipt except its capture time. The checker accepts it.
    """
    receipt = json.loads(DELETE_APPLIED_RECEIPT)

    result = _result(_invocation(receipt))

    assert EVIDENCE.check_agent_receipt(result) == ()
    record = EVIDENCE_TEST._record(result)
    CREDENTIALS_TEST._definition_validator(RECORD_DEFINITION).validate(record)
    before = record[BEFORE_FIELD]
    assert before[RESOURCES_FIELD] is None
    assert (before[AGENT_STATUS_FIELD], before[CREDENTIAL_HTTP_STATUS]) == (
        HTTPStatus.OK.value,
        HTTPStatus.OK.value,
    )
    expected = json.loads(DELETE_APPLIED_RECEIPT)
    del result[CAPTURED_AT_FIELD], expected[CAPTURED_AT_FIELD]
    assert result == expected


@pytest.mark.parametrize(
    ("absent_kinds", "registered", "changed_fields"),
    (
        pytest.param(frozenset(), True, "{}", id="all-present"),
        pytest.param(
            frozenset((KINDS.REPLICA_SET_KIND,)), True, "{}", id="replica-set-absent"
        ),
        pytest.param(frozenset((KINDS.LEASE_KIND,)), True, "{}", id="lease-absent"),
        pytest.param(
            RESUMED_ABSENT_KINDS,
            False,
            GONE_BEFORE_FIELDS,
            id="agent-token-release-absent",
        ),
    ),
)
def test_delete_applied_record_reports_what_the_before_read_found(
    absent_kinds: frozenset[str], registered: bool, changed_fields: str
) -> None:
    """Write ``before`` from the cleanup's read made before the first delete write.

    Rows:

    - ``all-present``: rows equal to ``ownership.resources`` are written once, as
      null.
    - ``replica-set-absent`` and ``lease-absent``: a recorded runtime object was
      already gone at the before read; delete.py accepts that for ReplicaSet and
      Lease rows, and ``before`` lists every owned row with that one ABSENT.
    - ``agent-token-release-absent``: a resumed delete read the Agent and its token
      404 and the Helm release gone, so ``before`` has no credential ID and null
      release revision and storage UID.

    :param absent_kinds: Kinds of the owned objects read absent before the writes.
    :param registered: Whether the Agent, its token and the Helm release were read
        present before the writes.
    :param changed_fields: JSON object of the ``before`` fields that differ from the
        recorded receipt's ``before``.
    """
    receipt = json.loads(DELETE_APPLIED_RECEIPT)
    invocation = _invocation(receipt)
    owned = EVIDENCE_TEST._owned_rows(EVIDENCE_TEST._record(receipt))
    invocation.cleanup = dataclasses.replace(
        invocation.cleanup,
        present=frozenset(
            row[UID_FIELD] for row in owned if row[KIND_FIELD] not in absent_kinds
        ),
        agent_present=registered,
        token_present=registered,
        release_present=registered,
    )
    recorded = EVIDENCE_TEST._record(json.loads(DELETE_APPLIED_RECEIPT))
    expected = {**recorded[BEFORE_FIELD], **json.loads(changed_fields)}
    if absent_kinds:
        expected[RESOURCES_FIELD] = [
            {
                **row,
                STATE_FIELD: (
                    TERMS.ABSENT if row[KIND_FIELD] in absent_kinds else TERMS.PRESENT
                ),
            }
            for row in recorded[OWNERSHIP_FIELD][RESOURCES_FIELD]
        ]

    record = EVIDENCE_TEST._record(_result(invocation))

    CREDENTIALS_TEST._definition_validator(RECORD_DEFINITION).validate(record)
    assert record[BEFORE_FIELD] == expected
    assert record[AFTER_FIELD] == recorded[AFTER_FIELD]
    assert record[OWNERSHIP_FIELD] == recorded[OWNERSHIP_FIELD]
    assert EVIDENCE.check_agent_receipt(_result(invocation)) == (
        () if registered else (EVIDENCE_TEST.DELETE_IDENTITY_UNVERIFIED,)
    )


def _ownership_replaced(invocation: SimpleNamespace) -> None:
    """Pair the outcome's journal with a cleanup of another Agent's ownership record.

    :param invocation: Delete invocation to change.
    """
    document = {
        **invocation.journal.document,
        AGENT_ID_FIELD: invocation.journal.document[AGENT_ID_FIELD] + 1,
    }
    invocation.cleanup = dataclasses.replace(
        invocation.cleanup,
        ownership=dataclasses.replace(invocation.journal, document=document),
    )


def _absence_on_other_cluster(invocation: SimpleNamespace) -> None:
    """Pair the cleanup with a logical absence proved on another cluster.

    :param invocation: Delete invocation to change.
    """
    absence = invocation.cleanup.absence
    invocation.cleanup = dataclasses.replace(
        invocation.cleanup,
        absence=dataclasses.replace(absence, cluster_uid=OTHER_CLUSTER_UID),
    )


@pytest.mark.parametrize(
    "change",
    (
        pytest.param(_ownership_replaced, id="cleanup-of-other-ownership"),
        pytest.param(_absence_on_other_cluster, id="absence-on-other-cluster"),
    ),
)
def test_delete_applied_result_refuses_observations_of_another_delete(
    change: Callable[[SimpleNamespace], None],
) -> None:
    """Refuse a cleanup that does not belong to the journal and cluster of this run.

    A cleanup read for another ownership record, or a logical absence proved on a
    cluster other than the one this invocation selected, would report a delete that
    this invocation did not prove.

    :param change: Edits one observation of the invocation copy.
    """
    invocation = _invocation(json.loads(DELETE_APPLIED_RECEIPT))
    assert EVIDENCE.check_agent_receipt(_result(invocation)) == ()
    invocation = _invocation(json.loads(DELETE_APPLIED_RECEIPT))

    change(invocation)

    with pytest.raises(ValueError, match=f"^{RESULT_MISMATCH}$"):
        _result(invocation)
