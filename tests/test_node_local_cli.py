"""Tests for the shared node-local diagnostics CLI adapter."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from conftest import import_script_module


def _args(**overrides: Any) -> SimpleNamespace:
    values = {
        "json": True,
        "yaml": False,
        "dry_run": False,
        "search": None,
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
        {"source": "cilium_node", "reason": "node_collection_failed"}
    ]


@pytest.mark.parametrize(
    ("kind", "probe_fragment"),
    (
        ("cilium_node", "crictl ps --output json"),
        ("ceph_kernel", "journalctl -k"),
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
        parser.parse_args(["--target", "target.toml", mode])

    captured = capsys.readouterr()
    data = loader(captured.out)

    assert exc.value.code == 2
    assert captured.err == ""
    assert data["kind"] == "cilium_node"
    assert data["status"] == "BLOCKED"
    assert data["errors"] == [{"source": "arguments", "reason": "invalid_arguments"}]


@pytest.mark.parametrize("kind", ("cilium_node", "ceph_kernel"))
def test_node_local_describe_needs_no_linux_runtime_or_credentials(capsys, kind):
    cli = import_script_module("core.node_local_cli")

    exit_status = cli.run(kind, SimpleNamespace(describe=True))
    contract = json.loads(capsys.readouterr().out)

    assert exit_status == 0
    assert contract["command"] == f"{kind}.py"
    assert contract["requires_authorities"] == []
    assert contract["target_protocol"] is None
