"""Exercise bounded node joining and owned cleanup without cluster access.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Optional

import pytest
from conftest import import_script_module
from test_tool_pod import MemoryCluster

CSR = import_script_module("lib.python.core.kubernetes.ocp_csr")
JOIN = import_script_module("lib.python.core.kubernetes.ocp_join")
INVOCATION = import_script_module("lib.python.core.kubernetes.ocp_invocation")
ONBOARD = import_script_module("lib.python.core.kubernetes.ocp_onboard")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
RUNTIME = import_script_module("lib.python.core.runtime")


def node_document(
    name: str,
    ip_addresses: tuple[str, ...],
    *,
    ready: bool = True,
    roles: tuple[str, ...] = ("worker",),
) -> dict[str, object]:
    """Build one exact Node read-back with independent status and identity fields."""
    return {
        "apiVersion": "v1",
        "kind": "Node",
        "metadata": {
            "name": name,
            "uid": f"uid-{name}",
            "labels": {f"node-role.kubernetes.io/{role}": "" for role in roles},
        },
        "status": {
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
            "addresses": [
                {"type": "InternalIP", "address": address} for address in ip_addresses
            ],
        },
    }


def request(
    name: str,
    signer: str,
    selector: CSR.JoinSelector,
    *,
    approved: bool = False,
    issued: bool = False,
) -> CSR.CSRObservation:
    """Create one typed CSR observation for orchestration-only tests."""
    return CSR.CSRObservation(
        name=name,
        uid=f"uid-{name}",
        signer=signer,
        usages=frozenset(("fixture usage",)),
        subject=CSR.CSRSubject(
            common_name=selector.common_name,
            organizations=("fixture:organization",),
            dns_names=("unrelated.example.test",),
            email_addresses=(),
            uris=(),
            ip_addresses=("198.51.100.200",),
        ),
        approved=approved,
        denied=False,
        issued=issued,
        created_at="2026-10-18T00:00:00Z",
    )


class JoinService(MemoryCluster):
    """Return a finite Node sequence and journal the one allowed native monitor."""

    def __init__(self, nodes: tuple[Optional[dict[str, object]], ...]) -> None:
        """Retain detached Node responses and ordered provider events."""
        super().__init__()
        self.nodes = nodes
        self.node_reads = 0
        self.events: list[str] = []
        self.commands: list[tuple[str, ...]] = []

    def read(self, kind: str, name: str, namespace: str) -> Optional[dict[str, object]]:
        """Read only the selected cluster-scoped Node identity."""
        assert (kind, namespace) == ("node", "")
        self.events.append("read-node")
        selected = self.nodes[min(self.node_reads, len(self.nodes) - 1)]
        self.node_reads += 1
        return deepcopy(selected)

    def json(self, *_args: str) -> dict[str, object]:
        """Reject an unmocked CSR collection read."""
        pytest.fail("join orchestration reached an unmocked CSR collection")

    def command(self, *args: str, openshift: bool = False) -> RUNTIME.CommandResult:
        """Accept only the native selected-address monitor after CSR completion."""
        assert openshift
        assert args[:4] == ("adm", "node-image", "monitor", "--ip-addresses")
        self.commands.append(args)
        self.events.append("monitor")
        return RUNTIME.CommandResult(args, 0, "joined\n", "")


class ArtifactRelease(KUBERNETES.K8SCallback):
    """Mark the point where production artifact-removal callbacks may begin."""

    def __init__(self, action: JOIN.NodeJoinAction, events: list[str]) -> None:
        """Retain the join action and shared provider event journal."""
        super().__init__(action)
        self.events = events
        self.ran = False

    def run(self) -> dict[str, object]:
        """Require final Ready evidence before representing artifact release."""
        assert isinstance(self.state, JOIN.OCPJoinState)
        assert self.state.join_node_present
        assert self.state.join_node.complete
        assert self.state.join_existing_ready or self.state.join_monitor_exit_code == 0
        self.ran = True
        self.events.append("artifact-release")
        return {"action": "APPLIED", "verified": True}

    def cleanup(self) -> dict[str, object]:
        """Return no rollback because this marker owns no provider resource."""
        return {}


def join_state(service: JoinService) -> JOIN.OCPJoinState:
    """Bind a mocked provider to a fresh join-aware workflow state."""
    state = JOIN.OCPJoinState()
    state.kubernetes = service
    return state


def test_client_then_serving_recollection_precedes_monitor_and_artifact_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later serving request is recollected before monitor and final cleanup."""
    selector = CSR.JoinSelector("worker-21", ("192.0.2.21", "2001:db8::21"))
    client_pending = request("client", CSR.CLIENT_SIGNER, selector)
    client_issued = replace(client_pending, approved=True, issued=True)
    serving_pending = request("serving", CSR.SERVING_SIGNER, selector)
    serving_issued = replace(serving_pending, approved=True, issued=True)
    inventories = iter(
        (
            CSR.CSRInventory(selector, (client_pending,), 1),
            CSR.CSRInventory(selector, (client_issued, serving_pending), 1),
            CSR.CSRInventory(selector, (client_issued, serving_issued), 1),
        )
    )
    known_names_seen: list[frozenset[str]] = []
    approvals: list[str] = []

    def read_inventory(
        _service: object,
        selected: CSR.JoinSelector,
        *,
        deadline: float,
        known_names: frozenset[str] = frozenset(),
    ) -> CSR.CSRInventory:
        """Return client first, then the independently recollected serving request."""
        assert selected == selector
        assert deadline > 0
        known_names_seen.append(known_names)
        return next(inventories)

    def approve(
        _service: object,
        selected: CSR.JoinSelector,
        observed: CSR.CSRObservation,
        *,
        deadline: Optional[float] = None,
    ) -> CSR.CSRObservation:
        """Record exact approvals while leaving issuance for a later collection."""
        assert selected == selector
        assert deadline is None or deadline > 0
        approvals.append(observed.name)
        return replace(observed, approved=True)

    ready = node_document(selector.node_name, selector.ip_addresses, roles=("worker",))
    service = JoinService((None, None, ready, ready))
    state = join_state(service)
    state.artifact_record_present = True
    action = JOIN.NodeJoinAction("node-build-run", selector, 30)
    release = ArtifactRelease(action, service.events)
    monkeypatch.setattr(JOIN, "read_csr_inventory", read_inventory)
    monkeypatch.setattr(JOIN, "approve_csr", approve)
    monkeypatch.setattr(
        JOIN,
        "time",
        SimpleNamespace(monotonic=time.monotonic, sleep=lambda _seconds: None),
    )
    monkeypatch.setattr(JOIN, "artifact_callbacks", lambda *_args: [release])

    JOIN.execute_join(state, action)

    assert approvals == ["client", "serving"]
    assert known_names_seen == [
        frozenset(),
        frozenset(("client",)),
        frozenset(("client", "serving")),
    ]
    assert service.commands == [
        ("adm", "node-image", "monitor", "--ip-addresses", "192.0.2.21,2001:db8::21")
    ]
    assert service.events[-3:] == ["monitor", "read-node", "artifact-release"]
    assert release.ran
    assert state.join_client_issued
    assert state.join_serving_issued
    assert state.join_node.roles == ("worker",)


