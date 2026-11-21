"""Agent result credential trace: values, schema and redaction survival."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Final

import pytest
from conftest import SKILL_ROOT, import_script_module
from jsonschema import Draft202012Validator

PROVIDER = import_script_module("lib.python.core.gitlab.cluster_agent.provider")
RECEIPT = import_script_module("lib.python.core.gitlab.cluster_agent.receipt")
RECOVERY = import_script_module("lib.python.core.gitlab.cluster_agent.recovery")
SECRET = import_script_module("lib.python.core.gitlab.cluster_agent.secret")
RUNTIME = import_script_module("lib.python.core.runtime")
ENDPOINTS = import_script_module("lib.python.core.endpoints")
STATUS = import_script_module("lib.python.sys.status")

RESULT_SCHEMA: Final = SKILL_ROOT / "schemas" / "results" / "gitlab-agent.schema.json"
GET: Final = "GET"
POST: Final = "POST"
GITLAB_TABLE: Final = ENDPOINTS.GITLAB_PRIVATE_TABLE
KUBERNETES_TABLE: Final = "kubernetes"
PROJECT_ROUTE: Final = "projects/42"
AGENTS_ROUTE: Final = f"{PROJECT_ROUTE}/cluster_agents"
TOKENS_ROUTE: Final = f"{AGENTS_ROUTE}/17/tokens"
GRAPHQL_ROUTE: Final = "graphql"
TOKEN_ID: Final = 29
TOKEN_NAME: Final = "consumer-agent-token-0a1b"
NAMESPACE: Final = "consumer-0a1b2c"
SECRET_NAME: Final = "consumer-agent-token-0a1b"
SECRET_UID: Final = "11111111-2222-3333-4444-555555555555"
NAMESPACE_UID: Final = "66666666-7777-8888-9999-000000000000"
RELEASE_STORAGE: Final = "sh.helm.release.v1.consumer-agent.v1"
CONNECTION_ID: Final = "4355863225129730728"
POD_NAME: Final = "consumer-agent-v2-abc-xyz"
POD_UID: Final = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
TOKEN_PLAINTEXT: Final = "unit-agent-token-plaintext"
PRESENT: Final = "PRESENT"
ABSENT: Final = "ABSENT"

CALLS: Final = (
    PROVIDER.ProviderCall(GET, PROJECT_ROUTE),
    PROVIDER.ProviderCall(GET, "metadata"),
    PROVIDER.ProviderCall(POST, AGENTS_ROUTE),
    PROVIDER.ProviderCall(POST, TOKENS_ROUTE),
    PROVIDER.ProviderCall(POST, GRAPHQL_ROUTE),
)

# Each case copies this record; a case changes only the fields it names.
RECORD_BASELINE: Final = json.dumps(
    {
        "action": STATUS.APPLIED,
        "ownership": {
            RECOVERY.CREDENTIAL_ID: TOKEN_ID,
            "registration_credential_name": TOKEN_NAME,
            "namespace": NAMESPACE,
            "credential_resource_name": SECRET_NAME,
            "credential_resource_uid": SECRET_UID,
            "created": {"registration_credential": True},
        },
        "installation": {
            "resources": [
                {
                    "kind": "Namespace",
                    "name": NAMESPACE,
                    "namespace": "",
                    "uid": NAMESPACE_UID,
                    "created": True,
                },
                {
                    "kind": "Secret",
                    "name": SECRET_NAME,
                    "namespace": NAMESPACE,
                    "uid": SECRET_UID,
                    "created": True,
                },
                {
                    "kind": "Secret",
                    "name": RELEASE_STORAGE,
                    "namespace": NAMESPACE,
                    "uid": None,
                    "created": False,
                },
            ]
        },
        "connection": {
            "items": [
                {
                    "connection_id": CONNECTION_ID,
                    "pod_name": POD_NAME,
                    "pod_uid": POD_UID,
                }
            ]
        },
    }
)


def _authorities() -> SimpleNamespace:
    """Build the two selected providers the trace reads.

    :returns: Authorities whose GitLab provider sent ``CALLS`` and whose target
        selected the private GitLab table and the Kubernetes table.
    """
    routing = SimpleNamespace(gitlab=GITLAB_TABLE, kubernetes=KUBERNETES_TABLE)
    return SimpleNamespace(
        provider=SimpleNamespace(calls=CALLS),
        kubernetes=SimpleNamespace(target=SimpleNamespace(routing=routing)),
    )


def _validator() -> Draft202012Validator:
    """Validate against the shipped result schema's credentials definition.

    :returns: Validator for ``$defs.credentials`` with the schema's own definitions.
    """
    schema = json.loads(RESULT_SCHEMA.read_text(encoding="utf-8"))
    return Draft202012Validator(
        {"$defs": schema["$defs"], "$ref": "#/$defs/credentials"}
    )


@pytest.mark.parametrize(
    ("case", "action", "owner", "state", "created", "created_kinds"),
    (
        ("fresh-apply", STATUS.APPLIED, True, PRESENT, True, ("Namespace", "Secret")),
        ("repeat-no-op", STATUS.NO_OP, True, PRESENT, False, ()),
        ("live-plan", STATUS.PLANNED, False, STATUS.PLANNED, False, ()),
        ("absent-delete", STATUS.NO_OP, False, ABSENT, False, ()),
    ),
)
def test_credentials_trace_names_each_token_use_without_values(
    case: str,
    action: str,
    owner: bool,
    state: str,
    created: bool,
    created_kinds: tuple[str, ...],
) -> None:
    """Report GitLab requests, created objects and the Agent token's Secret per record state.

    :param case: Row name for the reader.
    :param action: Record action produced by the operation.
    :param owner: Whether the record carries an ownership document.
    :param state: Expected Agent token state.
    :param created: Expected ``agent_credential.created``.
    :param created_kinds: Kinds expected in ``kubernetes.created_objects``.
    """
    row = json.loads(RECORD_BASELINE)
    row["action"] = action
    if not owner:
        row["ownership"] = None
        row["connection"]["items"] = []
    baseline = copy.deepcopy(row)

    trace = RECEIPT._credentials(_authorities(), row)

    assert row == baseline, case
    _validator().validate(trace)
    assert trace["gitlab"] == {
        "table": GITLAB_TABLE,
        "calls": [{"method": item.method, "endpoint": item.endpoint} for item in CALLS],
    }
    assert trace["kubernetes"]["table"] == KUBERNETES_TABLE
    assert [item["kind"] for item in trace["kubernetes"]["created_objects"]] == list(
        created_kinds
    )
    agent = trace["agent_credential"]
    assert (agent["state"], agent["created"]) == (state, created)
    if owner:
        assert (agent["id"], agent["name"]) == (TOKEN_ID, TOKEN_NAME)
        assert agent["storage"] == {
            "kind": "Secret",
            "namespace": NAMESPACE,
            "name": SECRET_NAME,
            "uid": SECRET_UID,
            "key": SECRET.TOKEN_KEY,
        }
        assert agent["consumers"] == [
            {"connection_id": CONNECTION_ID, "pod_name": POD_NAME, "pod_uid": POD_UID}
        ]
    else:
        assert (agent["id"], agent["name"], agent["storage"]) == (None, None, None)
        assert agent["consumers"] == []
    assert RUNTIME.redact_tree({"credentials": trace}) == {"credentials": trace}
    assert TOKEN_PLAINTEXT not in json.dumps(trace)
