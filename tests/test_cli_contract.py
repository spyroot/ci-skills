"""CLI contract tests for every public diagnostics command."""

from __future__ import annotations

import argparse
import json

import pytest
import yaml
from conftest import (
    import_script_module,
    install_executable,
    parse_json_output,
    read_journal,
    run_script,
)

SCRIPT_CASES = (
    ("access_check.py", ()),
    (
        "gitlab_job.py",
        ("--job-url", "https://gitlab.example.test/unit/repo/-/jobs/123"),
    ),
    ("storage_report.py", ()),
    ("event_trace.py", ()),
    ("cilium_status.py", ()),
)


@pytest.mark.parametrize(
    ("script_name", "specific_flags"),
    (
        ("access_check.py", ("--publication",)),
        ("gitlab_job.py", ("--job-url", "--search")),
        (
            "storage_report.py",
            ("--namespace", "--node", "--storage-class", "--phase", "--search"),
        ),
        (
            "event_trace.py",
            (
                "--from",
                "--to",
                "--namespace",
                "--kind",
                "--object",
                "--reason",
                "--search",
            ),
        ),
        ("cilium_status.py", ("--namespace", "--node", "--search")),
    ),
)
def test_help_lists_common_and_script_specific_flags(script_name, specific_flags):
    """Help output is dependency-free and documents every supported argument."""
    result = run_script(script_name, "--help")

    assert result.returncode == 0
    for flag in ("--target", "--revision", "--json", "--yaml", "--dry-run", "--help"):
        assert flag in result.stdout
    for flag in specific_flags:
        assert flag in result.stdout


@pytest.mark.parametrize(("script_name", "extra_args"), SCRIPT_CASES)
def test_dry_run_json_lists_access_probes_without_external_calls(
    fake_bin,
    call_journal,
    target_file,
    script_name,
    extra_args,
):
    """Dry run is a local explanation mode and cannot contact live tools."""
    body = (
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "journal = pathlib.Path(os.environ['FAKE_JOURNAL'])\n"
        "with journal.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps({'tool': pathlib.Path(sys.argv[0]).name, 'argv': sys.argv[1:]}) + '\\n')\n"
        "raise SystemExit(99)\n"
    )
    for tool in ("gh", "glab", "kubectl"):
        install_executable(fake_bin, tool, body)

    result = run_script(
        script_name,
        "--target",
        target_file,
        "--json",
        "--dry-run",
        *extra_args,
        fake_bin=fake_bin,
        env={"FAKE_JOURNAL": str(call_journal)},
    )
    data = parse_json_output(result)

    assert result.returncode == 0
    assert data["status"] == "DRY_RUN"
    if script_name == "gitlab_job.py":
        assert data["access"]["status"] == "DRY_RUN"
        assert data["access"]["target"]["kind"] == "project"
    else:
        assert set(data["surfaces"]) == {"github", "gitlab", "kubernetes"}
    assert read_journal(call_journal) == []


def test_output_modes_are_mutually_exclusive(target_file):
    """An ambiguous machine mode still returns a structured argument error."""
    result = run_script(
        "access_check.py",
        "--target",
        target_file,
        "--json",
        "--yaml",
        "--dry-run",
    )

    assert result.returncode == 2
    data = parse_json_output(result)
    assert data["status"] == "BLOCKED"
    assert data["kind"] == "access_check"
    assert data["errors"][0]["source"] == "arguments"
    assert "not allowed with argument" in data["errors"][0]["reason"]


@pytest.mark.parametrize(
    ("script_name", "args"),
    (
        ("gitlab_access.py", ("check", "--unexpected")),
        ("gitlab_job.py", ("--job-url",)),
        ("gitlab_issue.py", ("open-bug", "--title")),
        ("access_check.py", ("--unexpected",)),
    ),
)
@pytest.mark.parametrize(
    ("mode", "loader"),
    (("--json", json.loads), ("--yaml", yaml.safe_load)),
)
def test_argparse_failures_keep_machine_output_contract(
    script_name, args, mode, loader
):
    result = run_script(script_name, *args, mode)
    data = loader(result.stdout)

    assert result.returncode == 2
    assert data["status"] == "BLOCKED"
    assert data["errors"][0]["source"] == "arguments"
    assert data["errors"][0]["reason"]
    assert "usage:" not in result.stdout


@pytest.mark.parametrize(
    ("flag", "loader"),
    (("--json", json.loads), ("--yaml", yaml.safe_load)),
)
def test_machine_readable_target_failures_emit_blocked_envelope(
    tmp_path,
    flag,
    loader,
):
    """Target failures in machine-readable modes still write an error report."""
    missing_target = tmp_path / "missing-target.toml"

    result = run_script("access_check.py", "--target", missing_target, flag)
    data = loader(result.stdout)

    assert result.returncode == 2
    assert data["status"] == "BLOCKED"
    assert data["kind"] == "access_check"
    assert data["errors"][0]["source"] == "target"
    assert data["errors"][0]["reason"]
    assert "BLOCKED:" not in result.stderr


@pytest.mark.parametrize(
    ("json_mode", "yaml_mode", "loader"),
    ((True, False, json.loads), (False, True, yaml.safe_load)),
)
def test_cli_execute_validation_errors_emit_machine_readable_envelope(
    monkeypatch,
    capsys,
    tmp_path,
    json_mode,
    yaml_mode,
    loader,
):
    """Collector validation failures keep the declared output contract."""
    cli = import_script_module("core.cli")
    status = import_script_module("core.status")
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    target_file = tmp_path / "target.toml"
    target_file.write_text(
        (
            "[github]\n"
            'host = "github.example.test"\n'
            'repository = "unit/repo"\n'
            "\n"
            "[gitlab]\n"
            'url = "https://gitlab.example.test"\n'
            "\n"
            "[kubernetes]\n"
            'context = "unit-context"\n'
            'server = "https://api.cluster.example.test:6443"\n'
            f'kubeconfig = "{kubeconfig}"\n'
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        target=str(target_file),
        json=json_mode,
        yaml=yaml_mode,
        dry_run=False,
        output_dir=None,
        publication=False,
        revision="a" * 40,
    )

    monkeypatch.setattr(
        cli,
        "check_access",
        lambda _target, publication=False: {
            "kind": "access_check",
            "status": status.PASS,
        },
    )

    def collect_storage(_target, _args):
        raise ValueError("malformed nested response")

    exit_status = cli.execute(args, collect_storage)
    captured = capsys.readouterr()
    data = loader(captured.out)

    assert exit_status == 2
    assert data["status"] == "BLOCKED"
    assert data["kind"] == "storage_report"
    assert data["errors"] == [
        {"source": "collect_storage", "reason": "malformed nested response"}
    ]
    assert "BLOCKED:" not in captured.err