def test_native_monitor_requires_explicit_serving_evidence() -> None:
    """Missing reverse DNS cannot let the native monitor bypass explicit CSR checks."""
    selector = CSR.JoinSelector("worker-22", ("192.0.2.22",))
    action = JOIN.NodeJoinAction("node-build-run", selector, 30)
    service = JoinService((None,))
    state = join_state(service)
    callback = JOIN.WaitForJoiner(action)
    callback.bind(state)

    with pytest.raises(
        KUBERNETES.KubernetesError, match="node_join_approval_incomplete"
    ):
        callback.run()

    assert service.commands == []


def test_already_ready_is_no_op_after_artifact_record_was_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeated join reads the exact Ready Node without fabricating CSR evidence."""
    selector = CSR.JoinSelector("worker-23", ("192.0.2.23",))
    ready = node_document(selector.node_name, selector.ip_addresses, roles=("infra",))
    service = JoinService((ready,))
    state = join_state(service)
    state.join_existing_ready = True
    state.artifact_record_present = False
    state.recovery_record_present = False
    action = JOIN.NodeJoinAction("cleaned-node-run", selector, 30)
    release = ArtifactRelease(action, service.events)
    monkeypatch.setattr(JOIN, "artifact_callbacks", lambda *_args: [release])

    JOIN.execute_join(state, action)

    assert release.ran
    assert service.commands == []
    assert state.join_client_issued is False
    assert state.join_serving_issued is False
    assert state.join_client_completion_source == "already_ready"
    assert state.join_node.as_dict() == {
        "name": selector.node_name,
        "uid": f"uid-{selector.node_name}",
        "ready": True,
        "roles": ["infra"],
        "ip_addresses": ["192.0.2.23"],
        "selected_addresses_present": True,
    }


@pytest.mark.parametrize(
    "reason", ("fixture_csr_read_failed", "node_join_deadline_exceeded")
)
def test_failed_or_expired_join_preserves_artifact_resources(
    reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider failure or timeout stops before artifact removal is entered."""
    selector = CSR.JoinSelector("worker-24", ("192.0.2.24",))
    service = JoinService((None,))
    state = join_state(service)
    state.artifact_record_present = True
    action = JOIN.NodeJoinAction("retained-node-run", selector, 30)
    release = ArtifactRelease(action, service.events)

    def fail_inventory(*_args: object, **_kwargs: object) -> None:
        """Expose one provider failure before CSR approval can begin."""
        raise KUBERNETES.KubernetesError(reason)

    monkeypatch.setattr(JOIN, "read_csr_inventory", fail_inventory)
    monkeypatch.setattr(JOIN, "artifact_callbacks", lambda *_args: [release])

    with pytest.raises(KUBERNETES.KubernetesError, match=reason):
        JOIN.execute_join(state, action)

    assert state.artifact_record_present
    assert not release.ran
    assert "artifact-release" not in service.events
    assert service.commands == []


