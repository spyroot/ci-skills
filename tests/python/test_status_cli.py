"""Shared status token and CLI exit-code tests.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import import_script_module

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue
    from lib.python.core.target import Target


def test_status_tokens_have_single_exit_mapping() -> None:
    """Only PASS and DRY_RUN are successful CLI outcomes.
    :returns: None; assertions in the body validate the regression.
    """
    status = import_script_module("lib.python.sys.status")

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
    monkeypatch: pytest.MonkeyPatch,
    target_file: Path,
    collector_status: str,
    expected_exit: int,
) -> None:
    """Require collector PASS, PARTIAL, and BLOCKED to map to shared exits.

    :param monkeypatch: Fixture replacing provider access, binding, and rendering.
    :param target_file: Nonsecret target selected for the adapter.
    :param collector_status: Parametrized collector status returned by the fake.
    :param expected_exit: Required process status for that collector status.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.cli")
    status = import_script_module("lib.python.sys.status")
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
        "check_access",
        lambda _target, publication=False, cilium=False, state=None: {
            "status": status.PASS
        },
    )
    monkeypatch.setattr(cli, "bind_sources", lambda target, revision=None: target)
    monkeypatch.setattr(
        cli, "emit", lambda data, _mode, _output_dir=None: json.dumps(data)
    )

    def collect(_target: Target, _args: JSONValue, *, state=None) -> JSONObject:
        """Return the selected status through a fake collector report.

        :param _target: Bound target unused by this fake.
        :param _args: Parsed options unused by this fake.
        :param state: Shared invocation accepted by the collector double.
        :returns: Minimal report with the parametrized collector status.
        """
        return {"kind": "unit", "status": collector_status, "records": [], "errors": []}

    assert cli.execute(args, collect) == expected_exit


def test_cli_execute_maps_dry_run_to_success(
    monkeypatch: pytest.MonkeyPatch, target_file: Path
) -> None:
    """Require a DRY_RUN access report to exit zero without live probes.

    :param monkeypatch: Fixture replacing dry-run access and output rendering.
    :param target_file: Nonsecret target selected for the adapter.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.cli")
    status = import_script_module("lib.python.sys.status")
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
        lambda _target, publication=False, cilium=False: {
            "kind": "access_check",
            "status": status.DRY_RUN,
            "publication": publication,
        },
    )
    monkeypatch.setattr(cli, "emit", lambda data, _mode, _output_dir: json.dumps(data))

    assert cli.execute_access(args) == 0
