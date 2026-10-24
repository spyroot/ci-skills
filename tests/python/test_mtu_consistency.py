"""Physical MTU reports keep exact target, plan, and cleanup evidence.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Union

import pytest
from conftest import authenticated_kubernetes_service, import_script_module

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue
    from lib.python.core.kubernetes.mtu_consistency import MtuPlan, MtuRead
    from lib.python.core.target import Target

MTU = import_script_module("lib.python.core.kubernetes.mtu_consistency")
RUNTIME = import_script_module("lib.python.core.runtime")
TARGET = import_script_module("lib.python.core.target")


def _link(name: str, mtu: int, *, bus: str = "pci", family: str = "inet") -> JSONObject:
    """Build an ip-link record with selectable bus and address family.

    :param name: Interface name used in output records.
    :param mtu: MTU reported for the interface.
    :param bus: Parent bus used to distinguish PCI from virtual links.
    :param family: Address family used to test IPv4 extraction.
    :returns: Link mapping resembling ``ip -d -j addr show`` output.
    """
    return {
        "ifname": name,
        "mtu": mtu,
        "operstate": "UP",
        "link_type": "ether",
        "parentbus": bus,
        "parentdev": "0000:0a:00.0",
        "addr_info": [
            {
                "family": family,
                "local": "2001:db8::10" if family == "inet6" else "192.0.2.10",
            }
        ],
    }


def _target(tmp_path: Path) -> Target:
    """Build a Kubernetes-only target pinned to a fixture context/file.

    :param tmp_path: Directory receiving the target TOML path.
    :returns: Target carrying selected cluster and kubeconfig references.
    """
    path = tmp_path / "target.toml"
    path.write_text(
        '[kubernetes]\ncontext = "selected"\n'
        'server = "https://api.example.test:6443"\n',
        encoding="utf-8",
    )
    return TARGET.Target(
        None,
        None,
        TARGET.KubernetesTarget(
            "selected", "https://api.example.test:6443", tmp_path / "config"
        ),
        active_surfaces=("kubernetes",),
        source_file=path,
        source_kind="env:CI_SKILLS_TARGET",
        target_reference=f"cli:{path}",
    )


def _args(
    **overrides: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
) -> SimpleNamespace:
    """Build parsed-command-shaped MTU options with selected overrides.

    :param **overrides: Field overrides applied to the default test namespace.
    :returns: Namespace-shaped options used by plan and collection tests.

    """
    values = {
        "target": None,
        "binding": None,
        "describe": False,
        "dry_run": False,
        "apply": False,
        "confirm_plan": None,
        "node": None,
        "timeout": 90,
        "run_id": "unit-run",
        "log_file": None,
        "log_format": "text",
        "log_level": "error",
        "revision": None,
        "json": True,
        "yaml": False,
        "human": False,
        "output_dir": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _plan() -> MtuPlan:
    """Build a two-node MTU plan with a fixed digest and host command.

    :returns: Plan selecting diagnostics namespace and both fixture nodes.
    """
    return MTU.MtuPlan(
        "diagnostics",
        ("node-a", "node-b"),
        "a" * 64,
        {
            "selection": "PCI Ethernet with nonempty parentdev and no virtual kind",
            "host_command": ["chroot", "/host", "ip", "-d", "-j", "addr", "show"],
        },
    )


def test_physical_uplinks_include_pci_links_without_ipv4() -> None:
    """Keep PCI Ethernet without IPv4 while excluding virtual and veth links.
    :returns: None; assertions in the body validate the regression.
    """
    payload = [
        _link("eno1", 9000),
        _link("cilium_vxlan", 1450, bus="virtual"),
        _link("eno2", 9000, family="inet6"),
        {**_link("eno3", 9000), "addr_info": []},
        {**_link("veth0", 1500), "linkinfo": {"info_kind": "veth"}},
    ]

    assert MTU.physical_uplinks(payload) == [
        {
            "interface": "eno1",
            "mtu": 9000,
            "oper_state": "UP",
            "parent_bus": "pci",
            "pci_device": "0000:0a:00.0",
            "ipv4_addresses": ["192.0.2.10"],
        },
        {
            "interface": "eno2",
            "mtu": 9000,
            "oper_state": "UP",
            "parent_bus": "pci",
            "pci_device": "0000:0a:00.0",
            "ipv4_addresses": [],
        },
        {
            "interface": "eno3",
            "mtu": 9000,
            "oper_state": "UP",
            "parent_bus": "pci",
            "pci_device": "0000:0a:00.0",
            "ipv4_addresses": [],
        },
    ]


@pytest.mark.parametrize(
    "payload", ({}, None, [_link("eno1", 9000) | {"addr_info": {}}])
)
def test_malformed_link_envelopes_fail_instead_of_reporting_empty_pass(
    payload: JSONValue,
) -> None:
    """Reject malformed link envelopes instead of claiming no uplinks.

    :param payload: Invalid root or address-list shape supplied to the parser.

    :returns: None; assertions in the body validate the regression.
    """
    with pytest.raises(TypeError):
        MTU.physical_uplinks(payload)


def test_node_command_pins_kubeconfig_context_and_non_tty_debug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Require oc debug to pin target file/context without TTY or SSH.

    :param tmp_path: Directory used for target and kubeconfig paths.
    :param monkeypatch: Pytest fixture replacing the debug command runner.

    :returns: None; assertions in the body validate the regression.
    """
    target = _target(tmp_path)
    seen = []

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> RUNTIME.CommandResult:
        """Record the exact oc debug argv and return one physical uplink.

        :param argv: Native debug command requested for the selected node.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Successful command result containing JSON link data.

        """
        seen.append(tuple(argv))
        return RUNTIME.CommandResult(
            tuple(argv), 0, json.dumps([_link("eno1", 9000)]), ""
        )

    monkeypatch.setattr(MTU, "run_command_tail", fake_run)
    observation = MTU._read_one(target, _plan(), "node-a", "unit-marker", 90)

    assert observation.errors == []
    assert observation.records[0]["mtu"] == 9000
    assert seen == [
        (
            "oc",
            "--kubeconfig",
            str(tmp_path / "config"),
            "--context",
            "selected",
            "debug",
            "node/node-a",
            "CI_SKILLS_MTU_RUN_ID=unit-marker",
            "--quiet",
            "--to-namespace=diagnostics",
            "--",
            "chroot",
            "/host",
            "ip",
            "-d",
            "-j",
            "addr",
            "show",
        )
    ]
    assert "ssh" not in seen[0]


