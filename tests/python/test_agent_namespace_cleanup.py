"""Check that the Agent namespace deletion guard proves exact ownership."""

from __future__ import annotations

import json
from typing import Final
from unittest.mock import Mock, create_autospec

import pytest
import test_gitlab_agent_recovery as FIXTURE
from conftest import import_script_module

GUARD = import_script_module("lib.python.core.kubernetes.agent_namespace")
KUBERNETES = FIXTURE.KUBERNETES
RUNTIME = FIXTURE.RUNTIME

NAMESPACE: Final = FIXTURE.NAMESPACE
NAMESPACE_UID: Final = FIXTURE.NAMESPACE_UID
JOURNAL_UID: Final = FIXTURE.RECORD_UID
RUN_ID: Final = FIXTURE.RUN_ID
TIMEOUT: Final = 30.0
PERMITTED_UIDS: Final = frozenset((NAMESPACE_UID, JOURNAL_UID))
DISCOVERED: Final = ("configmaps", "events", "pods", "secrets", "serviceaccounts")
NS_FIRST: Final = "namespace-first"
NS_LAST: Final = "namespace-last"
DISCOVERY: Final = "discovery"
INVENTORY: Final = "inventory"
METADATA: Final = "metadata"
LABELS: Final = "labels"
UID: Final = "uid"
CONTINUE: Final = "continue"
ITEMS: Final = "items"
NAMESPACE_FIELD: Final = "namespace"
FIRST_ITEM: Final = 0
OTHER_UID: Final = "foreign-native-uid"
OTHER_SCOPE: Final = "unrelated-workload"
OWNERSHIP_MISMATCH: Final = "agent_namespace_ownership_mismatch"
DISCOVERY_INCOMPLETE: Final = "agent_namespace_discovery_incomplete"
INVENTORY_INVALID: Final = "agent_namespace_inventory_invalid"
FOREIGN_OBJECT: Final = "agent_namespace_foreign_object"
BASE_NAMESPACE: Final = {
    "apiVersion": "v1",
    "kind": "Namespace",
    METADATA: {"name": NAMESPACE, UID: NAMESPACE_UID, LABELS: FIXTURE.LABELS},
}
BASELINE: Final = json.dumps(
    {
        NS_FIRST: BASE_NAMESPACE,
        NS_LAST: BASE_NAMESPACE,
        DISCOVERY: "\n".join(DISCOVERED) + "\n",
        INVENTORY: {
            "apiVersion": "v1",
            "kind": "List",
            METADATA: {},
            ITEMS: [
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    METADATA: {
                        "name": FIXTURE.RECORD_NAME,
                        NAMESPACE_FIELD: NAMESPACE,
                        UID: JOURNAL_UID,
                        LABELS: FIXTURE.LABELS,
                    },
                }
            ],
        },
    }
)


def _service(wire: dict[str, object]) -> Mock:
    """Return independent Namespace reads and native discovery and list results."""
    service = create_autospec(KUBERNETES.KubernetesService, instance=True)
    service.read.side_effect = (wire[NS_FIRST], wire[NS_LAST])
    service.command.side_effect = (
        RUNTIME.CommandResult((), 0, wire[DISCOVERY], ""),
        RUNTIME.CommandResult((), 0, json.dumps(wire[INVENTORY]), ""),
    )
    return service


def _verify(service: Mock) -> None:
    """Run the real read-only guard immediately before a would-be cascade."""
    GUARD.verify_agent_namespace_cleanup(
        service, NAMESPACE, NAMESPACE_UID, RUN_ID, PERMITTED_UIDS, timeout=TIMEOUT
    )


def test_agent_namespace_guard_reads_exact_metadata_without_mutation() -> None:
    """Accept the original recovery journal and independently recheck the namespace."""
    service = _service(json.loads(BASELINE))

    assert _verify(service) is None

    assert [entry[0] for entry in service.mock_calls] == [
        "read",
        "command",
        "command",
        "read",
    ]
    for observed in service.read.call_args_list:
        assert observed.args == ("Namespace", NAMESPACE, "")
        assert 0 < observed.kwargs["timeout"] <= TIMEOUT
    discovery, inventory = service.command.call_args_list
    assert discovery.args == (
        "api-resources",
        "--namespaced=true",
        "--verbs=list",
        "--cached=false",
        "-o",
        "name",
    )
    assert inventory.args == (
        "get",
        ",".join(DISCOVERED),
        "--namespace",
        NAMESPACE,
        "--show-managed-fields",
        "-o",
        "json",
    )
    for observed in (discovery, inventory):
        assert 0 < observed.kwargs["timeout"] <= TIMEOUT


@pytest.mark.parametrize(
    ("path", "value", "reason", "commands", "namespace_reads"),
    (
        ((NS_FIRST, METADATA, UID), OTHER_UID, OWNERSHIP_MISMATCH, 0, 1),
        (
            (NS_FIRST, METADATA, LABELS, FIXTURE.SCOPE.RUN_ID_LABEL),
            OTHER_SCOPE,
            OWNERSHIP_MISMATCH,
            0,
            1,
        ),
        (
            (DISCOVERY,),
            "\n".join(item for item in DISCOVERED if item != "secrets") + "\n",
            DISCOVERY_INCOMPLETE,
            1,
            1,
        ),
        ((INVENTORY, METADATA, CONTINUE), "next-page", INVENTORY_INVALID, 2, 1),
        (
            (INVENTORY, ITEMS, FIRST_ITEM, METADATA, NAMESPACE_FIELD),
            OTHER_SCOPE,
            INVENTORY_INVALID,
            2,
            1,
        ),
        (
            (INVENTORY, ITEMS, FIRST_ITEM, METADATA, UID),
            OTHER_UID,
            FOREIGN_OBJECT,
            2,
            1,
        ),
        ((NS_LAST, METADATA, UID), OTHER_UID, OWNERSHIP_MISMATCH, 2, 2),
    ),
    ids=(
        "namespace-replaced-before-discovery",
        "ownership-label-mismatch",
        "required-type-missing",
        "inventory-pagination-unproven",
        "foreign-namespace-item",
        "foreign-object-uid",
        "namespace-replaced-after-inventory",
    ),
)
def test_agent_namespace_guard_rejects_one_unverified_observation(
    path: tuple[str | int, ...],
    value: str,
    reason: str,
    commands: int,
    namespace_reads: int,
) -> None:
    """A single changed observation blocks without any mutating call."""
    wire = json.loads(BASELINE)
    target = wire
    for field in path[:-1]:
        target = target[field]
    target[path[-1]] = value
    service = _service(wire)

    with pytest.raises(KUBERNETES.KubernetesError, match=f"^{reason}$"):
        _verify(service)

    assert service.command.call_count == commands
    assert service.read.call_count == namespace_reads
    assert all(entry[0] in ("read", "command") for entry in service.mock_calls)
    assert all(
        observed.args[0] in ("get", "api-resources")
        for observed in service.command.call_args_list
    )
