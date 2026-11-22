"""Build the ``gitlab_agent_smoke_cleanup`` record of one Agent qualification series.

`tools/check_live_acceptance.py` accepts the five Agent receipts of one run only with
one ``smoke_cleanup`` record. ``check_agent_series`` in
``ci-skills/lib/python/core/gitlab/cluster_agent/evidence.py`` verifies it: an
``after`` stage read after the confirmed delete and a ``repeat`` stage read after the
repeat delete. Each stage holds a 404 read of the Agent and of its registration
token, one row per resource of the original ownership record, and the release and
run-resource absence that the installed ``verify_agent_logical_absence`` reads.

A stage file wraps one stage with its run ID, read time and the digest of the copy
that read it; ``cleanup_record`` requires the ``after`` read before the repeat
receipt and the ``repeat`` read after it.

This module imports nothing from ``lib``. `tools/qualify_agent_cleanup.py` binds the
installed copy's services and keys (`AgentKeys`) and passes them in, so these
functions also run on test fakes.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from typing import Any, Final, Optional, Protocol

from skillkit.smoke_cleanup import (
    AFTER_FIELD,
    AGENT_CLEANUP_KIND,
    CAPTURED_AT_FIELD,
    CLEANUP_PASS,
    EXECUTION_HOST_FIELD,
    KIND_FIELD,
    REPEAT_FIELD,
    REVISION_FIELD,
    SKILL_DIGEST_FIELD,
    SKILL_FIELD,
    SMOKE_CLEANUP_FIELD,
    STATUS_FIELD,
    TESTED_REVISION_FIELD,
)

# Keys this record writes that the installed lib has no constant for.
RUN_ID_FIELD: Final = "run_id"
ORIGINAL_OWNERSHIP_FIELD: Final = "original_ownership"
AGENT_FIELD: Final = "agent"
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
# Ownership keys that select the scope the stage reads.
AGENT_ID_KEY: Final = "agent_id"
AGENT_NAME_KEY: Final = "agent_name"
PROJECT_PATH_KEY: Final = "project_path"
PROJECT_ID_KEY: Final = "project_id"
CLUSTER_UID_KEY: Final = "cluster_uid"
ORIGIN_KEY: Final = "origin"
RELEASE_KEY: Final = "release"
# Receipt keys read from the connect APPLIED and delete NO_OP receipts.
RECORDS_KEY: Final = "records"
OWNERSHIP_KEY: Final = "ownership"
# The repeat delete has no ownership record left; its selector names the run.
FILTERS_KEY: Final = "filters"
# Key of the cleanup observation inside a stage file.
OBSERVATION_KEY: Final = "observation"


class AgentCleanupError(RuntimeError):
    """A stage read shows an object present or an input fails its identity check."""


@dataclass(frozen=True)
class AgentKeys:
    """Keys bound from the installed copy.

    .. attribute :: credential
        :type: str

        Record key of the registration token, ``recovery.CREDENTIAL``.

    .. attribute :: credential_id
        :type: str

        Ownership key of the token's native ID, ``recovery.CREDENTIAL_ID``.

    .. attribute :: digest
        :type: str

        Skill identity key of the package digest, ``provenance.DIGEST_VALUE_KEY``.
    """

    credential: str
    credential_id: str
    digest: str


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
    if created and native is not None:
        raise AgentCleanupError(f"agent_cleanup_resource_present:{kind}/{name}")
    observed = None if native is None else native.get(METADATA_KEY, {}).get(UID_KEY)
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
    keys: AgentKeys,
) -> dict[str, object]:
    """Read one cleanup stage after a delete, without changing anything.

    :param agents: Installed Agent provider bound to the selected project.
    :param objects: Installed Kubernetes service bound to the selected cluster.
    :param ownership: Original ownership record from the connect APPLIED receipt.
    :param logical_absence: Installed release and run-resource absence read; it
        raises when either is still present.
    :param keys: Record and ownership keys bound from the installed copy.
    :returns: One ``cleanup_observation`` of the Agent result schema.
    :raises AgentCleanupError: The Agent, its token or a run-created object is
        present, or a borrowed object changed.
    """
    agent_id = ownership[AGENT_ID_KEY]
    token_id = ownership[keys.credential_id]
    if agents.read_agent(agent_id) is not None:
        raise AgentCleanupError("agent_cleanup_registration_present")
    if agents.read_token(agent_id, token_id) is not None:
        raise AgentCleanupError("agent_cleanup_credential_present")
    rows = [_resource_row(objects, item) for item in ownership[RESOURCES_FIELD]]
    logical_absence()
    return {
        AGENT_FIELD: _absent_provider_object(agent_id),
        keys.credential: _absent_provider_object(token_id),
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


def require_same_copy(
    connect: Mapping[str, Any], skill: Mapping[str, Any], keys: AgentKeys
) -> None:
    """Require that the reading copy is the copy that produced the series.

    :param connect: Connect APPLIED receipt of the series.
    :param skill: Skill identity of the copy bound for this read.
    :param keys: Keys bound from the installed copy.
    :raises AgentCleanupError: The package digest or revision differs.
    """
    recorded = connect[SKILL_FIELD]
    if (
        recorded[keys.digest] != skill[keys.digest]
        or recorded[REVISION_FIELD] != skill[REVISION_FIELD]
    ):
        raise AgentCleanupError("agent_cleanup_skill_mismatch")


def stage_file(
    ownership: Mapping[str, Any],
    observation: Mapping[str, Any],
    skill: Mapping[str, Any],
    captured_at: datetime,
    keys: AgentKeys,
) -> dict[str, object]:
    """Wrap one stage with the run, the read time and the reading copy's digest.

    :param ownership: Original ownership record of the run.
    :param observation: Stage returned by ``observe_stage``.
    :param skill: Skill identity of the copy that read the stage.
    :param captured_at: Time the stage read finished.
    :param keys: Keys bound from the installed copy.
    :returns: Stage file document for ``cleanup_record``.
    """
    return {
        RUN_ID_FIELD: ownership[RUN_ID_FIELD],
        CAPTURED_AT_FIELD: captured_at.isoformat(),
        SKILL_DIGEST_FIELD: skill[keys.digest],
        OBSERVATION_KEY: dict(observation),
    }


def _stage(
    stage: Mapping[str, Any], run_id: str, digest: str
) -> tuple[datetime, Mapping[str, Any]]:
    if stage[RUN_ID_FIELD] != run_id or stage[SKILL_DIGEST_FIELD] != digest:
        raise AgentCleanupError("agent_cleanup_stage_identity_mismatch")
    return datetime.fromisoformat(stage[CAPTURED_AT_FIELD]), stage[OBSERVATION_KEY]


def cleanup_record(
    connect: Mapping[str, Any],
    after: Mapping[str, Any],
    repeat: Mapping[str, Any],
    repeat_receipt: Mapping[str, Any],
    captured_at: datetime,
    keys: AgentKeys,
) -> dict[str, object]:
    """Assemble the record from the connect receipt and the two stage files.

    :param connect: Connect APPLIED receipt of the series.
    :param after: Stage file read after the confirmed delete.
    :param repeat: Stage file read after the repeat delete.
    :param repeat_receipt: Delete NO_OP receipt the record attaches to.
    :param captured_at: Time the record is written.
    :param keys: Keys bound from the installed copy.
    :returns: ``smoke_cleanup`` record of the Agent result schema.
    :raises AgentCleanupError: A stage belongs to another run or copy, or the
        reads are not ordered after-read, repeat receipt, repeat-read, record.
    """
    ownership = connect_ownership(connect)
    run_id = ownership[RUN_ID_FIELD]
    digest = connect[SKILL_FIELD][keys.digest]
    after_time, after_observation = _stage(after, run_id, digest)
    repeat_time, repeat_observation = _stage(repeat, run_id, digest)
    receipt_time = datetime.fromisoformat(repeat_receipt[CAPTURED_AT_FIELD])
    if not after_time < receipt_time <= repeat_time <= captured_at:
        raise AgentCleanupError("agent_cleanup_stage_order_invalid")
    return {
        KIND_FIELD: AGENT_CLEANUP_KIND,
        STATUS_FIELD: CLEANUP_PASS,
        CAPTURED_AT_FIELD: captured_at.isoformat(),
        EXECUTION_HOST_FIELD: connect[EXECUTION_HOST_FIELD],
        SKILL_DIGEST_FIELD: digest,
        TESTED_REVISION_FIELD: connect[TESTED_REVISION_FIELD],
        RUN_ID_FIELD: run_id,
        ORIGINAL_OWNERSHIP_FIELD: dict(ownership),
        AFTER_FIELD: dict(after_observation),
        REPEAT_FIELD: dict(repeat_observation),
    }


def attach_record(
    repeat_receipt: Mapping[str, Any], record: Mapping[str, Any]
) -> dict[str, object]:
    """Return the delete NO_OP receipt with the record under ``smoke_cleanup``.

    :param repeat_receipt: Delete NO_OP receipt of the same run.
    :param record: Record built by ``cleanup_record``.
    :returns: Copy of the receipt carrying the record.
    :raises AgentCleanupError: The receipt already carries a record or belongs to
        another run.
    """
    if SMOKE_CLEANUP_FIELD in repeat_receipt:
        raise AgentCleanupError("agent_cleanup_record_present")
    if repeat_receipt[FILTERS_KEY][RUN_ID_FIELD] != record[RUN_ID_FIELD]:
        raise AgentCleanupError("agent_cleanup_run_mismatch")
    return {**repeat_receipt, SMOKE_CLEANUP_FIELD: dict(record)}
