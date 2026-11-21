"""Join the five native observations required for Agent lifecycle acceptance."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from itertools import pairwise
from typing import Optional
from urllib.parse import urlsplit

from lib.python.core.gitlab.cluster_agent.recovery import (
    CREDENTIAL,
    CREDENTIAL_ID,
    CREDENTIAL_RESOURCE,
    CREDENTIAL_RESOURCE_NAME,
    CREDENTIAL_RESOURCE_UID,
    PREEXISTING_CREDENTIAL_IDS,
)
from lib.python.core.gitlab.cluster_agent.terms import (
    ABSENT,
    AGENT_RUNTIME_KINDS,
    CONNECT,
    DELETE,
    PRESENT,
    VERIFY,
)
from lib.python.core.gitlab.job_read import gitlab_job_reference
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    json_object,
    json_objects,
    json_string,
)
from lib.python.core.provenance import FUNCTIONAL_DIGEST_KEY
from lib.python.core.schema import validate_specification
from lib.python.sys.status import APPLIED, NO_OP, PASS, VERIFIED

AGENT_KIND = "gitlab_agent"
RESULT_SCHEMA = "results/gitlab-agent.schema.json"
AgentOperationKey = tuple[str, str, Optional[str]]
AGENT_OPERATION_KEYS: tuple[AgentOperationKey, ...] = (
    (AGENT_KIND, CONNECT, APPLIED),
    (AGENT_KIND, CONNECT, NO_OP),
    (AGENT_KIND, VERIFY, None),
    (AGENT_KIND, DELETE, APPLIED),
    (AGENT_KIND, DELETE, NO_OP),
)

CREDENTIAL_IDS = "registration_credential_ids"
CREDENTIAL_HTTP_STATUS = "registration_credential_http_status"

_INSTALLATION_FIELDS = (
    "namespace",
    "namespace_uid",
    "release",
    "release_revision",
    "release_storage_uid",
    "chart_version",
    "chart_sha256",
    "app_version",
    "image_digest",
)
_SNAPSHOT_FIELDS = (
    "project_id",
    "agent_id",
    "agent_name",
    *_INSTALLATION_FIELDS[:5],
)
_SKILL_FIELDS = ("algorithm", "digest", "file_count", FUNCTIONAL_DIGEST_KEY)
_CANDIDATE_FIELDS = ("tested_revision", "execution_host", "target")


def agent_operation_key(receipt: JSONObject) -> Optional[AgentOperationKey]:
    """Normalize only schema-recognized Agent deliveries for the required policy.

    :param receipt: Untrusted operation envelope, before full validation.
    :returns: Required operation key, or None if the shape or action is invalid.
    """
    if receipt.get("kind") != AGENT_KIND:
        return None
    try:
        (record,) = json_objects(receipt.get("records"))
        operation = json_string(receipt.get("operation"))
        action = json_string(record.get("action"))
    except (TypeError, ValueError):
        return None
    if operation == VERIFY and action != VERIFIED:
        return None
    key = (AGENT_KIND, operation, None if operation == VERIFY else action)
    return key if key in AGENT_OPERATION_KEYS else None


def _object(document: JSONObject, key: str) -> JSONObject:
    return json_object(document.get(key))


def _record(receipt: JSONObject) -> JSONObject:
    (row,) = json_objects(receipt.get("records"))
    return row


def _time(document: JSONObject, key: str = "captured_at") -> datetime:
    timestamp = datetime.fromisoformat(json_string(document.get(key)))
    if timestamp.utcoffset() is None:
        raise ValueError("agent_evidence_timestamp_timezone_missing")
    return timestamp


def _match(first: JSONObject, second: JSONObject, keys: Sequence[str]) -> bool:
    return all(first.get(key) == second.get(key) for key in keys)


def _resource_index(
    document: JSONObject,
) -> dict[tuple[str, str, str], JSONObject]:
    result: dict[tuple[str, str, str], JSONObject] = {}
    for resource in json_objects(document.get("resources")):
        identity = (
            json_string(resource.get("kind")),
            json_string(resource.get("name")),
            json_string(resource.get("namespace")),
        )
        if identity in result:
            raise ValueError("agent_evidence_duplicate_resource")
        result[identity] = resource
    return result


def _resource_identity(
    resource: JSONObject,
) -> tuple[JSONValue, JSONValue, JSONValue]:
    return resource.get("api_version"), resource.get("uid"), resource.get("created")


def _resource_identities(
    document: JSONObject,
) -> dict[tuple[str, str, str], tuple[JSONValue, JSONValue, JSONValue]]:
    return {
        identity: _resource_identity(resource)
        for identity, resource in _resource_index(document).items()
    }


def _same_owner(first: JSONObject, second: JSONObject) -> bool:
    fields = tuple(key for key in first if key not in ("state", "resources"))
    return _match(first, second, fields) and (
        _resource_identities(first) == _resource_identities(second)
    )


def _resolved(snapshot: JSONObject, owner: JSONObject) -> JSONObject:
    # A connected snapshot writes ``resources: null`` for rows equal to the owner's.
    if snapshot.get("resources") is None:
        return {**snapshot, "resources": owner.get("resources")}
    return snapshot


def _connected_snapshot(
    snapshot: JSONObject,
    owner: JSONObject,
    *,
    absent_kinds: frozenset[str] = frozenset(),
) -> bool:
    snapshot = _resolved(snapshot, owner)
    return (
        _match(snapshot, owner, _SNAPSHOT_FIELDS)
        and snapshot.get("agent_http_status") == 200
        and snapshot.get(CREDENTIAL_HTTP_STATUS) == 200
        and snapshot.get(CREDENTIAL_IDS) == [owner.get(CREDENTIAL_ID)]
        and _resource_identities(snapshot) == _resource_identities(owner)
        and all(
            item.get("state") == PRESENT
            or (item.get("kind") in absent_kinds and item.get("state") == ABSENT)
            for item in _resource_index(snapshot).values()
        )
    )


def verified_agent_owner(owner: JSONObject) -> bool:
    """Check that scalar native IDs agree with the exact retained resource rows.

    :param owner: Schema-validated Agent installation ownership record.
    :returns: Whether every required marker and resource proves its retained identity.
    :raises TypeError: If required object or scope fields are malformed.
    :raises ValueError: If duplicate resource identities make ownership ambiguous.
    """  # noqa: DOC502,DOC503 - Shared native shape readers propagate failures.
    created = _object(owner, "created")
    if (
        any(
            created.get(key) is not True
            for key in (
                "agent",
                CREDENTIAL,
                CREDENTIAL_RESOURCE,
                "release",
                "ownership_record",
            )
        )
        or owner.get(PREEXISTING_CREDENTIAL_IDS) != []
    ):
        return False
    namespace = json_string(owner.get("namespace"))
    identities = _resource_index(owner)
    markers = (
        ("Namespace", namespace, "", "namespace_uid", created.get("namespace")),
        (
            "Secret",
            json_string(owner.get(CREDENTIAL_RESOURCE_NAME)),
            namespace,
            CREDENTIAL_RESOURCE_UID,
            True,
        ),
        (
            "ConfigMap",
            json_string(owner.get("ownership_record_name")),
            namespace,
            "ownership_record_uid",
            True,
        ),
        (
            "Secret",
            json_string(owner.get("release_storage_name")),
            namespace,
            "release_storage_uid",
            True,
        ),
    )
    if len({(kind, name, scope) for kind, name, scope, _, _ in markers}) != len(
        markers
    ):
        return False
    for kind, name, scope, uid_key, owned in markers:
        resource = identities.get((kind, name, scope))
        if (
            resource is None
            or resource.get("uid") != owner.get(uid_key)
            or resource.get("created") is not owned
        ):
            return False
    return all(
        item.get("uid") is not None
        and item.get("state") == PRESENT
        and type(item.get("created")) is bool
        for item in identities.values()
    )


def _connection_verified(row: JSONObject, owner: JSONObject, when: datetime) -> bool:
    snapshot = _object(row, "after")
    installation = _object(row, "installation")
    connection = _object(row, "connection")
    prerequisites = _object(row, "prerequisites")
    installed = _resource_index(owner)
    image = json_string(installation.get("image_reference"))
    digest = json_string(installation.get("image_digest"))
    if (
        not _connected_snapshot(snapshot, owner)
        or not _match(installation, owner, _INSTALLATION_FIELDS)
        or not image.endswith(f"@{digest}")
        or prerequisites.get("cluster_uid") != owner.get("cluster_uid")
        or connection.get("state") != "CONNECTED"
        or connection.get("complete") is not True
    ):
        return False
    ids: set[str] = set()
    for item in json_objects(connection.get("items")):
        identifier = json_string(item.get("connection_id"))
        namespace = json_string(item.get("pod_namespace"))
        pod = installed.get(("Pod", json_string(item.get("pod_name")), namespace))
        if (
            identifier in ids
            or not _match(item, owner, ("agent_id", "agent_name"))
            or namespace != owner.get("namespace")
            or pod is None
            or pod.get("uid") != item.get("pod_uid")
            or pod.get("created") is not True
            or json_string(item.get("agent_version")).removeprefix("v")
            != json_string(installation.get("app_version")).removeprefix("v")
            or _time(item, "connected_at") > when
        ):
            return False
        ids.add(identifier)
    observed = snapshot.get("connection_ids")
    return bool(ids) and isinstance(observed, list) and ids == set(observed)


def _ci_proof_verified(
    receipt: JSONObject, record: JSONObject, owner: JSONObject
) -> bool:
    ci_access = _object(record, "ci_access")
    proof = _object(ci_access, "proof")
    skill = _object(receipt, "skill")
    filters = _object(receipt, "filters")
    host = urlsplit(json_string(owner.get("origin"))).netloc.lower()
    native_job = gitlab_job_reference(json_string(ci_access.get("job_url")), host)
    requested_job = gitlab_job_reference(json_string(filters.get("job_url")), host)
    return (
        ci_access.get("state") == VERIFIED
        and proof.get("status") == PASS
        and native_job == requested_job
        and native_job == (owner.get("project_path"), str(proof.get("job_id")))
        and _match(
            proof,
            owner,
            (
                "run_id",
                "project_id",
                "agent_id",
                "cluster_uid",
                "namespace",
                "namespace_uid",
            ),
        )
        and proof.get("agent_context")
        == f"{owner.get('project_path')}:{owner.get('agent_name')}"
        and proof.get("expected_candidate")
        == {
            "tested_revision": receipt.get("tested_revision"),
            FUNCTIONAL_DIGEST_KEY: skill.get(FUNCTIONAL_DIGEST_KEY),
        }
        and _time(proof) <= _time(receipt)
    )


def _delete_noop(row: JSONObject) -> bool:
    if (
        row.get("ownership") is not None
        or _object(row, "absence").get("mode") != "LOGICAL_SCOPE"
    ):
        return False
    for side in ("before", "after"):
        snapshot = _object(row, side)
        if (
            any(
                snapshot.get(key) is not None
                for key in (
                    "agent_id",
                    "agent_http_status",
                    CREDENTIAL_HTTP_STATUS,
                    "release_revision",
                    "release_storage_uid",
                )
            )
            or snapshot.get(CREDENTIAL_IDS) != []
            or snapshot.get("connection_ids") != []
            or any(
                resource.get("created") is True
                for resource in _resource_index(snapshot).values()
            )
        ):
            return False
    return True


def _delete_applied(row: JSONObject, owner: JSONObject) -> bool:
    before = _object(row, "before")
    after = _object(row, "after")
    prior = _resource_index(owner)
    observed = _resource_index(after)
    if (
        not _connected_snapshot(before, owner, absent_kinds=AGENT_RUNTIME_KINDS)
        or not _match(after, owner, ("project_id", "agent_id", "agent_name"))
        or after.get("agent_http_status") != 404
        or after.get(CREDENTIAL_HTTP_STATUS) != 404
        or after.get(CREDENTIAL_IDS) != []
        or after.get("connection_ids") != []
        or observed.keys() != prior.keys()
    ):
        return False
    for identity, resource in prior.items():
        final = observed[identity]
        fields = ("api_version", "uid", "created")
        if not _match(final, resource, fields):
            return False
        if final.get("state") != (ABSENT if resource.get("created") else PRESENT):
            return False
    return True


def check_agent_receipt(receipt: JSONObject) -> tuple[str, ...]:
    """Validate one Agent receipt before it can contribute an operation key.

    :param receipt: Full installed candidate receipt.
    :returns: Empty findings on success or a stable Agent evidence failure.
    """
    validate_specification(receipt, RESULT_SCHEMA)
    key = agent_operation_key(receipt)
    if receipt.get("status") != PASS or key is None:
        return ("agent_receipt_not_qualified",)
    try:
        row = _record(receipt)
        filters = _object(receipt, "filters")
        cleanup = _object(receipt, "cleanup")
        revision = _object(_object(receipt, "skill"), "revision")
        if (
            filters.get("operation") != key[1]
            or row.get("verified") is not True
            or receipt.get("mutated") is not (key[2] == APPLIED)
            or revision.get("verified") is not True
            or revision.get("value") != receipt.get("tested_revision")
            or cleanup.get("status") != PASS
            or cleanup.get("failures") != []
        ):
            return ("agent_receipt_envelope_mismatch",)
        when = _time(receipt)
        if key == AGENT_OPERATION_KEYS[-1]:
            if not _delete_noop(row):
                return ("agent_delete_logical_absence_unverified",)
            return ()
        owner = _object(row, "ownership")
        if not verified_agent_owner(owner) or json_string(receipt.get("target")).rstrip(
            "/"
        ) != json_string(owner.get("origin")).rstrip("/"):
            return ("agent_ownership_unverified",)
        if key[1] == DELETE:
            if not _delete_applied(row, owner):
                return ("agent_delete_identity_unverified",)
            return ()
        if not _connection_verified(row, owner, when):
            return ("agent_connection_unverified",)
        before = _object(row, "before")
        if key == AGENT_OPERATION_KEYS[0] and (
            before.get("agent_id") is not None
            or before.get("agent_http_status") == 200
            or before.get(CREDENTIAL_IDS) != []
            or before.get("release_storage_uid") is not None
            or before.get("connection_ids") != []
        ):
            return ("agent_connect_preexisting_installation",)
        if key == AGENT_OPERATION_KEYS[1] and not _connected_snapshot(before, owner):
            return ("agent_connect_noop_identity_changed",)
        if key[1] == VERIFY and not _ci_proof_verified(receipt, row, owner):
            return ("agent_ci_proof_identity_mismatch",)
    except (KeyError, TypeError, ValueError):
        return ("agent_receipt_cross_field_shape_invalid",)
    return ()


def _cleanup_observed(observed: JSONObject, owner: JSONObject) -> bool:
    for name, field in (("agent", "agent_id"), (CREDENTIAL, CREDENTIAL_ID)):
        resource = _object(observed, name)
        if (
            resource.get("id") != owner.get(field)
            or resource.get("http_status") != 404
            or resource.get("absent") is not True
            or resource.get("preserved") is not False
        ):
            return False
    previous = _resource_index(owner)
    final = _resource_index(observed)
    if previous.keys() != final.keys():
        return False
    for key, resource in previous.items():
        current = final[key]
        created = resource.get("created") is True
        uid = resource.get("uid")
        if (
            current.get("uid") != uid
            or current.get("absent") is not created
            or current.get("preserved") is not (not created)
            or current.get("observed_uid") != (None if created else uid)
        ):
            return False
    return (
        observed.get("release_absent") is True
        and observed.get("run_resources_absent") is True
    )


def _cleanup_verified(
    ordered: Sequence[JSONObject],
    owner: JSONObject,
    repeat_time: datetime,
    now: datetime,
) -> bool:
    supplements = [
        receipt["smoke_cleanup"] for receipt in ordered if "smoke_cleanup" in receipt
    ]
    if len(supplements) != 1:
        return False
    cleanup = json_object(supplements[0])
    first = ordered[0]
    skill = _object(first, "skill")
    return (
        cleanup.get("run_id") == owner.get("run_id")
        and cleanup.get("skill_digest") == skill.get("digest")
        and _match(cleanup, first, ("execution_host", "tested_revision"))
        and _same_owner(_object(cleanup, "original_ownership"), owner)
        and repeat_time <= _time(cleanup) <= now
        and all(
            _cleanup_observed(_object(cleanup, stage), owner)
            for stage in ("after", "repeat")
        )
    )


def check_agent_series(
    receipts: Sequence[JSONObject], *, now: datetime
) -> tuple[str, ...]:
    """Require a complete Agent lifecycle from individually validated receipts.

    The caller owns schema checks, redaction, freshness, and acceptance-policy
    dispatch. This join compares only observations already accepted individually.

    :param receipts: Agent receipts that passed the shared individual validator.
    :param now: Gate reference time for the independent cleanup observation.
    :returns: Empty findings for one coherent series, otherwise stable failure tokens.
    """
    by_key: dict[AgentOperationKey, JSONObject] = {}
    for receipt in receipts:
        key = agent_operation_key(receipt)
        if key is None or key in by_key or receipt.get("status") != PASS:
            return ("agent_series_duplicate_or_invalid_operation",)
        by_key[key] = receipt
    if set(by_key) != set(AGENT_OPERATION_KEYS):
        return ("agent_series_required_operations_missing",)
    ordered = [by_key[key] for key in AGENT_OPERATION_KEYS]
    try:
        first, _, verify, delete, repeat = ordered
        times = [_time(receipt) for receipt in ordered]
        if any(current >= later for current, later in pairwise(times)):
            return ("agent_series_capture_order_invalid",)
        skill = _object(first, "skill")
        if any(
            not _match(first, receipt, _CANDIDATE_FIELDS)
            or not _match(skill, _object(receipt, "skill"), _SKILL_FIELDS)
            for receipt in ordered[1:]
        ):
            return ("agent_series_candidate_mismatch",)
        owner = _object(_record(first), "ownership")
        if any(
            not _same_owner(owner, _object(_record(receipt), "ownership"))
            for receipt in ordered[1:4]
        ):
            return ("agent_series_installation_identity_mismatch",)
        first_installation = _object(_record(first), "installation")
        if any(
            not _match(
                first_installation,
                _object(_record(receipt), "installation"),
                ("image_reference",),
            )
            for receipt in ordered[1:3]
        ):
            return ("agent_series_installation_image_mismatch",)
        if any(
            _object(_record(receipt), "prerequisites").get("cluster_uid")
            != owner.get("cluster_uid")
            for receipt in ordered
        ):
            return ("agent_series_cluster_identity_mismatch",)
        selector_fields = (
            "project",
            "project_name",
            "agent_name",
            "run_id",
            "namespace",
        )
        delete_filters = _object(delete, "filters")
        if not _match(
            delete_filters, _object(repeat, "filters"), selector_fields
        ) or not _match(delete_filters, owner, ("agent_name", "run_id")):
            return ("agent_series_delete_selector_mismatch",)
        proof = _object(_object(_record(verify), "ci_access"), "proof")
        if not times[0] <= _time(proof) <= times[2]:
            return ("agent_series_proof_order_invalid",)
        if not _cleanup_verified(ordered, owner, times[-1], now):
            return ("agent_series_cleanup_unverified",)
    except (KeyError, TypeError, ValueError):
        return ("agent_series_shape_invalid",)
    return ()
