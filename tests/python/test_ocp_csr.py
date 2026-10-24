"""Exercise exact, bounded CSR selection without a live OpenShift cluster.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from types import SimpleNamespace
from typing import Optional

import pytest
from conftest import authenticated_kubernetes_service, import_script_module

CSR = import_script_module("lib.python.core.kubernetes.ocp_csr")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
RUNTIME = import_script_module("lib.python.core.runtime")


def csr_document(
    name: str,
    signer: str,
    *,
    uid: Optional[str] = None,
    username: str = "",
    usages: tuple[str, ...] = ("unusual fixture usage",),
    conditions: tuple[str, ...] = (),
    issued: bool = False,
) -> dict[str, object]:
    """Build one native CSR record without embedding certificate request bytes."""
    return {
        "apiVersion": "certificates.k8s.io/v1",
        "kind": "CertificateSigningRequest",
        "metadata": {
            "name": name,
            "uid": uid or f"uid-{name}",
            "creationTimestamp": f"2026-10-18T00:00:{len(name):02d}Z",
        },
        "spec": {
            "signerName": signer,
            "username": username,
            "usages": list(usages),
        },
        "status": {
            "conditions": [
                {"type": condition, "status": "True"} for condition in conditions
            ],
            **({"certificate": "fixture-issued"} if issued else {}),
        },
    }


def described_subject(
    common_name: str,
    *,
    organization: str = "fixture:organization",
    dns_name: str = "unrelated.example.test",
    ip_address: str = "192.0.2.250",
) -> str:
    """Render the bounded identity fields printed by ``oc describe csr``."""
    return (
        f"Common Name: {common_name}\n"
        f"Organization: {organization}\n"
        f"DNS Names: {dns_name}\n"
        f"IP Addresses: {ip_address}\n"
    )


class CSRService:
    """Provide finite CSR reads, native subject output and approval read-back."""

    def __init__(
        self,
        payload: object,
        subjects: Optional[dict[str, str]] = None,
        *,
        describe_failure: str = "",
    ) -> None:
        """Retain detached provider records and an exact mutation journal."""
        self.payload = deepcopy(payload)
        self.subjects = subjects or {}
        self.describe_failure = describe_failure
        self.json_calls: list[tuple[str, ...]] = []
        self.described: list[str] = []
        self.approved: list[str] = []
        self.timeouts: list[tuple[str, Optional[float]]] = []

    def json(self, *args: str, timeout: Optional[float] = None) -> object:
        """Return one native list result for the exact CSR collection read."""
        assert args == ("get", "certificatesigningrequests", "-o", "json")
        self.json_calls.append(args)
        self.timeouts.append(("list", timeout))
        return deepcopy(self.payload)

    def read(
        self, kind: str, name: str, namespace: str, *, timeout: Optional[float] = None
    ) -> Optional[dict[str, object]]:
        """Read one exact CSR from the current detached provider snapshot."""
        assert (kind, namespace) == ("certificatesigningrequest", "")
        self.timeouts.append(("read", timeout))
        if not isinstance(self.payload, dict):
            return None
        items = self.payload.get("items", [])
        if not isinstance(items, list):
            return None
        for item in items:
            if (
                isinstance(item, dict)
                and isinstance(item.get("metadata"), dict)
                and item["metadata"].get("name") == name
            ):
                return deepcopy(item)
        return None

    def command(
        self, *args: str, openshift: bool = False, timeout: Optional[float] = None
    ) -> RUNTIME.CommandResult:
        """Decode one subject or approve only the explicitly named request."""
        assert openshift
        self.timeouts.append(("command", timeout))
        if args[:2] == ("describe", "csr"):
            name = args[2]
            self.described.append(name)
            if name == self.describe_failure:
                return RUNTIME.CommandResult(args, 17, "", "fixture decode failure")
            return RUNTIME.CommandResult(args, 0, self.subjects[name], "")
        assert args[:3] == ("adm", "certificate", "approve")
        name = args[3]
        self.approved.append(name)
        current = self.read("certificatesigningrequest", name, "", timeout=timeout)
        assert current is not None
        status = current["status"]
        assert isinstance(status, dict)
        conditions = status.setdefault("conditions", [])
        assert isinstance(conditions, list)
        conditions.append({"type": "Approved", "status": "True"})
        assert isinstance(self.payload, dict)
        items = self.payload["items"]
        assert isinstance(items, list)
        for index, item in enumerate(items):
            if (
                isinstance(item, dict)
                and isinstance(item.get("metadata"), dict)
                and item["metadata"].get("name") == name
            ):
                items[index] = current
                break
        return RUNTIME.CommandResult(args, 0, "approved\n", "")


def test_exact_hostname_and_two_signers_are_the_only_approval_candidates() -> None:
    """Org, usage and SAN evidence cannot widen or narrow the named join selector."""
    selector = CSR.JoinSelector("worker-07", ("192.0.2.7",))
    documents = [
        csr_document("client-exact", CSR.CLIENT_SIGNER),
        csr_document(
            "serving-exact",
            CSR.SERVING_SIGNER,
            username=selector.common_name,
            usages=("fixture serving usage",),
        ),
        csr_document("client-other-host", CSR.CLIENT_SIGNER),
        csr_document("unrelated", "example.test/unrelated-signer"),
    ]
    service = CSRService(
        {"items": documents},
        {
            "client-exact": described_subject(
                selector.common_name,
                organization="not-a-selection-rule",
                dns_name="different.example.test",
                ip_address="198.51.100.19",
            ),
            "serving-exact": described_subject(
                selector.common_name,
                organization="another:organization",
                dns_name="another.example.test",
                ip_address="203.0.113.44",
            ),
            "client-other-host": described_subject("system:node:worker-99"),
        },
    )

    inventory = CSR.read_csr_inventory(service, selector, deadline=float("inf"))

    client = inventory.selected(CSR.CLIENT_SIGNER)
    serving = inventory.selected(CSR.SERVING_SIGNER)
    assert client is not None and client.name == "client-exact"
    assert serving is not None and serving.name == "serving-exact"
    assert inventory.unrelated_signer_count == 1
    assert service.described == [
        "client-exact",
        "serving-exact",
        "client-other-host",
    ]

    CSR.approve_csr(service, selector, client)
    CSR.approve_csr(service, selector, serving)

    assert service.approved == ["client-exact", "serving-exact"]
    unrelated = service.read("certificatesigningrequest", "unrelated", "")
    assert unrelated is not None
    assert unrelated["status"] == {"conditions": []}


def test_inventory_expiry_blocks_subject_decode_before_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired shared deadline stops native decoding and any later approval."""
    selector = CSR.JoinSelector("worker-08", ("192.0.2.8",))
    service = CSRService(
        {"items": [csr_document("client", CSR.CLIENT_SIGNER)]},
        {"client": described_subject(selector.common_name)},
    )
    monkeypatch.setattr(CSR, "time", SimpleNamespace(monotonic=lambda: 12.0))

    with pytest.raises(KUBERNETES.KubernetesError, match="node_join_deadline_exceeded"):
        CSR.read_csr_inventory(service, selector, deadline=12.0)

    assert service.json_calls == []
    assert service.described == []
    assert service.approved == []


