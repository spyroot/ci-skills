"""Shared status token and CLI exit-code tests."""

from __future__ import annotations

import argparse
import json

import pytest
from conftest import import_script_module


def test_status_tokens_have_single_exit_mapping():
    """Only PASS and DRY_RUN are successful CLI outcomes."""
    status = import_script_module("core.status")

    assert status.exit_code(status.PASS) == 0
    assert status.exit_code(status.DRY_RUN) == 0
    assert status.exit_code(status.PARTIAL) == 2
    assert status.exit_code(status.BLOCKED) == 2
    assert status.exit_code(status.UNKNOWN) == 2


@pytest.mark.parametrize(
    ("collector_status", "expected_exit"),
    (("PASS", 0), ("PARTIAL", 2), ("BLOCKED", 2)),
)
def test_cli_execute_uses_shared_exit_mapping(
    monkeypatch,
    target_file,
    collector_status,
    expected_exit,
):
    """CLI adapters return the shared exit code for collector statuses."""
    cli = import_script_module("core.cli")
    status = import_script_module("core.status")
    args = argparse.Namespace(
        target=str(target_file),
        json=True,
        yaml=False,
        dry_run=False,
        output_dir=None,
        publication=False,
    )

    monkeypatch.setattr(
        cli,
        "verify",
        lambda _target, job_url=None, publication=False: {"status": status.PASS},
    )
    monkeypatch.setattr(cli, "emit", lambda data, _mode, _output_dir: json.dumps(data))

    def collect(_target, _args):
        return {"kind": "unit", "status": collector_status, "records": [], "errors": []}

    assert cli.execute(args, collect) == expected_exit


def test_cli_execute_maps_dry_run_to_success(monkeypatch, target_file):
    """DRY_RUN is explicit evidence of no live probes and exits successfully."""
    cli = import_script_module("core.cli")
    status = import_script_module("core.status")
    args = argparse.Namespace(
        target=str(target_file),
        json=True,
        yaml=False,
        dry_run=True,
        output_dir=None,
        publication=True,
    )

    monkeypatch.setattr(
        cli,
        "dry_run_access",
        lambda _target, publication=False: {
            "kind": "access_check",
            "status": status.DRY_RUN,
            "publication": publication,
        },
    )
    monkeypatch.setattr(cli, "emit", lambda data, _mode, _output_dir: json.dumps(data))

    assert cli.execute(args) == 0
