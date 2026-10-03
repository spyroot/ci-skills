"""Physical MTU reports keep exact target, plan, and cleanup evidence."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from conftest import import_script_module

MTU = import_script_module("core.mtu_consistency")
RUNTIME = import_script_module("core.runtime")
TARGET = import_script_module("core.target")


def _link(name: str, mtu: int, *, bus: str = "pci", family: str = "inet"):
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


def _target(tmp_path):
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
        source_kind="argv:--target",
        target_reference=f"cli:{path}",
    )


def _args(**overrides):
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


def _plan():
    return MTU.MtuPlan(
        "diagnostics",
        ("node-a", "node-b"),
        "a" * 64,
        {"selection": "PCI Ethernet with IPv4"},
    )


def test_physical_uplinks_include_pci_links_without_ipv4():
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
def test_malformed_link_envelopes_fail_instead_of_reporting_empty_pass(payload):
    with pytest.raises(TypeError):
        MTU.physical_uplinks(payload)


def test_node_command_pins_kubeconfig_context_and_non_tty_debug(tmp_path, monkeypatch):
    target = _target(tmp_path)
    seen = []

    def fake_run(argv, **_kwargs):
        seen.append(tuple(argv))
        return RUNTIME.CommandResult(
            tuple(argv), 0, json.dumps([_link("eno1", 9000)]), ""
        )

    monkeypatch.setattr(MTU, "run_command_tail", fake_run)
    links, error = MTU._read_one(target, _plan(), "node-a", "unit-marker", 90)

    assert error is None
    assert links[0]["mtu"] == 9000
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


def test_non_openshift_target_blocks_before_debug_pod_creation(tmp_path, monkeypatch):
    target = _target(tmp_path)
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
        MTU.build_plan(target, _args())


def test_mtu_mismatch_is_partial_with_action_and_verified_cleanup(
    tmp_path, monkeypatch
):
    target = _target(tmp_path)
    monkeypatch.setattr(
        MTU,
        "_read_one",
        lambda _target, _plan, node, _marker, _timeout: (
            [
                {
                    "node": node,
                    **MTU.physical_uplinks(
                        [_link("eno1", 9000 if node == "node-a" else 1500)]
                    )[0],
                }
            ],
            None,
        ),
    )
    monkeypatch.setattr(
        MTU,
        "_cleanup",
        lambda *_args: {"status": "PASS", "verified": True, "remaining_pods": []},
    )

    result = MTU.collect_mtu_consistency(target, _args(), _plan(), "unit-marker")

    assert result["status"] == "PARTIAL"
    assert result["condition"] == "INCONSISTENT"
    assert result["summary"]["nodes_read"] == 2
    assert result["summary"]["mtu_values"] == [1500, 9000]
    assert result["findings"][0]["code"] == "physical_mtu_mismatch"


def test_cleanup_failure_blocks_even_when_nic_reads_succeed(tmp_path, monkeypatch):
    target = _target(tmp_path)
    monkeypatch.setattr(
        MTU,
        "_read_one",
        lambda _target, _plan, node, _marker, _timeout: (
            [{"node": node, **MTU.physical_uplinks([_link("eno1", 9000)])[0]}],
            None,
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

    result = MTU.collect_mtu_consistency(target, _args(), _plan(), "unit-marker")

    assert result["status"] == "BLOCKED"
    assert result["condition"] == "UNKNOWN"
    assert result["summary"]["consistent"] is False


def test_cleanup_deletes_only_this_runs_marked_pod_and_reads_back(
    tmp_path, monkeypatch
):
    target = _target(tmp_path)
    observed = iter([["node-a-debug"], []])
    calls = []
    monkeypatch.setattr(MTU, "_marked_pods", lambda *_args: next(observed))

    def fake_run(argv, **_kwargs):
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


def test_marked_pod_readback_ignores_other_debug_runs(tmp_path, monkeypatch):
    target = _target(tmp_path)

    def pod(name, marker):
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
    tmp_path, monkeypatch
):
    target = _target(tmp_path)
    called = []

    def fake_read(_target, _plan, node, _marker, _timeout):
        if node == "node-a":
            return [
                {"node": node, **MTU.physical_uplinks([_link("eno1", 9000)])[0]}
            ], None
        return [], {"source": "node/node-b", "reason": "node/node-b:timeout"}

    def fake_cleanup(*_args):
        called.append("cleanup")
        return {"status": "PASS", "verified": True, "remaining_pods": []}

    monkeypatch.setattr(MTU, "_read_one", fake_read)
    monkeypatch.setattr(MTU, "_cleanup", fake_cleanup)

    result = MTU.collect_mtu_consistency(target, _args(), _plan(), "unit-marker")

    assert called == ["cleanup"]
    assert result["status"] == "PARTIAL"
    assert result["summary"]["nodes_read"] == 1
    assert result["errors"] == [
        {"source": "node/node-b", "reason": "node/node-b:timeout"}
    ]


def test_interrupted_debug_still_runs_cleanup(tmp_path, monkeypatch):
    target = _target(tmp_path)
    called = []

    def interrupted(*_args):
        raise MTU.Interrupted("interrupted")

    def fake_cleanup(*_args):
        called.append("cleanup")
        return {"status": "PASS", "verified": True, "remaining_pods": []}

    monkeypatch.setattr(MTU, "_read_one", interrupted)
    monkeypatch.setattr(MTU, "_cleanup", fake_cleanup)

    result = MTU.collect_mtu_consistency(target, _args(), _plan(), "unit-marker")

    assert called == ["cleanup"]
    assert result["status"] == "BLOCKED"
    assert {"source": "run", "reason": "interrupted"} in result["errors"]


def test_default_plan_and_mismatched_confirmation_never_launch_debug(
    tmp_path, monkeypatch, capsys
):
    target = _target(tmp_path)
    monkeypatch.setattr(MTU, "resolve_target", lambda *_args, **_kwargs: target)
    monkeypatch.setattr(MTU, "bind_sources", lambda target, **_kwargs: target)

    def base_kubernetes_access(_target, *, cilium):
        assert cilium is False
        return {"status": "PASS"}

    monkeypatch.setattr(MTU, "check_access", base_kubernetes_access)
    monkeypatch.setattr(MTU, "build_plan", lambda *_args: _plan())
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


def test_plan_digest_changes_when_selected_nodes_change(tmp_path, monkeypatch):
    target = _target(tmp_path)
    monkeypatch.setattr(MTU, "_verify_openshift", lambda _target: None)
    monkeypatch.setattr(MTU, "_read_namespace", lambda _target: "diagnostics")
    monkeypatch.setattr(MTU, "_read_nodes", lambda *_args: ("node-a", "node-b"))
    first = MTU.build_plan(target, _args())
    monkeypatch.setattr(MTU, "_read_nodes", lambda *_args: ("node-a",))
    second = MTU.build_plan(target, _args())

    assert first.digest != second.digest