def test_approval_rejects_same_name_with_changed_provider_uid() -> None:
    """A recreated CSR under the selected name cannot inherit prior approval authority."""
    selector = CSR.JoinSelector("worker-uid", ("192.0.2.81",))
    service = CSRService(
        {"items": [csr_document("client", CSR.CLIENT_SIGNER, uid="uid-recreated")]},
        {"client": described_subject(selector.common_name)},
    )
    selected = CSR.CSRObservation(
        name="client",
        uid="uid-original",
        signer=CSR.CLIENT_SIGNER,
        usages=frozenset(),
        subject=CSR.decode_described_subject(described_subject(selector.common_name)),
        approved=False,
        denied=False,
        issued=False,
        created_at="2026-10-18T00:00:00Z",
    )

    with pytest.raises(
        KUBERNETES.KubernetesError, match="node_join_csr_identity_changed"
    ):
        CSR.approve_csr(service, selector, selected)

    assert service.approved == []


@pytest.mark.parametrize(
    ("payload", "reason"),
    (
        ([], "node_join_csr_list_invalid"),
        (
            {
                "items": [
                    {
                        "metadata": {"name": "malformed", "uid": "uid-malformed"},
                        "spec": {},
                        "status": {},
                    }
                ]
            },
            "node_join_csr_invalid",
        ),
    ),
)
def test_malformed_provider_records_report_the_exact_boundary(
    payload: object, reason: str
) -> None:
    """Collection-shape and record-shape failures remain distinguishable."""
    selector = CSR.JoinSelector("worker-09", ("192.0.2.9",))
    service = CSRService(payload)

    with pytest.raises(KUBERNETES.KubernetesError, match=reason):
        CSR.read_csr_inventory(service, selector, deadline=float("inf"))