def test_non_openshift_target_blocks_before_debug_pod_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject absent OpenShift API before choosing nodes or creating debug Pods.

    :param tmp_path: Directory used for the selected target.
    :param monkeypatch: Pytest fixture replacing cluster probes and node read.

    :returns: None; assertions in the body validate the regression.
    """
    target = _target(tmp_path)
    service = authenticated_kubernetes_service(target)
    target = service.target
    monkeypatch.setattr(MTU.shutil, "which", lambda _name: "/fake/oc")
    monkeypatch.setattr(
        MTU,
        "run_command_tail",
        lambda argv, **_kwargs: RUNTIME.CommandResult(
            tuple(argv), 1, "", "the server returned 404 Not Found"
        ),
    )
    monkeypatch.setattr(
        MTU,
        "_read_nodes",
        lambda *_args: pytest.fail("node debug planning continued on non-OpenShift"),
    )

    with pytest.raises(ValueError, match="cluster:openshift_api_absent"):
        MTU.build_plan(target, _args(), state=service.state)


def test_mtu_mismatch_is_partial_with_action_and_verified_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report an inter-node MTU mismatch as partial after verified cleanup.

    :param tmp_path: Directory used for the selected target.
    :param monkeypatch: Pytest fixture returning differing node link data.

    :returns: None; assertions in the body validate the regression.
    """
    target = _target(tmp_path)
    service = authenticated_kubernetes_service(target)
    target = service.target
    monkeypatch.setattr(
        MTU,
        "_read_one",
        lambda _target, _plan, node, _marker, _timeout: MTU.MtuRead(
            [
                {
                    "node": node,
                    **MTU.physical_uplinks(
                        [_link("eno1", 9000 if node == "node-a" else 1500)]
                    )[0],
                }
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        MTU,
        "_cleanup",
        lambda *_args: {"status": "PASS", "verified": True, "remaining_pods": []},
    )

    result = MTU.collect_mtu_consistency(
        target, _args(), _plan(), "unit-marker", state=service.state
    )

    assert result["status"] == "PARTIAL"
    assert result["condition"] == "INCONSISTENT"
    assert result["summary"]["nodes_read"] == 2
    assert result["summary"]["mtu_values"] == [1500, 9000]
    assert result["findings"][0]["code"] == "physical_mtu_mismatch"


def test_identical_multi_nic_nodes_are_consistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compare MTU by interface, allowing distinct NIC sizes across nodes.

    :param tmp_path: Directory used for the selected target.
    :param monkeypatch: Pytest fixture returning matching per-NIC node data.

    :returns: None; assertions in the body validate the regression.
    """
    target = _target(tmp_path)
    service = authenticated_kubernetes_service(target)
    target = service.target

    def fake_read(
        _target: Target, _plan: MtuPlan, node: str, _marker: str, _timeout: int
    ) -> MtuRead:
        """Return matching two-interface inventory for each selected node.

        :param _target: Selected cluster target accepted by the double.
        :param _plan: MTU plan accepted by the double.
        :param node: Node name attached to each returned link.
        :param _marker: Debug-run marker accepted by the double.
        :param _timeout: Read timeout accepted by the double.
        :returns: Node-labeled read observation with no errors.
        """
        links = MTU.physical_uplinks([_link("eno1", 9000), _link("eno2", 1500)])
        return MTU.MtuRead([{"node": node, **link} for link in links], [])

    monkeypatch.setattr(MTU, "_read_one", fake_read)
    monkeypatch.setattr(
        MTU,
        "_cleanup",
        lambda *_args: {"status": "PASS", "verified": True, "remaining_pods": []},
    )

    result = MTU.collect_mtu_consistency(
        target, _args(), _plan(), "unit-marker", state=service.state
    )

    assert result["status"] == "PASS"
    assert result["condition"] == "CONSISTENT"
    assert result["summary"]["mtu_values"] == [1500, 9000]
    assert result["summary"]["mtu_by_interface"] == {
        "eno1": [9000],
        "eno2": [1500],
    }
    assert result["findings"] == []


def test_cleanup_failure_blocks_even_when_nic_reads_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Block an otherwise healthy MTU report when debug Pods remain.

    :param tmp_path: Directory used for the selected target.
    :param monkeypatch: Pytest fixture simulating healthy reads and failed cleanup.

    :returns: None; assertions in the body validate the regression.
    """
    target = _target(tmp_path)
    service = authenticated_kubernetes_service(target)
    target = service.target
    monkeypatch.setattr(
        MTU,
        "_read_one",
        lambda _target, _plan, node, _marker, _timeout: MTU.MtuRead(
            [{"node": node, **MTU.physical_uplinks([_link("eno1", 9000)])[0]}],
            [],
        ),
    )
    monkeypatch.setattr(
        MTU,
        "_cleanup",
        lambda *_args: {
            "status": "BLOCKED",
            "verified": False,
            "remaining_pods": ["node-a-debug"],
        },
    )

    result = MTU.collect_mtu_consistency(
        target, _args(), _plan(), "unit-marker", state=service.state
    )

    assert result["status"] == "BLOCKED"
    assert result["condition"] == "UNKNOWN"
    assert result["summary"]["consistent"] is False


def test_cleanup_deletes_only_this_runs_marked_pod_and_reads_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delete only the marked debug Pod and verify it is absent afterward.

    :param tmp_path: Directory used for target and kubeconfig paths.
    :param monkeypatch: Pytest fixture returning before/after Pod lists.

    :returns: None; assertions in the body validate the regression.
    """
    target = _target(tmp_path)
    observed = iter([["node-a-debug"], []])
    calls = []
    monkeypatch.setattr(MTU, "_marked_pods", lambda *_args: next(observed))

    def fake_run(
        argv: Sequence[str],
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> RUNTIME.CommandResult:
        """Record the marked Pod deletion and return native success.

        :param argv: kubectl delete command with selected namespace and context.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Successful result reporting Pod deletion.

        """
        calls.append(tuple(argv))
        return RUNTIME.CommandResult(tuple(argv), 0, "pod deleted", "")

    monkeypatch.setattr(MTU, "run_command_tail", fake_run)

    result = MTU._cleanup(target, "diagnostics", "this-run")

    assert result["verified"] is True
    assert result["remaining_pods"] == []
    assert calls == [
        (
            "kubectl",
            "--kubeconfig",
            str(tmp_path / "config"),
            "--context",
            "selected",
            "-n",
            "diagnostics",
            "delete",
            "pod",
            "node-a-debug",
            "--ignore-not-found=true",
            "--wait=true",
            "--timeout=30s",
        )
    ]


def test_marked_pod_readback_ignores_other_debug_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Filter debug Pods by the selected run marker, preserving other runs.

    :param tmp_path: Directory used for the selected target.
    :param monkeypatch: Pytest fixture supplying mixed-run Pod JSON.

    :returns: None; assertions in the body validate the regression.
    """
    target = _target(tmp_path)

    def pod(name: str, marker: str) -> JSONObject:
        """Build a debug Pod with its own run marker.

        :param name: Pod name returned in marked read-back.
        :param marker: CI_SKILLS_MTU_RUN_ID value in the debugger container.
        :returns: Minimal Pod mapping for the namespace list fixture.
        """
        return {
            "metadata": {"name": name},
            "spec": {
                "containers": [
                    {
                        "name": "debugger",
                        "env": [{"name": "CI_SKILLS_MTU_RUN_ID", "value": marker}],
                    }
                ]
            },
        }

    payload = {"items": [pod("our-debug", "our-run"), pod("other-debug", "other-run")]}
    monkeypatch.setattr(
        MTU,
        "run_command_tail",
        lambda argv, **_kwargs: RUNTIME.CommandResult(
            tuple(argv), 0, json.dumps(payload), ""
        ),
    )

    assert MTU._marked_pods(target, "diagnostics", "our-run") == ["our-debug"]


def test_timeout_keeps_partial_evidence_and_still_verifies_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retain one node's MTU data when another times out, then clean up.

    :param tmp_path: Directory used for the selected target.
    :param monkeypatch: Pytest fixture simulating mixed node results.

    :returns: None; assertions in the body validate the regression.
    """
    target = _target(tmp_path)
    service = authenticated_kubernetes_service(target)
    target = service.target
    called = []

    def fake_read(
        _target: Target, _plan: MtuPlan, node: str, _marker: str, _timeout: int
    ) -> MtuRead:
        """Return node-a data and a node-b timeout error.

        :param _target: Selected cluster target accepted by the double.
        :param _plan: MTU plan accepted by the double.
        :param node: Selected node determining success or timeout.
        :param _marker: Debug-run marker accepted by the double.
        :param _timeout: Read timeout accepted by the double.
        :returns: Node read observation for the selected branch.
        """
        if node == "node-a":
            return MTU.MtuRead(
                [
                    {
                        "node": node,
                        **MTU.physical_uplinks([_link("eno1", 9000)])[0],
                    }
                ],
                [],
            )
        return MTU.MtuRead(
            [], [{"source": "node/node-b", "reason": "node/node-b:timeout"}]
        )

    def fake_cleanup(*_args: str) -> JSONObject:
        """Record cleanup after a node timeout and claim verified removal.

        :param *_args: Ignored positional arguments accepted by the fake boundary.
        :returns: Passing cleanup record with no remaining Pods.

        """
        called.append("cleanup")
        return {"status": "PASS", "verified": True, "remaining_pods": []}

    monkeypatch.setattr(MTU, "_read_one", fake_read)
    monkeypatch.setattr(MTU, "_cleanup", fake_cleanup)

    result = MTU.collect_mtu_consistency(
        target, _args(), _plan(), "unit-marker", state=service.state
    )

    assert called == ["cleanup"]
    assert result["status"] == "PARTIAL"
    assert result["summary"]["nodes_read"] == 1
    assert result["errors"] == [
        {"source": "node/node-b", "reason": "node/node-b:timeout"}
    ]


def test_interrupted_debug_still_runs_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run cleanup under ignored signals and restore original handlers.

    :param tmp_path: Directory used for the selected target.
    :param monkeypatch: Pytest fixture replacing read and cleanup operations.

    :returns: None; assertions in the body validate the regression.
    """
    target = _target(tmp_path)
    service = authenticated_kubernetes_service(target)
    target = service.target
    called = []
    original_int = MTU.signal.getsignal(MTU.signal.SIGINT)
    original_term = MTU.signal.getsignal(MTU.signal.SIGTERM)

    def interrupted(*_args: str) -> None:
        """Interrupt a node read before returning link data.

        :param *_args: Ignored positional arguments accepted by the fake boundary.
        :returns: None; the helper records behavior through its side effects.
        :raises MTU.Interrupted: Debug collection is interrupted.

        """
        raise MTU.Interrupted("interrupted")

    def fake_cleanup(*_args: str) -> JSONObject:
        """Verify signal masking during cleanup after interruption.

        :param *_args: Ignored positional arguments accepted by the fake boundary.
        :returns: Passing cleanup record with no remaining Pods.

        """
        assert MTU.signal.getsignal(MTU.signal.SIGINT) == MTU.signal.SIG_IGN
        assert MTU.signal.getsignal(MTU.signal.SIGTERM) == MTU.signal.SIG_IGN
        called.append("cleanup")
        return {"status": "PASS", "verified": True, "remaining_pods": []}

    monkeypatch.setattr(MTU, "_read_one", interrupted)
    monkeypatch.setattr(MTU, "_cleanup", fake_cleanup)

    result = MTU.collect_mtu_consistency(
        target, _args(), _plan(), "unit-marker", state=service.state
    )

    assert called == ["cleanup"]
    assert result["status"] == "BLOCKED"
    assert {"source": "run", "reason": "interrupted"} in result["errors"]
    assert result["debug_marker"] == "unit-marker"
    assert MTU.signal.getsignal(MTU.signal.SIGINT) == original_int
    assert MTU.signal.getsignal(MTU.signal.SIGTERM) == original_term


def test_default_plan_and_mismatched_confirmation_never_launch_debug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Require an exact plan digest before launching a node debug Pod.

    :param tmp_path: Directory used for the selected target.
    :param monkeypatch: Pytest fixture replacing access, plan, and collector.
    :param capsys: Pytest fixture capturing planned and denied machine reports.

    :returns: None; assertions in the body validate the regression.
    """
    target = _target(tmp_path)
    monkeypatch.setattr(MTU, "resolve_target", lambda *_args, **_kwargs: target)
    monkeypatch.setattr(MTU, "bind_sources", lambda target, **_kwargs: target)

    def base_kubernetes_access(
        _target: Target, *, cilium: bool, state: MTU.DeliveryState
    ) -> JSONObject:
        """Allow base cluster access without requiring a Cilium probe.

        :param _target: Selected cluster target accepted by the fake gate.
        :param cilium: Must be false for the MTU-only access path.
        :param state: Actual invocation receiving the preceding identity observation.
        :returns: Passing base Kubernetes access result.
        """
        assert cilium is False
        authenticated_kubernetes_service(_target, state=state)
        return {"status": "PASS"}

    monkeypatch.setattr(MTU, "check_access", base_kubernetes_access)
    monkeypatch.setattr(MTU, "build_plan", lambda *_args, **_kwargs: _plan())
    monkeypatch.setattr(
        MTU,
        "collect_mtu_consistency",
        lambda *_args: pytest.fail("oc debug ran without an exact plan digest"),
    )

    assert MTU.run(_args()) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["status"] == "DRY_RUN"
    assert planned["plan_digest"] == "a" * 64

    assert MTU.run(_args(apply=True, confirm_plan="b" * 64)) == 2
    denied = json.loads(capsys.readouterr().out)
    assert denied["status"] == "BLOCKED"
    assert denied["errors"] == [{"source": "plan", "reason": "plan_digest_mismatch"}]


def test_plan_digest_changes_when_selected_nodes_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bind the plan digest to the exact set of selected nodes.

    :param tmp_path: Directory used for the selected target.
    :param monkeypatch: Pytest fixture varying node selection between plans.

    :returns: None; assertions in the body validate the regression.
    """
    target = _target(tmp_path)
    service = authenticated_kubernetes_service(target)
    target = service.target
    monkeypatch.setattr(MTU, "_verify_openshift", lambda _target: None)
    monkeypatch.setattr(MTU, "_read_namespace", lambda _target: "diagnostics")
    monkeypatch.setattr(MTU, "_read_nodes", lambda *_args: ("node-a", "node-b"))
    first = MTU.build_plan(target, _args(), state=service.state)
    monkeypatch.setattr(MTU, "_read_nodes", lambda *_args: ("node-a",))
    second = MTU.build_plan(target, _args(), state=service.state)

    assert first.digest != second.digest