def test_expired_join_loop_starts_no_new_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared timeout is checked before a new CSR collection begins."""
    selector = CSR.JoinSelector("worker-25", ("192.0.2.25",))
    service = JoinService((None,))
    state = join_state(service)
    action = JOIN.NodeJoinAction("expired-node-run", selector, 1)
    callback = JOIN.ApproveJoinRequests(action)
    callback.bind(state)
    observed_calls: list[str] = []
    moments = iter((10.0, 11.0))
    monkeypatch.setattr(
        JOIN,
        "time",
        SimpleNamespace(monotonic=lambda: next(moments), sleep=lambda _seconds: None),
    )
    monkeypatch.setattr(
        JOIN,
        "read_csr_inventory",
        lambda *_args, **_kwargs: observed_calls.append("csr-list"),
    )

    with pytest.raises(KUBERNETES.KubernetesError, match="node_join_deadline_exceeded"):
        callback.run()

    assert observed_calls == []
    assert service.commands == []


def test_ready_readback_reports_exact_hostname_roles_and_addresses() -> None:
    """Node readiness, hostname, role labels and selected IPs come from one exact read."""
    selector = CSR.JoinSelector("worker-26", ("192.0.2.26", "2001:0db8::26"))
    service = JoinService(
        (
            node_document(
                selector.node_name,
                ("2001:db8::26", "192.0.2.26", "198.51.100.26"),
                roles=("edge", "worker"),
            ),
        )
    )

    observed = JOIN.read_node(service, selector)

    assert observed is not None
    assert observed.complete
    assert observed.name == selector.node_name
    assert observed.roles == ("edge", "worker")
    assert observed.ip_addresses == (
        "192.0.2.26",
        "198.51.100.26",
        "2001:db8::26",
    )
    assert observed.selected_addresses_present


def test_ready_exact_hostname_succeeds_when_monitor_address_is_nat_only() -> None:
    """A reachable monitor IP absent from Node addresses remains truthful evidence."""
    selector = CSR.JoinSelector("worker-nat", ("203.0.113.29",))
    service = JoinService(
        (
            node_document(
                selector.node_name,
                ("10.0.0.29", "fd00::29"),
                roles=("worker",),
            ),
        )
    )

    observed = JOIN.read_node(service, selector)

    assert observed is not None
    assert observed.name == selector.node_name
    assert observed.ready
    assert observed.complete
    assert observed.ip_addresses == ("10.0.0.29", "fd00::29")
    assert observed.selected_addresses_present is False


def test_node_hostname_readback_mismatch_reports_exact_error() -> None:
    """A provider record under the requested key cannot substitute another hostname."""
    selector = CSR.JoinSelector("worker-27", ("192.0.2.27",))
    service = JoinService((node_document("worker-other", selector.ip_addresses),))

    with pytest.raises(
        KUBERNETES.KubernetesError, match="node_join_name_readback_mismatch"
    ):
        JOIN.read_node(service, selector)


def test_confirmation_mismatch_precedes_join_reads_and_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale plan digest cannot resolve CSRs, approve, monitor or release artifacts."""
    args = INVOCATION.build_parser().parse_args(
        [
            "join",
            "--run-id",
            "confirmed-node-run",
            "--node-name",
            "worker-28",
            "--ip-address",
            "192.0.2.28",
            "--apply",
            "--confirm-plan",
            "stale-digest",
        ]
    )
    invocation = ONBOARD.NodeInvocation(args)
    cluster = MemoryCluster()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        """Fail if a mutation-capable join phase is constructed before confirmation."""
        pytest.fail("stale join confirmation reached provider callbacks")

    monkeypatch.setattr(ONBOARD, "resolve_join", forbidden)
    monkeypatch.setattr(ONBOARD, "execute_join", forbidden)

    with pytest.raises(ValueError, match="confirm_plan_mismatch_rerun_plan"):
        invocation._join(INVOCATION.ActionMode.APPLY, cluster.target)

    assert cluster.applied == cluster.deleted == cluster.executed == []
