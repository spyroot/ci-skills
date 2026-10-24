"""Tests for the shared node-local diagnostics CLI adapter.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, MutableSequence, Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Optional, Union

import pytest
import yaml
from conftest import import_script_module

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue
    from lib.python.core.target import Target


@pytest.fixture(autouse=True)
def _selected_existing_pods(monkeypatch: pytest.MonkeyPatch, target_file: Path) -> None:
    """Keep CLI tests on declared routes without provider calls.

    :param monkeypatch: Pytest fixture replacing resolution, access, and Pod selection.
    :param target_file: Fixture target extended with existing-Pod routes.

    :returns: None; the helper records behavior through its side effects.
    """
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
    cli = import_script_module("lib.python.core.kubernetes.node_local_cli")
    load_target = import_script_module("lib.python.core.target").load_target
    monkeypatch.setattr(
        cli,
        "resolve_project_target",
        lambda _target, _binding, **_kwargs: replace(
            load_target(target_file), source_file=target_file, source_kind="test"
        ),
    )
    monkeypatch.setattr(cli, "bind_sources", lambda target, **_kwargs: target)
    monkeypatch.setattr(
        cli,
        "check_access",
        lambda _target, *, cilium=False, state=None: {"status": "PASS"},
    )
    monkeypatch.setattr(cli, "access_evidence", lambda _gate: {"status": "PASS"})
    monkeypatch.setattr(cli, "select_node_pod", lambda *_args, **_kwargs: object())


def _args(**overrides: JSONValue) -> SimpleNamespace:
    """Build node-local command options with selected overrides.

    :param **overrides: Field overrides applied to the default test namespace.
    :returns: Namespace passed to the shared node CLI adapter.

    """
    values = {
        "json": True,
        "yaml": False,
        "dry_run": False,
        "search": None,
        "classification": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _patch_collectors(
    monkeypatch: pytest.MonkeyPatch, report: Union[JSONObject, BaseException]
) -> None:
    """Make both node collectors return one report or raise one failure.

    :param monkeypatch: Pytest fixture replacing collector entrypoints.
    :param report: Structured collector result or exception to raise.

    :returns: None; the helper records behavior through its side effects.
    """
    node_local = import_script_module("lib.python.core.kubernetes.node_local")
    cli = import_script_module("lib.python.core.kubernetes.node_local_cli")

    def collect(
        *_args: str,
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> JSONObject:
        """Return or raise the fixture selected by the adapter test.

        :param *_args: Ignored positional arguments accepted by the fake boundary.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: Structured fixture report when no exception was selected.
        :raises BaseException: Configured collector failure supplied by the test.
        """  # noqa: DOC503 - The dynamic fixture exception is raised verbatim.
        if isinstance(report, BaseException):
            raise report
        return report

    monkeypatch.setattr(node_local, "collect_ceph_kernel", collect, raising=False)
    monkeypatch.setattr(cli, "collect_ceph_kernel", collect, raising=False)
    monkeypatch.setattr(node_local, "collect_cilium_node", collect, raising=False)
    monkeypatch.setattr(cli, "collect_cilium_node", collect, raising=False)


def test_node_adapter_uses_the_selected_project_binding(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pass the binding path to shared target resolution in dry-run.

    :param monkeypatch: Pytest fixture recording the resolver call.
    :param capsys: Pytest fixture capturing the planned report.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.kubernetes.node_local_cli")
    selected = cli.resolve_project_target
    calls: MutableSequence[
        tuple[Optional[str], Optional[str], bool, Sequence[str]]
    ] = []

    def resolve(
        target: Optional[str],
        binding: Optional[str],
        *,
        dry_run: bool,
        required_surfaces: Sequence[str],
    ) -> Target:
        """Record target/binding selection then delegate to fixture resolver.

        :param target: Explicit target path passed by the adapter.
        :param binding: Selected project binding path.
        :param dry_run: Whether the resolver is planning without live access.
        :param required_surfaces: Authority set requested by the node adapter.
        :returns: Resolved target from the prepatched fixture function.
        """
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
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    json_mode: bool,
    yaml_mode: bool,
    loader: Callable[[str], JSONObject],
) -> None:
    """Render the same Ceph collector report in JSON or YAML.

    :param monkeypatch: Pytest fixture replacing platform and collector.
    :param capsys: Pytest fixture capturing the machine report.
    :param json_mode: Whether JSON rendering is selected.
    :param yaml_mode: Whether YAML rendering is selected.
    :param loader: Parser paired with the selected output mode.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.kubernetes.node_local_cli")
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
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Turn collector ValueError into structured blocked JSON.

    :param monkeypatch: Pytest fixture replacing platform and collector.
    :param capsys: Pytest fixture capturing the blocked report.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.kubernetes.node_local_cli")
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
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Narrow Ceph output to matching text/classification and actions.

    :param monkeypatch: Pytest fixture replacing platform and collector.
    :param capsys: Pytest fixture capturing filtered report.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.kubernetes.node_local_cli")
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
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep PARTIAL and errors when a search removes all Cilium rows.

    :param monkeypatch: Pytest fixture replacing platform and collector.
    :param capsys: Pytest fixture capturing the filtered report.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.kubernetes.node_local_cli")
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
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    probe_fragment: str,
) -> None:
    """Publish node command probe intent without invoking collectors.

    :param monkeypatch: Pytest fixture rejecting collector calls.
    :param capsys: Pytest fixture capturing dry-run JSON.
    :param kind: Cilium or Ceph node command selected.
    :param probe_fragment: Required human-readable planned probe text.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.kubernetes.node_local_cli")

    def fail_if_called(
        *_args: str,
        **_kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> None:
        """Abort if dry-run invokes a node-local collector.


        :param *_args: Ignored positional arguments accepted by the fake boundary.
        :param **_kwargs: Ignored keyword arguments accepted by the fake boundary.
        :returns: None; the helper records behavior through its side effects.
        """
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


def test_node_local_cli_dry_run_records_requested_filters(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Retain search and classification intent in dry-run output.

    :param capsys: Pytest fixture capturing the planned report.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.kubernetes.node_local_cli")

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
    capsys: pytest.CaptureFixture[str],
    mode: str,
    loader: Callable[[str], JSONObject],
) -> None:
    """Render invalid node options as JSON/YAML usage results.

    :param capsys: Pytest fixture capturing parser output.
    :param mode: Machine output flag selected with invalid option.
    :param loader: Parser paired with the selected output mode.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.kubernetes.node_local_cli")
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


def test_node_local_parser_invalid_classification_is_structured_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject an unknown Ceph classification in a JSON usage envelope.

    :param capsys: Pytest fixture capturing parser output.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.kubernetes.node_local_cli")
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
def test_node_local_describe_needs_no_linux_runtime_or_credentials(
    capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    """Describe node CLI contract before checking runtime or credentials.

    :param capsys: Pytest fixture capturing the described contract.
    :param kind: Cilium or Ceph node command selected.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.kubernetes.node_local_cli")

    exit_status = cli.run(kind, SimpleNamespace(describe=True))
    contract = json.loads(capsys.readouterr().out)

    assert exit_status == 0
    assert contract["command"] == f"{kind}.py"
    assert contract["requires_authorities"] == ["kubernetes"]
    assert contract["target_protocol"]
