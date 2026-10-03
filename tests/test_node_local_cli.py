"""Tests for the shared node-local diagnostics CLI adapter."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from conftest import import_script_module


@pytest.fixture(autouse=True)
def _selected_existing_pods(monkeypatch, target_file):
    """Keep adapter tests on a declared target without contacting providers."""
    target_file.write_text(
        target_file.read_text(encoding="utf-8")
        + "\n[kubernetes.node_diagnostics]\n"
        + 'node = "node-a"\n'
        + "\n[kubernetes.node_diagnostics.cilium]\n"
        + 'namespace = "cilium"\nselector = "app=cilium"\n'
        + 'container = "cilium-agent"\n'
        + "\n[kubernetes.node_diagnostics.journal]\n"
        + 'namespace = "diagnostics"\nselector = "app=journal"\n'
        + 'container = "journal-reader"\n'
        + 'directory = "/host-journal"\nhost_path = "/var/log/journal"\n',
        encoding="utf-8",
    )
    cli = import_script_module("core.node_local_cli")
    load_target = import_script_module("core.target").load_target
    monkeypatch.setattr(
        cli,
        "resolve_project_target",
        lambda _target, _binding, **_kwargs: replace(
            load_target(target_file), source_file=target_file, source_kind="test"
        ),
    )
    monkeypatch.setattr(cli, "bind_sources", lambda target, **_kwargs: target)
    monkeypatch.setattr(
        cli, "check_access", lambda _target, *, cilium=False: {"status": "PASS"}
    )
    monkeypatch.setattr(cli, "access_evidence", lambda _gate: {"status": "PASS"})
    monkeypatch.setattr(cli, "select_node_pod", lambda *_args, **_kwargs: object())


def _args(**overrides: Any) -> SimpleNamespace:
    values = {
        "json": True,
        "yaml": False,
        "dry_run": False,
        "search": None,
        "classification": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _patch_collectors(monkeypatch, report: dict[str, Any] | BaseException) -> None:
    node_local = import_script_module("core.node_local")
    cli = import_script_module("core.node_local_cli")

    def collect(*_args, **_kwargs):
        if isinstance(report, BaseException):
            raise report
        return report

    monkeypatch.setattr(node_local, "collect_ceph_kernel", collect, raising=False)
    monkeypatch.setattr(cli, "collect_ceph_kernel", collect, raising=False)
    monkeypatch.setattr(node_local, "collect_cilium_node", collect, raising=False)
    monkeypatch.setattr(cli, "collect_cilium_node", collect, raising=False)


def test_node_adapter_uses_the_selected_project_binding(monkeypatch, capsys):
    """Node reads pass the project binding to the shared target resolver."""
    cli = import_script_module("core.node_local_cli")
    selected = cli.resolve_project_target
    calls = []

    def resolve(target, binding, *, dry_run, required_surfaces):
        calls.append((target, binding, dry_run, required_surfaces))
        return selected(
            target,
            binding,
            dry_run=dry_run,
            required_surfaces=required_surfaces,
        )

    monkeypatch.setattr(cli, "resolve_project_target", resolve)
    status = cli.run(
        "cilium_node",
        _args(binding="/project/.ci-skills/binding.toml", dry_run=True),
    )

    assert status == 0
    assert calls == [(None, "/project/.ci-skills/binding.toml", True, ("kubernetes",))]
    assert json.loads(capsys.readouterr().out)["status"] == "DRY_RUN"


@pytest.mark.parametrize(
    ("json_mode", "yaml_mode", "loader"),
    ((True, False, json.loads), (False, True, yaml.safe_load)),
)
def test_node_local_cli_emits_machine_readable_ceph_reports(
    monkeypatch,
    capsys,
    json_mode,
    yaml_mode,
    loader,
):
    """The shared adapter renders Ceph reports as JSON or YAML."""
    cli = import_script_module("core.node_local_cli")
    monkeypatch.setattr(cli.sys, "platform", "linux")
    report = {
        "schema_version": "1.0",
        "kind": "ceph_kernel",
        "status": "PASS",
        "records": [{"message": "libceph: mon0 connect error"}],
        "errors": [],
        "summary": {"record_count": 1, "error_count": 0},
    }
    _patch_collectors(monkeypatch, report)

    exit_status = cli.run(
        "ceph_kernel",
        _args(json=json_mode, yaml=yaml_mode),
    )
    captured = capsys.readouterr()
    data = loader(captured.out)

    assert exit_status == 0
    assert captured.err == ""
    assert data == report


def test_node_local_cli_returns_blocked_envelope_for_collector_exceptions(
    monkeypatch,
    capsys,
):
    """Collector exceptions in JSON mode still produce a structured report."""
    cli = import_script_module("core.node_local_cli")
    monkeypatch.setattr(cli.sys, "platform", "linux")
    _patch_collectors(monkeypatch, ValueError("invalid_crictl_response"))

    exit_status = cli.run("cilium_node", _args(json=True))
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert exit_status == 2
    assert captured.err == ""
    assert data["kind"] == "cilium_node"
    assert data["status"] == "BLOCKED"
    assert data["errors"] == [
        {"source": "cilium_node", "reason": "invalid_crictl_response"}
    ]


def test_node_local_cli_filters_ceph_records_by_search_and_classification(
    monkeypatch,
    capsys,
):
    """Ceph node-local output can be narrowed by text and classification."""
    cli = import_script_module("core.node_local_cli")
    monkeypatch.setattr(cli.sys, "platform", "linux")
    report = {
        "schema_version": "1.0",
        "kind": "ceph_kernel",
        "status": "PASS",
        "filters": {"since": "3 minutes ago", "message_regex": "libceph|rbd|ceph"},
        "records": [
            {
                "message": "libceph: mon0 connection refused",
                "classification": "connectivity",
                "action": "inspect_ceph_monitor_network",
            },
            {
                "message": "rbd0: blk_update_request: I/O error",
                "classification": "io_error",
                "action": "inspect_rbd_and_ceph_health",
            },
            {
                "message": "ceph: auth ticket denied",
                "classification": "authentication",
                "action": "inspect_ceph_client_auth",
            },
        ],
        "actions": [
            "inspect_ceph_client_auth",
            "inspect_ceph_monitor_network",
            "inspect_rbd_and_ceph_health",
        ],
        "errors": [],
        "summary": {"record_count": 3, "error_count": 0},
    }
    _patch_collectors(monkeypatch, report)

    exit_status = cli.run(
        "ceph_kernel",
        _args(search="mon0", classification="connectivity"),
    )
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert exit_status == 0
    assert captured.err == ""
    assert data["filters"]["search"] == "mon0"
    assert data["filters"]["classification"] == "connectivity"
    assert data["summary"] == {"record_count": 1, "error_count": 0}
    assert [row["message"] for row in data["records"]] == [
        "libceph: mon0 connection refused"
    ]
    assert data["actions"] == ["inspect_ceph_monitor_network"]


def test_node_local_cli_search_no_match_preserves_collection_status(
    monkeypatch,
    capsys,
):
    """A filter miss does not turn a partial Cilium read into a health pass."""
    cli = import_script_module("core.node_local_cli")
    monkeypatch.setattr(cli.sys, "platform", "linux")
    report = {
        "schema_version": "1.0",
        "kind": "cilium_node",
        "status": "PARTIAL",
        "records": [
            {
                "name": "cilium-agent",
                "status": "PASS",
                "findings": [{"component": "endpoint", "state": "timeout"}],
            }
        ],
        "errors": [{"source": "health", "reason": "component_unhealthy"}],
        "summary": {"record_count": 1, "error_count": 1},
    }
    _patch_collectors(monkeypatch, report)

    exit_status = cli.run("cilium_node", _args(search="no-such-peer"))
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert exit_status == 2
    assert captured.err == ""
    assert data["status"] == "PARTIAL"
    assert data["filters"] == {"search": "no-such-peer"}
    assert data["records"] == []
    assert data["errors"] == [{"source": "health", "reason": "component_unhealthy"}]
    assert data["summary"] == {"record_count": 0, "error_count": 1}


@pytest.mark.parametrize(
    ("kind", "probe_fragment"),
    (
        ("cilium_node", "Cilium status and health"),
        ("ceph_kernel", "kernel journal"),
    ),
)
def test_node_local_cli_dry_run_json_does_not_call_collectors(
    monkeypatch,
    capsys,
    kind,
    probe_fragment,
):
    """Dry run is handled by the adapter without invoking node-local commands."""
    cli = import_script_module("core.node_local_cli")

    def fail_if_called(*_args, **_kwargs):
        pytest.fail("dry-run called collector")

    monkeypatch.setattr(cli, "collect_ceph_kernel", fail_if_called, raising=False)
    monkeypatch.setattr(cli, "collect_cilium_node", fail_if_called, raising=False)

    exit_status = cli.run(kind, _args(json=True, dry_run=True))
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert exit_status == 0
    assert data["kind"] == kind
    assert data["status"] == "DRY_RUN"
    assert any(probe_fragment in probe for probe in data["probes"])


def test_node_local_cli_dry_run_records_requested_filters(capsys):
    """Dry-run keeps filter intent machine-readable without invoking collectors."""
    cli = import_script_module("core.node_local_cli")

    exit_status = cli.run(
        "ceph_kernel",
        _args(dry_run=True, search="rbd0", classification="io_error"),
    )
    data = json.loads(capsys.readouterr().out)

    assert exit_status == 0
    assert data["status"] == "DRY_RUN"
    assert data["filters"] == {"search": "rbd0", "classification": "io_error"}
    assert data["records"] == []


@pytest.mark.parametrize(
    ("mode", "loader"),
    (("--json", json.loads), ("--yaml", yaml.safe_load)),
)
def test_node_local_parser_invalid_args_emit_structured_stdout(
    capsys,
    mode,
    loader,
):
    """Argument errors keep the machine-readable stdout contract."""
    cli = import_script_module("core.node_local_cli")
    parser = cli.parser("cilium_node")

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--unknown", mode])

    captured = capsys.readouterr()
    data = loader(captured.out)

    assert exc.value.code == 2
    assert captured.err == ""
    assert data["kind"] == "cilium_node"
    assert data["status"] == "BLOCKED"
    assert data["errors"] == [{"source": "arguments", "reason": "invalid_arguments"}]


def test_node_local_parser_invalid_classification_is_structured_json(capsys):
    """Unknown Ceph classifications fail in the same machine-readable envelope."""
    cli = import_script_module("core.node_local_cli")
    parser = cli.parser("ceph_kernel")

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--classification", "not-a-class", "--json"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert exc.value.code == 2
    assert captured.err == ""
    assert data["kind"] == "ceph_kernel"
    assert data["status"] == "BLOCKED"
    assert data["errors"] == [{"source": "arguments", "reason": "invalid_arguments"}]


@pytest.mark.parametrize("kind", ("cilium_node", "ceph_kernel"))
def test_node_local_describe_needs_no_linux_runtime_or_credentials(capsys, kind):
    cli = import_script_module("core.node_local_cli")

    exit_status = cli.run(kind, SimpleNamespace(describe=True))
    contract = json.loads(capsys.readouterr().out)

    assert exit_status == 0
    assert contract["command"] == f"{kind}.py"
    assert contract["requires_authorities"] == ["kubernetes"]
    assert contract["target_protocol"]
