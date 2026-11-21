"""Agent result credential trace: values, record wiring, schema and redaction survival."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from functools import cache
from http import HTTPMethod
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final, Union

import pytest
from conftest import FIXTURE_GITLAB, import_script_module
from jsonschema import Draft202012Validator

ABSENCE = import_script_module("lib.python.core.gitlab.cluster_agent.absence")
CLI_API = import_script_module("lib.python.core.cli_api")
ENDPOINTS = import_script_module("lib.python.core.endpoints")
EVIDENCE = import_script_module("lib.python.core.gitlab.cluster_agent.evidence")
PLAN = import_script_module("lib.python.core.gitlab.cluster_agent.plan")
PORTABLE = import_script_module("lib.python.core.portable")
RECEIPT = import_script_module("lib.python.core.gitlab.cluster_agent.receipt")
RECOVERY = import_script_module("lib.python.core.gitlab.cluster_agent.recovery")
REPORT = import_script_module("lib.python.core.report")
RUNTIME = import_script_module("lib.python.core.runtime")
SCHEMA = import_script_module("lib.python.core.schema")
SECRET = import_script_module("lib.python.core.gitlab.cluster_agent.secret")
SELECTION = import_script_module("lib.python.core.gitlab.cluster_agent.selection")
STATUS = import_script_module("lib.python.sys.status")
WORKFLOW = import_script_module("lib.python.core.gitlab.cluster_agent.workflow")

CONNECT: Final = "connect"
VERIFY: Final = "verify"
DELETE: Final = "delete"
GET: Final = "GET"
POST: Final = "POST"
PROJECT_ID: Final = 42
PROJECT_PATH: Final = "consumer/project"
PROJECT_ROUTE: Final = f"projects/{PROJECT_ID}"
AGENTS_ROUTE: Final = f"{PROJECT_ROUTE}/cluster_agents"
TOKENS_ROUTE: Final = f"{AGENTS_ROUTE}/17/tokens"
GRAPHQL_ROUTE: Final = "graphql"
ROUTE_QUERY: Final = "?page=1"
ROUTE_FRAGMENT: Final = "#notes"
AGENT_NAME: Final = "consumer-agent"
RUN_ID: Final = "run-001"
DIGEST: Final = "a" * 64
TOKEN_ID: Final = 29
TOKEN_NAME: Final = "consumer-agent-gitlab-token"
NAMESPACE: Final = "consumer-0a1b2c"
SECRET_NAME: Final = "consumer-agent-token-0a1b"
SECRET_UID: Final = "11111111-2222-3333-4444-555555555555"
NAMESPACE_UID: Final = "66666666-7777-8888-9999-000000000000"
CLUSTER_UID: Final = "99999999-8888-7777-6666-555555555555"
RELEASE_STORAGE: Final = "sh.helm.release.v1.consumer-agent.v1"
CONNECTION_ID: Final = "4355863225129730728"
POD_NAME: Final = "consumer-agent-v2-abc-xyz"
POD_UID: Final = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
DEPLOYMENT_NAME: Final = "consumer-agent-v2"
DEPLOYMENT_UID: Final = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
GITLAB_VERSION: Final = "17.5.0"
KAS_ADDRESS: Final = "wss://kas.gitlab.example.test"
TOKEN_PLAINTEXT: Final = "unit-agent-token-plaintext"
# Input field that holds TOKEN_PLAINTEXT; the trace must never copy it.
PLANTED_FIELD: Final = "agent_token"
CONNECTED: Final = "CONNECTED"
NAMESPACE_KIND: Final = "Namespace"
SECRET_KIND: Final = "Secret"
DEPLOYMENT_KIND: Final = "Deployment"
OTHER_SECRET_KEY: Final = "ca.crt"
UNKNOWN_FIELD: Final = "value"
TABLE_FIELD: Final = "table"
CREDENTIALS_FIELD: Final = "credentials"
RECORD_DEFINITION: Final = "record"
INSTALLATION_FIELD: Final = "installation"
RESOURCES_FIELD: Final = "resources"
UNKNOWN_PROPERTY: Final = "additionalProperties"
RECEIPT_FILE: Final = "agent-receipt.json"
JSON_MODE: Final = "json"

CALLS: Final = (
    CLI_API.APIRequest(GET, PROJECT_ROUTE),
    CLI_API.APIRequest(GET, "metadata"),
    CLI_API.APIRequest(POST, AGENTS_ROUTE),
    CLI_API.APIRequest(POST, TOKENS_ROUTE),
    CLI_API.APIRequest(POST, GRAPHQL_ROUTE),
)
EXPECTED_CALLS: Final = json.dumps(
    [{"method": item.method, "endpoint": item.endpoint} for item in CALLS]
)
CONSUMER: Final = json.dumps(
    {"connection_id": CONNECTION_ID, "pod_name": POD_NAME, "pod_uid": POD_UID}
)
NOT_CONNECTED: Final = json.dumps(
    {"state": STATUS.UNKNOWN, "complete": False, "items": []}
)

# Each case copies this record; a case changes only the fields it names. The
# resource rows live in ownership.resources, the one place the result keeps them.
# PLANTED_FIELD carries a token value the trace must not copy, on the record, its
# ownership document and its connection item.
RECORD_BASELINE: Final = json.dumps(
    {
        "action": STATUS.APPLIED,
        PLANTED_FIELD: TOKEN_PLAINTEXT,
        "ownership": {
            RECOVERY.CREDENTIAL_ID: TOKEN_ID,
            RECOVERY.CREDENTIAL_NAME: TOKEN_NAME,
            "namespace": NAMESPACE,
            RECOVERY.CREDENTIAL_RESOURCE_NAME: SECRET_NAME,
            RECOVERY.CREDENTIAL_RESOURCE_UID: SECRET_UID,
            "created": {RECOVERY.CREDENTIAL: True},
            PLANTED_FIELD: TOKEN_PLAINTEXT,
            RESOURCES_FIELD: [
                {
                    "kind": NAMESPACE_KIND,
                    "name": NAMESPACE,
                    "namespace": "",
                    "uid": NAMESPACE_UID,
                    "created": True,
                },
                {
                    "kind": SECRET_KIND,
                    "name": SECRET_NAME,
                    "namespace": NAMESPACE,
                    "uid": SECRET_UID,
                    "created": True,
                },
                {
                    "kind": SECRET_KIND,
                    "name": RELEASE_STORAGE,
                    "namespace": NAMESPACE,
                    "uid": None,
                    "created": False,
                },
            ],
        },
        "connection": {
            "state": CONNECTED,
            "complete": True,
            "items": [{**json.loads(CONSUMER), PLANTED_FIELD: TOKEN_PLAINTEXT}],
        },
    }
)

# One valid credentials object. Each schema case copies it and breaks one invariant.
CREDENTIALS_BASELINE: Final = json.dumps(
    {
        "gitlab": {
            "calls": [
                {"method": GET, "endpoint": PROJECT_ROUTE},
                {"method": POST, "endpoint": TOKENS_ROUTE},
            ],
        },
        "kubernetes": {
            "created_objects": [
                {
                    "kind": SECRET_KIND,
                    "name": SECRET_NAME,
                    "namespace": NAMESPACE,
                    "uid": SECRET_UID,
                }
            ],
        },
        "agent_credential": {
            "state": RECOVERY.PRESENT,
            "id": TOKEN_ID,
            "name": TOKEN_NAME,
            "created": True,
            "storage": {
                "kind": SECRET_KIND,
                "namespace": NAMESPACE,
                "name": SECRET_NAME,
                "uid": SECRET_UID,
                "key": SECRET.TOKEN_KEY,
            },
            "consumers": [json.loads(CONSUMER)],
        },
    }
)

JSONPath = tuple[Union[str, int], ...]
JSONRow = dict[str, Any]


def _authorities() -> SimpleNamespace:
    """Build the selected providers that ``_record`` and the trace read.

    :returns: Authorities whose GitLab client sent ``CALLS``, with the KAS metadata
        and cluster UID the record prerequisites copy.
    """
    return SimpleNamespace(
        gitlab=SimpleNamespace(api=SimpleNamespace(requests=CALLS)),
        kas=SimpleNamespace(
            gitlab_version=GITLAB_VERSION, version=GITLAB_VERSION, address=KAS_ADDRESS
        ),
        cluster_uid=CLUSTER_UID,
    )


@cache
def _definition_validator(definition: str) -> Draft202012Validator:
    """Validate against one definition of the shipped Agent result schema.

    :param definition: Name under the result schema's ``$defs``.
    :returns: Validator that resolves sibling schemas through the installed registry.
    """
    schema = SCHEMA.load_schema(EVIDENCE.RESULT_SCHEMA)
    return SCHEMA.specification_validator(EVIDENCE.RESULT_SCHEMA).evolve(
        schema={"$ref": f"{schema['$id']}#/$defs/{definition}"}
    )


def _planned_record(
    operation: str, observe: Callable[[SELECTION.ConnectSelection], object]
) -> dict[str, Any]:
    """Build a planned Agent record through ``_record`` for one selected scope.

    :param operation: ``connect`` or ``delete``.
    :param observe: Builds the plan observation from the selection.
    :returns: Record with its credentials trace, before schema validation.
    """
    selection = SELECTION.select_connect(
        SELECTION.AgentProject(PROJECT_ID, PROJECT_PATH),
        project_name=None,
        agent_name=AGENT_NAME,
        namespace=NAMESPACE,
        run_id=RUN_ID,
    )
    outcome = WORKFLOW.AgentOutcome(
        STATUS.PLANNED, selection, DIGEST, None, observe(selection)
    )
    return dict(RECEIPT._record(_authorities(), outcome, operation))


def _fresh_plan(selection: SELECTION.ConnectSelection) -> object:
    """Observe a fresh connect plan with no borrowed namespace.

    :param selection: Selected Agent scope; a fresh plan does not read it.
    :returns: Fresh connect plan bound to ``DIGEST``.
    """
    return PLAN.FreshConnectPlan(None, DIGEST)


def _logical_absence(selection: SELECTION.ConnectSelection) -> object:
    """Observe an empty logical scope for the selected project and release.

    :param selection: Selected Agent scope whose project and release were read.
    :returns: Logical absence on the selected cluster.
    """
    return ABSENCE.LogicalAgentAbsence(
        selection.project, selection.release, CLUSTER_UID
    )


@pytest.mark.parametrize(
    ("operation", "action", "owner", "connected", "state", "created", "created_kinds"),
    (
        pytest.param(
            CONNECT,
            STATUS.APPLIED,
            True,
            True,
            RECOVERY.PRESENT,
            True,
            (NAMESPACE_KIND, SECRET_KIND),
            id="fresh-apply",
        ),
        pytest.param(
            CONNECT,
            STATUS.NO_OP,
            True,
            True,
            RECOVERY.PRESENT,
            False,
            (),
            id="repeat-no-op",
        ),
        pytest.param(
            CONNECT,
            STATUS.PLANNED,
            True,
            True,
            RECOVERY.PRESENT,
            False,
            (),
            id="repeat-connect-plan",
        ),
        pytest.param(
            CONNECT,
            STATUS.PLANNED,
            False,
            False,
            STATUS.PLANNED,
            False,
            (),
            id="fresh-connect-plan",
        ),
        pytest.param(
            VERIFY,
            STATUS.VERIFIED,
            True,
            True,
            RECOVERY.PRESENT,
            False,
            (),
            id="verify",
        ),
        pytest.param(
            DELETE,
            STATUS.PLANNED,
            True,
            False,
            RECOVERY.RECORDED,
            False,
            (),
            id="tracked-delete-plan",
        ),
        pytest.param(
            DELETE,
            STATUS.PLANNED,
            False,
            False,
            RECOVERY.ABSENT,
            False,
            (),
            id="logical-delete-plan",
        ),
        pytest.param(
            DELETE,
            STATUS.NO_OP,
            False,
            False,
            RECOVERY.ABSENT,
            False,
            (),
            id="logical-delete-no-op",
        ),
        pytest.param(
            CONNECT,
            STATUS.APPLIED,
            False,
            False,
            RECOVERY.ABSENT,
            False,
            (),
            id="applied-without-ownership",
        ),
    ),
)
def test_credentials_trace_names_each_token_use_without_values(
    operation: str,
    action: str,
    owner: bool,
    connected: bool,
    state: str,
    created: bool,
    created_kinds: tuple[str, ...],
) -> None:
    """Report GitLab requests, created objects and the Agent token's Secret per record state.

    :param operation: Agent operation that produced the record.
    :param action: Record action produced by the operation.
    :param owner: Whether the record carries an ownership document.
    :param connected: Whether this invocation read an active connection.
    :param state: Expected Agent token state.
    :param created: Expected ``agent_credential.created``.
    :param created_kinds: Kinds expected in ``kubernetes.created_objects``.
    """
    row = json.loads(RECORD_BASELINE)
    row["action"] = action
    if not connected:
        row["connection"] = json.loads(NOT_CONNECTED)
    if not owner:
        row["ownership"] = None
    baseline = copy.deepcopy(row)

    trace = RECEIPT._credentials(_authorities(), row, operation)

    assert row == baseline
    _definition_validator(CREDENTIALS_FIELD).validate(trace)
    assert trace["gitlab"] == {"calls": json.loads(EXPECTED_CALLS)}
    assert [item["kind"] for item in trace["kubernetes"]["created_objects"]] == list(
        created_kinds
    )
    agent = trace["agent_credential"]
    assert (agent["state"], agent["created"]) == (state, created)
    if owner:
        assert (agent["id"], agent["name"]) == (TOKEN_ID, TOKEN_NAME)
        assert agent["storage"] == {
            "kind": SECRET_KIND,
            "namespace": NAMESPACE,
            "name": SECRET_NAME,
            "uid": SECRET_UID,
            "key": SECRET.TOKEN_KEY,
        }
    else:
        assert (agent["id"], agent["name"], agent["storage"]) == (None, None, None)
    assert agent["consumers"] == ([json.loads(CONSUMER)] if owner and connected else [])
    assert RUNTIME.redact_tree({CREDENTIALS_FIELD: trace}) == {CREDENTIALS_FIELD: trace}
    assert TOKEN_PLAINTEXT in json.dumps(row)
    assert TOKEN_PLAINTEXT not in json.dumps(trace)


@pytest.mark.parametrize(
    ("change", "storage_uid", "created_kinds", "token_created"),
    (
        pytest.param(
            lambda row: row["ownership"].update(
                {RECOVERY.CREDENTIAL_RESOURCE_UID: None}
            ),
            None,
            (NAMESPACE_KIND, SECRET_KIND),
            True,
            id="secret-uid-unobserved",
        ),
        pytest.param(
            lambda row: row["ownership"][RESOURCES_FIELD][0].pop("created"),
            SECRET_UID,
            (SECRET_KIND,),
            True,
            id="resource-created-missing",
        ),
        pytest.param(
            lambda row: row["ownership"]["created"].update(
                {RECOVERY.CREDENTIAL: False}
            ),
            SECRET_UID,
            (NAMESPACE_KIND, SECRET_KIND),
            False,
            id="applied-token-not-created-by-this-run",
        ),
        pytest.param(
            lambda row: row["ownership"]["created"].update({RECOVERY.CREDENTIAL: None}),
            SECRET_UID,
            (NAMESPACE_KIND, SECRET_KIND),
            False,
            id="token-creation-unknown",
        ),
        pytest.param(
            lambda row: row.update(
                {
                    INSTALLATION_FIELD: {
                        RESOURCES_FIELD: [
                            {
                                "kind": DEPLOYMENT_KIND,
                                "name": DEPLOYMENT_NAME,
                                "namespace": NAMESPACE,
                                "uid": DEPLOYMENT_UID,
                                "created": True,
                            }
                        ]
                    }
                }
            ),
            SECRET_UID,
            (NAMESPACE_KIND, SECRET_KIND),
            True,
            id="installation-rows-ignored",
        ),
    ),
)
def test_credentials_trace_reads_only_owned_rows_and_creation_markers(
    change: Callable[[JSONRow], object],
    storage_uid: str | None,
    created_kinds: tuple[str, ...],
    token_created: bool,
) -> None:
    """Trace a connected apply when one ownership or installation field changes.

    :param change: Edits one field of the record copy.
    :param storage_uid: Expected UID of the Secret that stores the Agent token.
    :param created_kinds: Kinds expected in ``kubernetes.created_objects``, in order.
    :param token_created: Expected ``agent_credential.created``.
    """
    row = json.loads(RECORD_BASELINE)
    change(row)

    trace = RECEIPT._credentials(_authorities(), row, CONNECT)

    _definition_validator(CREDENTIALS_FIELD).validate(trace)
    agent = trace["agent_credential"]
    assert (agent["state"], agent["id"], agent["created"]) == (
        RECOVERY.PRESENT,
        TOKEN_ID,
        token_created,
    )
    assert agent["storage"]["uid"] == storage_uid
    assert [item["kind"] for item in trace["kubernetes"]["created_objects"]] == list(
        created_kinds
    )


@pytest.mark.parametrize(
    ("operation", "observe", "state"),
    (
        pytest.param(CONNECT, _fresh_plan, STATUS.PLANNED, id="fresh-connect-plan"),
        pytest.param(
            DELETE, _logical_absence, RECOVERY.ABSENT, id="logical-delete-plan"
        ),
    ),
)
def test_record_carries_a_schema_valid_credentials_trace(
    operation: str,
    observe: Callable[[SELECTION.ConnectSelection], object],
    state: str,
) -> None:
    """Add ``credentials`` to the record that ``_record`` builds and validate the record.

    :param operation: ``connect`` or ``delete``.
    :param observe: Builds the plan observation from the selection.
    :param state: Expected Agent token state for a record without ownership.
    """
    record = _planned_record(operation, observe)

    _definition_validator(RECORD_DEFINITION).validate(record)
    assert RESOURCES_FIELD not in record[INSTALLATION_FIELD]
    assert record[CREDENTIALS_FIELD] == {
        "gitlab": {"calls": json.loads(EXPECTED_CALLS)},
        "kubernetes": {"created_objects": []},
        "agent_credential": {
            "state": state,
            "id": None,
            "name": None,
            "created": False,
            "storage": None,
            "consumers": [],
        },
    }


@pytest.mark.parametrize(
    ("change", "keyword", "location"),
    (
        pytest.param(
            lambda record: record.pop(CREDENTIALS_FIELD),
            "required",
            (),
            id="credentials-missing",
        ),
        pytest.param(
            lambda record: record[INSTALLATION_FIELD].update({RESOURCES_FIELD: []}),
            UNKNOWN_PROPERTY,
            (INSTALLATION_FIELD,),
            id="installation-resources",
        ),
    ),
)
def test_record_schema_rejects_missing_credentials_and_installation_rows(
    change: Callable[[JSONRow], object], keyword: str, location: JSONPath
) -> None:
    """Reject a planned record without ``credentials`` or with ``installation.resources``.

    :param change: Edits one field of the record copy.
    :param keyword: Schema keyword expected to fail.
    :param location: Instance path the single failure must point at.
    """
    record = _planned_record(CONNECT, _fresh_plan)
    validator = _definition_validator(RECORD_DEFINITION)
    assert list(validator.iter_errors(record)) == []

    change(record)

    assert [
        (error.validator, tuple(error.absolute_path))
        for error in validator.iter_errors(record)
    ] == [(keyword, location)]


@pytest.mark.parametrize(
    ("change", "keyword", "location"),
    (
        pytest.param(
            lambda document: document["gitlab"]["calls"][0].update(
                {"endpoint": PROJECT_ROUTE + ROUTE_QUERY}
            ),
            "pattern",
            ("gitlab", "calls", 0, "endpoint"),
            id="endpoint-with-query",
        ),
        pytest.param(
            lambda document: document["gitlab"]["calls"][0].update(
                {"endpoint": PROJECT_ROUTE + ROUTE_FRAGMENT}
            ),
            "pattern",
            ("gitlab", "calls", 0, "endpoint"),
            id="endpoint-with-fragment",
        ),
        pytest.param(
            lambda document: document["gitlab"]["calls"][0].update(
                {"method": HTTPMethod.PATCH.value}
            ),
            "enum",
            ("gitlab", "calls", 0, "method"),
            id="method-outside-enum",
        ),
        pytest.param(
            lambda document: document["gitlab"].update(
                {TABLE_FIELD: ENDPOINTS.GITLAB_PRIVATE_TABLE}
            ),
            UNKNOWN_PROPERTY,
            ("gitlab",),
            id="removed-table-field",
        ),
        pytest.param(
            lambda document: document["agent_credential"]["storage"].update(
                {"key": OTHER_SECRET_KEY}
            ),
            "anyOf",
            ("agent_credential", "storage"),
            id="storage-key-not-token",
        ),
        pytest.param(
            lambda document: document["agent_credential"].update(
                {UNKNOWN_FIELD: TOKEN_PLAINTEXT}
            ),
            UNKNOWN_PROPERTY,
            ("agent_credential",),
            id="unknown-property",
        ),
        pytest.param(
            lambda document: document["agent_credential"].update(
                {"state": STATUS.APPLIED}
            ),
            "enum",
            ("agent_credential", "state"),
            id="state-outside-enum",
        ),
        pytest.param(
            lambda document: document["agent_credential"]["consumers"][0].pop(
                "pod_uid"
            ),
            "required",
            ("agent_credential", "consumers", 0),
            id="consumer-without-pod-uid",
        ),
        pytest.param(
            lambda document: document["agent_credential"].update({"id": 0}),
            "minimum",
            ("agent_credential", "id"),
            id="token-id-below-one",
        ),
    ),
)
def test_credentials_schema_rejects_one_broken_invariant(
    change: Callable[[JSONRow], object], keyword: str, location: JSONPath
) -> None:
    """Reject a credentials object that differs from the valid baseline in one field.

    :param change: Edits one field of the credentials copy.
    :param keyword: Schema keyword expected to fail.
    :param location: Instance path the single failure must point at.
    """
    validator = _definition_validator(CREDENTIALS_FIELD)
    assert list(validator.iter_errors(json.loads(CREDENTIALS_BASELINE))) == []
    document = json.loads(CREDENTIALS_BASELINE)

    change(document)

    assert [
        (error.validator, tuple(error.absolute_path))
        for error in validator.iter_errors(document)
    ] == [(keyword, location)]


def test_credentials_survive_report_rendering_and_portable_receipt(
    tmp_path: Path,
) -> None:
    """Keep the credentials object unchanged in emitted JSON and the portable receipt.

    :param tmp_path: Directory that receives the portable receipt file.
    """
    record = {CREDENTIALS_FIELD: json.loads(CREDENTIALS_BASELINE)}
    data = REPORT.report(EVIDENCE.AGENT_KIND, FIXTURE_GITLAB["url"], {}, [record], [])
    destination = tmp_path / RECEIPT_FILE

    rendered = json.loads(REPORT.emit(data, JSON_MODE))
    PORTABLE.write_portable_receipt(data, str(destination))
    written = json.loads(destination.read_text(encoding="utf-8"))

    expected = [{CREDENTIALS_FIELD: json.loads(CREDENTIALS_BASELINE)}]
    assert rendered["records"] == expected
    assert written["records"] == expected
