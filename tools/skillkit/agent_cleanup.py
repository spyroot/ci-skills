"""Build the ``gitlab_agent_smoke_cleanup`` record of one Agent qualification series.

`tools/check_live_acceptance.py` accepts the five Agent receipts of one run only with
one ``smoke_cleanup`` record. ``check_agent_series`` in
``ci-skills/lib/python/core/gitlab/cluster_agent/evidence.py`` verifies it: an
``after`` stage read after the confirmed delete and a ``repeat`` stage read after the
repeat delete. Each stage holds a 404 read of the Agent and of its registration
token, one row per resource of the original ownership record, and the release and
run-resource absence that the installed ``verify_agent_logical_absence`` reads.

This module imports nothing from ``lib``. `tools/qualify_agent_cleanup.py` binds the
installed copy's services and passes them in, so these functions also run on test
fakes.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from http import HTTPStatus
from typing import Any, Final, Optional, Protocol

from skillkit.smoke_cleanup import (
    CAPTURED_AT_FIELD,
    EXECUTION_HOST_FIELD,
    KIND_FIELD,
    SKILL_DIGEST_FIELD,
    SKILL_FIELD,
    SMOKE_CLEANUP_FIELD,
    STATUS_FIELD,
    TESTED_REVISION_FIELD,
)

AGENT_CLEANUP_KIND: Final = "gitlab_agent_smoke_cleanup"
AGENT_CLEANUP_STATUS: Final = "PASS_INDEPENDENT_ABSENCE_AND_REPEAT"
# Stage keys of the record; ``after`` follows the delete, ``repeat`` the repeat delete.
AFTER_STAGE: Final = "after"
REPEAT_STAGE: Final = "repeat"
# Keys of the record and of the original ownership document it carries.
RUN_ID_FIELD: Final = "run_id"
ORIGINAL_OWNERSHIP_FIELD: Final = "original_ownership"
AGENT_FIELD: Final = "agent"
CREDENTIAL_FIELD: Final = "registration_credential"
AGENT_ID_FIELD: Final = "agent_id"
CREDENTIAL_ID_FIELD: Final = "registration_credential_id"
RESOURCES_FIELD: Final = "resources"
RELEASE_ABSENT_FIELD: Final = "release_absent"
RUN_RESOURCES_ABSENT_FIELD: Final = "run_resources_absent"
# Keys of one ownership resource row and of its absence row.
KIND_KEY: Final = "kind"
NAME_KEY: Final = "name"
NAMESPACE_KEY: Final = "namespace"
UID_KEY: Final = "uid"
CREATED_KEY: Final = "created"
OBSERVED_UID_KEY: Final = "observed_uid"
ABSENT_KEY: Final = "absent"
PRESERVED_KEY: Final = "preserved"
ID_KEY: Final = "id"
HTTP_STATUS_KEY: Final = "http_status"
METADATA_KEY: Final = "metadata"
# Receipt keys read from the connect APPLIED and delete NO_OP receipts.
DIGEST_KEY: Final = "digest"
RECORDS_KEY: Final = "records"
OWNERSHIP_KEY: Final = "ownership"
# The repeat delete has no ownership record left; its selector names the run.
FILTERS_KEY: Final = "filters"


class AgentCleanupError(RuntimeError):
    """A stage read shows an object present or a record lacks a required value."""


class AgentReads(Protocol):
    """The installed ``AgentProvider`` reads this module calls."""

    def read_agent(self, agent_id: int) -> Optional[object]:
        """Return the registration, or None after a native 404."""

    def read_token(self, agent_id: int, token_id: int) -> Optional[object]:
        """Return the token, or None after a native 404."""


class ObjectReads(Protocol):
    """The installed ``KubernetesService`` read this module calls."""

    def read(self, kind: str, name: str, namespace: str) -> Optional[Mapping[str, Any]]:
        """Return the native object, or None when it does not exist."""


def _absent_provider_object(identifier: object) -> dict[str, object]:
    return {
        ID_KEY: identifier,
        HTTP_STATUS_KEY: HTTPStatus.NOT_FOUND.value,
        ABSENT_KEY: True,
        PRESERVED_KEY: False,
    }


def _resource_row(
    objects: ObjectReads, resource: Mapping[str, Any]
) -> dict[str, object]:
    kind = resource[KIND_KEY]
    name = resource[NAME_KEY]
    namespace = resource[NAMESPACE_KEY]
    uid = resource[UID_KEY]
    created = resource[CREATED_KEY]
    if not isinstance(created, bool):
        raise AgentCleanupError("agent_cleanup_ownership_invalid")
    native = objects.read(kind, name, namespace)
    observed = None if native is None else native.get(METADATA_KEY, {}).get(UID_KEY)
    if created and observed is not None:
        raise AgentCleanupError(f"agent_cleanup_resource_present:{kind}/{name}")
    if not created and observed != uid:
        raise AgentCleanupError(
            f"agent_cleanup_borrowed_resource_changed:{kind}/{name}"
        )
    return {
        KIND_KEY: kind,
        NAME_KEY: name,
        NAMESPACE_KEY: namespace,
        UID_KEY: uid,
        OBSERVED_UID_KEY: observed,
        ABSENT_KEY: created,
        PRESERVED_KEY: not created,
    }


def observe_stage(
    agents: AgentReads,
    objects: ObjectReads,
    ownership: Mapping[str, Any],
    logical_absence: Callable[[], object],
) -> dict[str, object]:
    """Read one cleanup stage after a delete, without changing anything.

    :param agents: Installed Agent provider bound to the selected project.
    :param objects: Installed Kubernetes service bound to the selected cluster.
    :param ownership: Original ownership record from the connect APPLIED receipt.
    :param logical_absence: Installed release and run-resource absence read; it
        raises when either is still present.
    :returns: One ``cleanup_observation`` of the Agent result schema.
    :raises AgentCleanupError: The Agent, its token or a run-created object is
        present, or a borrowed object changed.
    """
    agent_id = ownership[AGENT_ID_FIELD]
    token_id = ownership[CREDENTIAL_ID_FIELD]
    if agents.read_agent(agent_id) is not None:
        raise AgentCleanupError("agent_cleanup_registration_present")
    if agents.read_token(agent_id, token_id) is not None:
        raise AgentCleanupError("agent_cleanup_credential_present")
    rows = [_resource_row(objects, item) for item in ownership[RESOURCES_FIELD]]
    logical_absence()
    return {
        AGENT_FIELD: _absent_provider_object(agent_id),
        CREDENTIAL_FIELD: _absent_provider_object(token_id),
        RESOURCES_FIELD: rows,
        RELEASE_ABSENT_FIELD: True,
        RUN_RESOURCES_ABSENT_FIELD: True,
    }


def connect_ownership(connect: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the ownership record of a connect APPLIED receipt.

    :param connect: Connect APPLIED receipt of the series.
    :returns: Its single record's ownership document.
    :raises AgentCleanupError: The receipt does not hold exactly one record.
    """
    records = connect.get(RECORDS_KEY)
    if not isinstance(records, list) or len(records) != 1:
        raise AgentCleanupError("agent_cleanup_connect_receipt_invalid")
    return records[0][OWNERSHIP_KEY]


