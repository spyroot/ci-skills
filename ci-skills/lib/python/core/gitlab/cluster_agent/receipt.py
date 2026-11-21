"""Project native GitLab Agent observations into the approved result schema."""

from __future__ import annotations

from http import HTTPStatus
from typing import Final

from lib.python.core.catalog import ROUTED_RESULT_SCHEMA_VERSION
from lib.python.core.gitlab.cluster_agent.absence import LogicalAgentAbsence
from lib.python.core.gitlab.cluster_agent.authority import AgentAuthorities
from lib.python.core.gitlab.cluster_agent.ci_access import CI_ACCESS_ARTIFACT
from lib.python.core.gitlab.cluster_agent.connection import AgentConnectivity
from lib.python.core.gitlab.cluster_agent.delete import AgentCleanup
from lib.python.core.gitlab.cluster_agent.evidence import (
    AGENT_KIND,
    CREDENTIAL_HTTP_STATUS,
    CREDENTIAL_IDS,
    RESULT_SCHEMA,
)
from lib.python.core.gitlab.cluster_agent.plan import FreshConnectPlan
from lib.python.core.gitlab.cluster_agent.recovery import (
    CREDENTIAL,
    CREDENTIAL_ID,
    CREDENTIAL_NAME,
    CREDENTIAL_RESOURCE_NAME,
    CREDENTIAL_RESOURCE_UID,
)
from lib.python.core.gitlab.cluster_agent.secret import TOKEN_KEY
from lib.python.core.gitlab.cluster_agent.selection import IMAGE_TAG, ConnectSelection
from lib.python.core.gitlab.cluster_agent.terms import (
    ABSENT,
    CONNECT,
    DELETE,
    PRESENT,
    RECORDED,
    VERIFY,
)
from lib.python.core.gitlab.cluster_agent.workflow import AgentOutcome
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json_object,
    json_integer,
    json_object,
    json_objects,
    json_string,
)
from lib.python.core.kubernetes.runner_scope import SECRET_KIND
from lib.python.core.report import report
from lib.python.core.schema import validate_specification
from lib.python.sys.status import APPLIED, NO_OP, PASS, PLANNED, UNKNOWN, VERIFIED

