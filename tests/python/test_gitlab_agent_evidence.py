"""Agent acceptance checker: a connected snapshot with null resources."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from http import HTTPStatus
from typing import Any, Final

import pytest
from conftest import import_script_module

EVIDENCE = import_script_module("lib.python.core.gitlab.cluster_agent.evidence")
RECOVERY = import_script_module("lib.python.core.gitlab.cluster_agent.recovery")

PROJECT_ID: Final = 42
AGENT_ID: Final = 17
AGENT_NAME: Final = "consumer-agent"
TOKEN_ID: Final = 29
NAMESPACE: Final = "consumer-0a1b2c"
NAMESPACE_UID: Final = "66666666-7777-8888-9999-000000000000"
RELEASE: Final = "consumer-agent"
RELEASE_REVISION: Final = 1
RELEASE_STORAGE_UID: Final = "77777777-8888-9999-0000-111111111111"
SECRET_NAME: Final = "consumer-agent-token-0a1b"
SECRET_UID: Final = "11111111-2222-3333-4444-555555555555"
OTHER_UID: Final = "22222222-3333-4444-5555-666666666666"
CONNECTION_ID: Final = "4355863225129730728"
API_VERSION: Final = "v1"
NAMESPACE_KIND: Final = "Namespace"
SECRET_KIND: Final = "Secret"
RESOURCES_FIELD: Final = "resources"

JSONRow = dict[str, Any]


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
        "state": RECOVERY.PRESENT,
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
                {"state": RECOVERY.ABSENT}
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