def cleanup_record(
    connect: Mapping[str, Any],
    after: Mapping[str, Any],
    repeat: Mapping[str, Any],
    captured_at: datetime,
) -> dict[str, object]:
    """Assemble the record from the connect receipt and the two stage reads.

    :param connect: Connect APPLIED receipt of the series.
    :param after: Stage read after the confirmed delete.
    :param repeat: Stage read after the repeat delete.
    :param captured_at: Time the record is written, at or after the repeat receipt.
    :returns: ``smoke_cleanup`` record of the Agent result schema.
    :raises AgentCleanupError: The connect receipt is not a single-record receipt.
    """  # noqa: DOC502 - connect_ownership raises for a malformed receipt.
    ownership = connect_ownership(connect)
    return {
        KIND_FIELD: AGENT_CLEANUP_KIND,
        STATUS_FIELD: AGENT_CLEANUP_STATUS,
        CAPTURED_AT_FIELD: captured_at.isoformat(),
        EXECUTION_HOST_FIELD: connect[EXECUTION_HOST_FIELD],
        SKILL_DIGEST_FIELD: connect[SKILL_FIELD][DIGEST_KEY],
        TESTED_REVISION_FIELD: connect[TESTED_REVISION_FIELD],
        RUN_ID_FIELD: ownership[RUN_ID_FIELD],
        ORIGINAL_OWNERSHIP_FIELD: dict(ownership),
        AFTER_STAGE: dict(after),
        REPEAT_STAGE: dict(repeat),
    }


def attach_record(
    repeat_receipt: Mapping[str, Any], record: Mapping[str, Any]
) -> dict[str, object]:
    """Return the delete NO_OP receipt with the record under ``smoke_cleanup``.

    :param repeat_receipt: Delete NO_OP receipt of the same run.
    :param record: Record built by ``cleanup_record``.
    :returns: Copy of the receipt carrying the record.
    :raises AgentCleanupError: The receipt already carries a record, belongs to
        another run, or was captured after the record.
    """
    if SMOKE_CLEANUP_FIELD in repeat_receipt:
        raise AgentCleanupError("agent_cleanup_record_present")
    if repeat_receipt[FILTERS_KEY][RUN_ID_FIELD] != record[RUN_ID_FIELD]:
        raise AgentCleanupError("agent_cleanup_run_mismatch")
    if datetime.fromisoformat(
        repeat_receipt[CAPTURED_AT_FIELD]
    ) > datetime.fromisoformat(record[CAPTURED_AT_FIELD]):
        raise AgentCleanupError("agent_cleanup_record_before_repeat")
    return {**repeat_receipt, SMOKE_CLEANUP_FIELD: dict(record)}
