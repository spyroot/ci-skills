"""Tests for the declared target-resolution chain and the output default.

The protocol is the PR's whole claim: four declared places, first match wins,
the winner reported, and a `--target` that does not exist is an error rather
than a quiet fall-through to a lower tier. None of that was covered, so a
refactor turning the raise into a `continue` would have shipped green while
aiming a run at a cluster the caller did not name.

`conftest.run_script` sets `HOME` to the current working directory, which makes
the project and user candidates the same path. These tests therefore drive the
resolution function directly with an explicit `HOME` and an explicit working
directory, and keep one end-to-end subprocess case for the error text.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import SCRIPT_ROOT, import_script_module

CLI = import_script_module("core.cli")
CATALOG = import_script_module("core.catalog")
TARGET = import_script_module("core.target")

TARGET_BODY = (
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
)


def _write_target(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TARGET_BODY, encoding="utf-8")
    return path


@pytest.fixture
def tiers(tmp_path, monkeypatch):
    """Lay out all four candidate locations without filling any of them in."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / CATALOG.PROJECT_DIR).mkdir(parents=True)
    (project / CATALOG.PROJECT_DIR).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CI_SKILLS_TARGET", raising=False)
    monkeypatch.chdir(project)
    return {
        "argv": tmp_path / "explicit" / CATALOG.TARGET_FILENAME,
        "env": tmp_path / "from-env" / CATALOG.TARGET_FILENAME,
        "project": project / CATALOG.PROJECT_DIR / CATALOG.TARGET_FILENAME,
        "user": home / CATALOG.PROJECT_DIR / CATALOG.TARGET_FILENAME,
    }


def test_each_declared_tier_resolves_and_names_itself(tiers, monkeypatch):
    """Every tier must work alone, and report the source the protocol declares."""
    _write_target(tiers["user"])
    assert CLI.resolve_target(None) == (tiers["user"], "user")

    _write_target(tiers["project"])
    assert CLI.resolve_target(None) == (tiers["project"], "project")

    monkeypatch.setenv("CI_SKILLS_TARGET", str(_write_target(tiers["env"])))
    assert CLI.resolve_target(None) == (tiers["env"], "env:CI_SKILLS_TARGET")

    explicit = _write_target(tiers["argv"])
    assert CLI.resolve_target(str(explicit)) == (explicit, "argv:--target")


def test_the_chain_is_the_declared_order_and_first_match_wins(tiers, monkeypatch):
    """With every tier present, the highest one must win, in declared order."""
    for key in ("user", "project", "env", "argv"):
        _write_target(tiers[key])
    monkeypatch.setenv("CI_SKILLS_TARGET", str(tiers["env"]))

    declared = [step["source"] for step in CATALOG.TARGET_PROTOCOL]
    assert declared == ["argv:--target", "env:CI_SKILLS_TARGET", "project", "user"]

    # Remove the winner one tier at a time and watch the next one take over.
    assert CLI.resolve_target(str(tiers["argv"]))[1] == declared[0]
    assert CLI.resolve_target(None)[1] == declared[1]
    monkeypatch.delenv("CI_SKILLS_TARGET")
    assert CLI.resolve_target(None)[1] == declared[2]
    tiers["project"].unlink()
    assert CLI.resolve_target(None)[1] == declared[3]


def test_an_explicit_target_that_is_absent_is_an_error_not_a_fallback(tiers):
    """The declared rule: a missing --target never falls through to a lower tier."""
    _write_target(tiers["user"])
    _write_target(tiers["project"])
    absent = tiers["argv"]  # never written

    with pytest.raises(TARGET.TargetError, match=f"target_file_missing:{absent}"):
        CLI.resolve_target(str(absent))


def test_an_absent_env_target_is_an_error_not_a_fallback(tiers, monkeypatch):
    """An explicit environment selector cannot fall through to another cluster."""
    _write_target(tiers["user"])
    monkeypatch.setenv("CI_SKILLS_TARGET", str(tiers["env"]))  # never written

    with pytest.raises(TARGET.TargetError, match="target_file_missing"):
        CLI.resolve_target(None)


def test_node_diagnostic_routes_are_optional_but_strict(tmp_path):
    """The selected target declares exact existing-Pod routes, no image or token."""
    path = _write_target(tmp_path / "target.toml")
    assert TARGET.load_target(path).kubernetes.node_diagnostics is None

    path.write_text(
        TARGET_BODY
        + "\n[kubernetes.node_diagnostics]\n"
        + 'node = "node-a"\n'
        + "\n[kubernetes.node_diagnostics.cilium]\n"
        + 'namespace = "cilium"\nselector = "app=cilium"\n'
        + 'container = "cilium-agent"\n'
        + "\n[kubernetes.node_diagnostics.journal]\n"
        + 'namespace = "diagnostics"\nselector = "app=journal"\n'
        + 'container = "journal-reader"\n'
        + 'directory = "/host-journal"\n'
        + 'host_path = "/var/log/journal"\n',
        encoding="utf-8",
    )
    selected = TARGET.load_target(path).kubernetes.node_diagnostics
    assert selected.node == "node-a"
    assert selected.cilium.selector == "app=cilium"
    assert selected.journal.host_path == "/var/log/journal"

    path.write_text(path.read_text(encoding="utf-8") + 'image = "busybox"\n')
    with pytest.raises(TARGET.TargetError, match="unsupported fields"):
        TARGET.load_target(path)


def test_no_target_anywhere_names_the_template_and_every_path_searched(tiers):
    """The error has to be actionable: what to copy, and where it looked."""
    with pytest.raises(TARGET.TargetError) as raised:
        CLI.resolve_target(None)

    message = str(raised.value)
    assert "target.toml.template" in message
    assert str(tiers["project"]) in message
    assert str(tiers["user"]) in message


def test_a_cold_run_with_no_target_still_answers_a_pipe_in_json(tmp_path):
    """The first failure a program meets must be the declared default format.

    A bare stderr line here was unparseable by the caller the default output
    mode exists for, and it contradicted `default_output` in the manifest.
    """
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()

    result = subprocess.run(
        [sys.executable, str(SCRIPT_ROOT / "storage_report.py")],
        capture_output=True,
        text=True,
        check=False,
        cwd=project,
        env={"HOME": str(home), "PATH": "/nonexistent", "LC_ALL": "C"},
        timeout=30,
    )
    report = json.loads(result.stdout)

    assert result.returncode == 2
    assert report["status"] == "BLOCKED"
    assert report["kind"] == "storage_report"
    assert "target.toml.template" in report["errors"][0]["reason"]