_APPLIED_OPERATIONS: Final = frozenset((CONNECT, DELETE))
_INSTALLATION_FIELDS: Final = (
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
_NOT_VERIFIED: Final = "NOT_VERIFIED"
_LOGICAL_SCOPE: Final = "LOGICAL_SCOPE"
_NOT_CHECKED: Final = "NOT_CHECKED"
_TRACKED_IDENTITIES: Final = "TRACKED_IDENTITIES"
_CONNECTED: Final = "CONNECTED"
_RESULT_MISMATCH: Final = "agent_receipt_native_observation_mismatch"
_REPORT_KIND: Final = "GitLabAgent"


def _snapshot(selection: ConnectSelection) -> dict[str, JSONValue]:
    return {
        "project_id": selection.project.id,
        "agent_id": None,
        "agent_name": selection.agent_name,
        "agent_http_status": None,
        CREDENTIAL_IDS: [],
        CREDENTIAL_HTTP_STATUS: None,
        "namespace": selection.scope.namespace,
        "namespace_uid": None,
        "release": selection.release,
        "release_revision": None,
        "release_storage_uid": None,
        "resources": [],
        "connection_ids": [],
    }


def _absence(mode: str) -> JSONObject:
    verified = mode != _NOT_CHECKED
    return {
        "mode": mode,
        "verified": verified,
        "agent_absent": True if verified else None,
        "release_absent": True if verified else None,
        "run_resources_absent": True if verified else None,
        "exact_ids_checked": mode == _TRACKED_IDENTITIES,
    }


def _owner_selection(
    authorities: AgentAuthorities, selection: ConnectSelection, owner: JSONObject
) -> None:
    expected = {
        "project_id": selection.project.id,
        "project_path": selection.project.path,
        "agent_name": selection.agent_name,
        "run_id": selection.scope.run_id,
        "namespace": selection.scope.namespace,
        "release": selection.release,
        "cluster_uid": authorities.cluster_uid,
        "origin": authorities.gitlab.session.origin.rstrip("/"),
    }
    if any(owner.get(field) != value for field, value in expected.items()):
        raise ValueError(_RESULT_MISMATCH)


def _recorded_snapshot(
    selection: ConnectSelection, owner: JSONObject
) -> dict[str, JSONValue]:
    return {
        **_snapshot(selection),
        **{field: owner[field] for field in _INSTALLATION_FIELDS[:5]},
        "agent_id": owner["agent_id"],
        "resources": [
            {**item, "state": UNKNOWN} for item in json_objects(owner["resources"])
        ],
    }


def _connected_record(
    authorities: AgentAuthorities,
    outcome: AgentOutcome,
    connected: AgentConnectivity,
    operation: str,
    row: dict[str, JSONValue],
) -> None:
    selection = outcome.selection
    owner = connected.record.document
    _owner_selection(authorities, selection, owner)
    agent_id = json_integer(owner["agent_id"])
    credential_id = json_integer(owner[CREDENTIAL_ID])
    if not connected.connections or (operation == VERIFY) != (
        outcome.ci_access is not None
    ):
        raise ValueError(_RESULT_MISMATCH)
    active = [
        {
            "agent_id": agent_id,
            "agent_name": selection.agent_name,
            "connection_id": item.connection.connection_id,
            "connected_at": item.connection.connected_at,
            "pod_name": item.connection.pod_name,
            "pod_namespace": item.connection.pod_namespace,
            "pod_uid": item.pod.uid,
            "agent_version": item.connection.agent_version,
            "agent_commit": item.connection.agent_commit,
        }
        for item in connected.connections
    ]
    resources = list(json_objects(owner["resources"]))
    after: JSONObject = {
        **_recorded_snapshot(selection, owner),
        "agent_http_status": HTTPStatus.OK.value,
        CREDENTIAL_IDS: [credential_id],
        CREDENTIAL_HTTP_STATUS: HTTPStatus.OK.value,
        # The connected snapshot's rows equal ownership.resources; null says so once.
        "resources": None,
        "connection_ids": [item["connection_id"] for item in active],
    }
    before = after
    if outcome.action == APPLIED:
        if outcome.before is not None:
            raise ValueError(_RESULT_MISMATCH)
        fresh = _snapshot(selection)
        if not selection.scope.owns_namespace:
            (borrowed,) = tuple(
                item for item in resources if item["kind"] == "Namespace"
            )
            if (
                borrowed.get("created") is not False
                or borrowed.get("uid") != owner["namespace_uid"]
            ):
                raise ValueError(_RESULT_MISMATCH)
            fresh.update(namespace_uid=borrowed["uid"], resources=[borrowed])
        before = fresh
    elif outcome.before != connected.record:
        raise ValueError(_RESULT_MISMATCH)
    row.update(
        before=before,
        after=after,
        ownership=owner,
        installation={
            **{field: owner[field] for field in _INSTALLATION_FIELDS},
            "image_reference": f"{IMAGE_TAG}@{json_string(owner['image_digest'])}",
            "observed_image_ids": sorted(
                {item.pod.image_digest for item in connected.connections}
            ),
        },
        connection={"state": _CONNECTED, "complete": True, "items": active},
    )


def _deleted_record(
    authorities: AgentAuthorities,
    selection: ConnectSelection,
    owner: JSONObject,
    cleanup: AgentCleanup,
    row: dict[str, JSONValue],
) -> None:
    """Record a confirmed tracked delete from its before read, after read and absence proof.

    :param authorities: Selected providers and the cluster UID read for this invocation.
    :param selection: Run, Agent and release identity the delete used.
    :param owner: Ownership record that authorized the delete.
    :param cleanup: Native state read before the writes, final resource rows and
        independent logical absence.
    :param row: Record to complete in place.
    :raises ValueError: The cleanup does not belong to this owner, project, release or
        cluster.
    """
    _owner_selection(authorities, selection, owner)
    absence = cleanup.absence
    if (
        cleanup.ownership.document != owner
        or absence.project != selection.project
        or absence.release != selection.release
        or absence.cluster_uid != authorities.cluster_uid
    ):
        raise ValueError(_RESULT_MISMATCH)
    credential_id = json_integer(owner[CREDENTIAL_ID])
    owned = list(json_objects(owner["resources"]))
    observed = [
        {**item, "state": PRESENT if item["uid"] in cleanup.present else ABSENT}
        for item in owned
    ]
    before: dict[str, JSONValue] = {
        **_recorded_snapshot(selection, owner),
        "agent_http_status": (
            HTTPStatus.OK if cleanup.agent_present else HTTPStatus.NOT_FOUND
        ).value,
        CREDENTIAL_IDS: [credential_id] if cleanup.token_present else [],
        CREDENTIAL_HTTP_STATUS: (
            HTTPStatus.OK if cleanup.token_present else HTTPStatus.NOT_FOUND
        ).value,
        # Rows equal to ownership.resources are written once, as null.
        "resources": None if observed == owned else observed,
        "connection_ids": [],
    }
    after: dict[str, JSONValue] = {
        **_snapshot(selection),
        "agent_id": owner["agent_id"],
        "agent_http_status": HTTPStatus.NOT_FOUND.value,
        CREDENTIAL_IDS: [],
        CREDENTIAL_HTTP_STATUS: HTTPStatus.NOT_FOUND.value,
        "resources": list(cleanup.resources),
        "connection_ids": [],
    }
    row.update(
        before=before,
        after=after,
        ownership=owner,
        installation={
            **{field: owner[field] for field in _INSTALLATION_FIELDS},
            "image_reference": f"{IMAGE_TAG}@{json_string(owner['image_digest'])}",
            "observed_image_ids": [],
        },
        absence=_absence(_TRACKED_IDENTITIES),
    )


def _logical_record(
    authorities: AgentAuthorities,
    selection: ConnectSelection,
    absence: LogicalAgentAbsence,
    row: dict[str, JSONValue],
) -> None:
    if (
        absence.project != selection.project
        or absence.release != selection.release
        or absence.cluster_uid != authorities.cluster_uid
    ):
        raise ValueError(_RESULT_MISMATCH)
    row.update(absence=_absence(_LOGICAL_SCOPE))


def _credentials(
    authorities: AgentAuthorities, row: dict[str, JSONValue], operation: str
) -> JSONObject:
    """Trace the three credentials of this invocation without any credential value.

    :param authorities: Selected providers; the GitLab client holds the request trace.
    :param row: Completed record whose ownership and connection are final.
    :param operation: ``connect``, ``verify`` or ``delete``.
    :returns: GitLab requests sent with the selected GitLab token, Kubernetes objects
        created with the selected kubeconfig, and the Agent token's state, ID, Secret
        and consumers. The token state is ``PLANNED`` for a fresh connect plan,
        ``ABSENT`` when no ownership record exists or a confirmed delete read the
        token absent, ``PRESENT`` when this invocation
        read an active connection, and ``RECORDED`` when only the ownership record
        names the token.
    """
    action = row["action"]
    owner = row["ownership"]
    owned = [] if owner is None else json_objects(json_object(owner)["resources"])
    created_objects: list[JSONValue] = [
        {
            "kind": item["kind"],
            "name": item["name"],
            "namespace": item["namespace"],
            "uid": item["uid"],
        }
        for item in owned
        if operation == CONNECT and action == APPLIED and item.get("created") is True
    ]
    agent: JSONObject = {
        "state": PLANNED if operation == CONNECT and action == PLANNED else ABSENT,
        "id": None,
        "name": None,
        "created": False,
        "storage": None,
        "consumers": [],
    }
    if owner is not None:
        record = json_object(owner)
        connection = json_object(row["connection"])
        if operation == DELETE and action == APPLIED:
            state = ABSENT
        elif connection["state"] == _CONNECTED:
            state = PRESENT
        else:
            state = RECORDED
        agent = {
            "state": state,
            "id": json_integer(record[CREDENTIAL_ID]),
            "name": json_string(record[CREDENTIAL_NAME]),
            "created": operation == CONNECT
            and action == APPLIED
            and json_object(record["created"]).get(CREDENTIAL) is True,
            "storage": {
                "kind": SECRET_KIND,
                "namespace": json_string(record["namespace"]),
                "name": json_string(record[CREDENTIAL_RESOURCE_NAME]),
                "uid": record[CREDENTIAL_RESOURCE_UID],
                "key": TOKEN_KEY,
            },
            "consumers": [
                {
                    "connection_id": item["connection_id"],
                    "pod_name": item["pod_name"],
                    "pod_uid": item["pod_uid"],
                }
                for item in json_objects(connection["items"])
            ],
        }
    return {
        "gitlab": {
            "calls": [
                {"method": call.method, "endpoint": call.endpoint}
                for call in authorities.gitlab.api.requests
            ],
        },
        "kubernetes": {"created_objects": created_objects},
        "agent_credential": agent,
    }


def _record(
    authorities: AgentAuthorities, outcome: AgentOutcome, operation: str
) -> JSONObject:
    row = dict(_record_fields(authorities, outcome, operation))
    row["credentials"] = _credentials(authorities, row, operation)
    return row


def _record_fields(
    authorities: AgentAuthorities, outcome: AgentOutcome, operation: str
) -> JSONObject:
    selection = outcome.selection
    row: dict[str, JSONValue] = {
        "callback": _REPORT_KIND,
        "action": outcome.action,
        "verified": outcome.action != PLANNED,
        "before": _snapshot(selection),
        "after": _snapshot(selection),
        "ownership": None,
        "installation": {
            **dict.fromkeys(_INSTALLATION_FIELDS),
            "image_reference": None,
            "observed_image_ids": [],
        },
        "connection": {"state": UNKNOWN, "complete": False, "items": []},
        "ci_access": {
            "state": _NOT_VERIFIED,
            "job_url": None,
            "artifact_path": CI_ACCESS_ARTIFACT,
            "artifact_sha256": None,
            "proof": None,
        },
        "absence": _absence(_NOT_CHECKED),
        "prerequisites": {
            "gitlab_version": authorities.kas.gitlab_version,
            "kas_enabled": True,
            "kas_version": authorities.kas.version,
            "kas_address": authorities.kas.address,
            "cluster_uid": authorities.cluster_uid,
            "cluster_access": VERIFIED,
            "egress": "UNPROVEN",
        },
    }
    observation = outcome.observation
    if isinstance(observation, AgentConnectivity) and (
        (operation == CONNECT and outcome.action in (PLANNED, APPLIED, NO_OP))
        or (operation == VERIFY and outcome.action == VERIFIED)
    ):
        _connected_record(authorities, outcome, observation, operation, row)
        row["prerequisites"] = {
            **json_object(row["prerequisites"]),
            "egress": _CONNECTED,
        }
        proof = outcome.ci_access
        if proof is not None:
            row["ci_access"] = {
                "state": VERIFIED,
                "job_url": proof.job_url,
                "artifact_path": CI_ACCESS_ARTIFACT,
                "artifact_sha256": proof.artifact_sha256,
                "proof": proof.proof,
            }
        return row
    if outcome.ci_access is not None:
        raise ValueError(_RESULT_MISMATCH)
    if (
        operation == CONNECT
        and outcome.action == PLANNED
        and isinstance(observation, FreshConnectPlan)
        and outcome.before is None
        and observation.digest == outcome.digest
    ):
        snapshot = {
            **_snapshot(selection),
            "namespace_uid": observation.namespace_uid,
        }
        row.update(before=snapshot, after=snapshot, absence=_absence(_LOGICAL_SCOPE))
    elif (
        operation == DELETE
        and outcome.action in (PLANNED, NO_OP)
        and isinstance(observation, LogicalAgentAbsence)
        and outcome.before is None
    ):
        _logical_record(authorities, selection, observation, row)
    elif (
        operation == DELETE
        and outcome.action == APPLIED
        and isinstance(observation, AgentCleanup)
        and outcome.before is not None
    ):
        _deleted_record(
            authorities, selection, outcome.before.document, observation, row
        )
    elif (
        operation == DELETE
        and outcome.action == PLANNED
        and observation is None
        and outcome.before is not None
    ):
        owner = outcome.before.document
        _owner_selection(authorities, selection, owner)
        snapshot = _recorded_snapshot(selection, owner)
        row.update(before=snapshot, after=snapshot, ownership=owner)
    else:
        raise ValueError(_RESULT_MISMATCH)
    return row


def agent_result(
    authorities: AgentAuthorities, intent: JSONObject, outcome: AgentOutcome
) -> dict[str, JSONValue]:
    """Build and validate the live report from observations made by this invocation.

    :param authorities: Selected verified providers and installed skill provenance.
    :param intent: Normalized request bound to the same operation and target.
    :param outcome: Completed native observations, excluding all credentials.
    :returns: Schema-validated result for the shared renderer and portable writer.
    :raises ValueError: Required native observations or result fields disagree.
    """  # noqa: DOC503 - Shared evidence readers propagate shape errors.
    operation = json_string(intent["operation"])
    selection = outcome.selection
    session = authorities.gitlab.session
    if (
        not authorities.cluster_uid
        or selection.project != authorities.project
        or intent["project"] != session.target_reference
        or intent.get("run_id") not in (None, selection.scope.run_id)
        or intent.get("agent_name") not in (None, selection.agent_name)
        or intent.get("namespace") not in (None, selection.scope.namespace)
        or (outcome.action == APPLIED and operation not in _APPLIED_OPERATIONS)
        or (operation == VERIFY) != (outcome.digest is None)
    ):
        raise ValueError(_RESULT_MISMATCH)
    filters: JSONObject = {**intent, "run_id": selection.scope.run_id}
    revision = json_object(session.skill["revision"])
    if revision.get("verified") is not True:
        raise ValueError("agent_receipt_candidate_unverified")
    result = dict(
        report(
            AGENT_KIND,
            session.origin,
            filters,
            [_record(authorities, outcome, operation)],
            [],
        )
    )
    result.update(
        schema_version=ROUTED_RESULT_SCHEMA_VERSION,
        status=PLANNED if outcome.action == PLANNED else PASS,
        operation=operation,
        plan_digest=outcome.digest,
        mutated=outcome.action == APPLIED,
        cleanup={"status": PASS, "failures": []},
        skill=copy_json_object(session.skill),
        tested_revision=json_string(revision["value"]),
        execution_host=session.execution_host,
        target_source=session.target_source,
    )
    routing = authorities.kubernetes.target.routing
    if routing is not None:
        result["routing"] = routing.as_dict()
    validate_specification(result, RESULT_SCHEMA)
    return result