def test_native_subject_decode_failure_is_reported_without_approval() -> None:
    """A failed native subject command cannot be mistaken for a malformed identity."""
    selector = CSR.JoinSelector("worker-10", ("192.0.2.10",))
    document = csr_document("client", CSR.CLIENT_SIGNER)
    service = CSRService(
        {"items": [document]},
        {"client": described_subject(selector.common_name)},
        describe_failure="client",
    )

    with pytest.raises(KUBERNETES.KubernetesError, match="command_failed"):
        CSR.observe_csr(service, document)

    assert service.approved == []


@pytest.mark.parametrize("operation", ("inventory", "approval"))
def test_each_csr_provider_call_receives_the_remaining_join_deadline(
    operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CSR collection, decoding, approval and read-back share the remaining bound."""
    selector = CSR.JoinSelector("worker-budget", ("192.0.2.33",))
    document = csr_document("client", CSR.CLIENT_SIGNER)
    service = CSRService(
        {"items": [document]}, {"client": described_subject(selector.common_name)}
    )
    monkeypatch.setattr(CSR, "time", SimpleNamespace(monotonic=lambda: 10.0))

    if operation == "inventory":
        result = CSR.read_csr_inventory(service, selector, deadline=10.25)
        assert result.selected(CSR.CLIENT_SIGNER) is not None
        assert {kind for kind, _timeout in service.timeouts} == {"list", "command"}
    else:
        selected = CSR.observe_csr(service, document)
        service.timeouts.clear()
        result = CSR.approve_csr(service, selector, selected, deadline=10.25)
        assert result.approved
        assert service.approved == ["client"]
        assert {kind for kind, _timeout in service.timeouts} == {"read", "command"}
    assert service.timeouts
    assert all(timeout == 0.25 for _kind, timeout in service.timeouts)


@pytest.mark.parametrize(("remaining", "expected"), ((0.25, 0.25), (100.0, 30.0)))
def test_csr_deadline_caps_the_real_service_native_request_timeout(
    remaining: float,
    expected: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow native list is bounded by both the join budget and service default."""
    from test_tool_pod import MemoryCluster

    service = authenticated_kubernetes_service(MemoryCluster().target, timeout=30)
    timeouts: list[float] = []

    def native(
        argv: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, Optional[str]],
    ) -> RUNTIME.CommandResult:
        """Replace only native process execution and retain its exact timeout."""
        del env
        timeouts.append(timeout)
        return RUNTIME.CommandResult(tuple(argv), 0, json.dumps({"items": []}), "")

    monkeypatch.setattr(KUBERNETES, "run_command_bounded", native)
    monkeypatch.setattr(CSR, "time", SimpleNamespace(monotonic=lambda: 10.0))

    inventory = CSR.read_csr_inventory(
        service,
        CSR.JoinSelector("worker-transport", ("192.0.2.34",)),
        deadline=10.0 + remaining,
    )

    assert inventory.requests == ()
    assert timeouts == [expected]
    assert service.timeout == 30
