"""Keep the observed command inventory aligned with the shipped interfaces."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT
from test_installed_package import ENTRYPOINT_CASES, NODE_ENTRYPOINT_CASES

INVENTORY = json.loads(
    (REPO_ROOT / "inventory" / "command-interfaces.json").read_text(encoding="utf-8")
)
MANIFEST = json.loads(
    (REPO_ROOT / "skills" / "ci-skills" / "tools.json").read_text(encoding="utf-8")
)


def test_inventory_covers_exactly_the_shipped_commands_and_binding() -> None:
    """No command, implementation, authority, or mode finding may disappear."""
    binding = yaml.safe_load(
        (REPO_ROOT / "standards-binding.yaml").read_text(encoding="utf-8")
    )
    assert INVENTORY["standards_revision"] == binding["spec"]["source"]["revision"]
    assert set(INVENTORY["applicable_contracts"]) <= set(
        binding["spec"]["requiredContracts"]
    )
    assert INVENTORY["evidence_class"] == "offline installed-command interface"
    assert set(INVENTORY["commands"]) == set(MANIFEST["commands"])

    for name, row in INVENTORY["commands"].items():
        declared = MANIFEST["commands"][name]
        for field in ("entrypoint", "implementation"):
            path = Path(row[field])
            assert not path.is_absolute() and ".." not in path.parts
            assert (REPO_ROOT / path).is_file(), (name, field)
        assert (
            row["entrypoint"]
            == f"skills/ci-skills/{name if name.startswith('bin/') else f'scripts/{name}'}"
        )
        assert row["report_kind"] == declared["report_kind"]
        assert row["requires_authorities"] == declared["requires_authorities"]
        assert row["read_only_declared"] == declared.get("read_only")
        assert row["help"] == "supported"
        assert row["json_flag"] in {"supported", "missing"}
        assert row["yaml_flag"] in {"supported", "missing"}
        assert row["current_output"].strip()
        assert (REPO_ROOT / row["evidence"]).is_file()


@pytest.mark.parametrize("name", sorted(INVENTORY["commands"]))
def test_help_matches_the_recorded_json_and_yaml_flags(
    name: str, tmp_path: Path
) -> None:
    """Inspect the real shipped entrypoint without contacting an authority."""
    row = INVENTORY["commands"][name]
    entrypoint = REPO_ROOT / row["entrypoint"]
    argv = (
        [str(entrypoint)]
        if name.startswith("bin/")
        else [sys.executable, str(entrypoint)]
    )
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["KUBECONFIG"] = ""
    result = subprocess.run(
        [*argv, "--help"],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (name, result.stderr)
    help_text = result.stdout
    assert "--help" in help_text
    for flag, field in (("--json", "json_flag"), ("--yaml", "yaml_flag")):
        assert (flag in help_text) == (row[field] == "supported"), (name, flag)
    if name.startswith("bin/"):
        for option in MANIFEST["commands"][name]["options"]:
            assert option in help_text, (name, option)


def test_existing_installed_mode_cases_cover_every_python_command() -> None:
    """The existing installed test runs both machine modes for all 15 scripts."""
    covered = {case[0] for case in ENTRYPOINT_CASES}
    covered.update(case[0] for case in NODE_ENTRYPOINT_CASES)
    covered.add("k8s_verify_mtu_consistency.py")
    python_commands = {name for name in MANIFEST["commands"] if name.endswith(".py")}
    assert covered == python_commands
    for name in covered:
        assert INVENTORY["commands"][name]["json_flag"] == "supported"
        assert INVENTORY["commands"][name]["yaml_flag"] == "supported"
        assert INVENTORY["commands"][name]["evidence"] == (
            "tests/test_installed_package.py"
        )


@pytest.mark.parametrize("name", ("bin/ci-api", "bin/ci-binary-build"))
@pytest.mark.parametrize("flag", ("--json", "--yaml"))
def test_bash_mode_gap_is_observed_without_a_provider_call(
    name: str, flag: str, tmp_path: Path
) -> None:
    """The Bash mode gaps remain visible until the owning tools are repaired."""
    row = INVENTORY["commands"][name]
    assert row["json_flag" if flag == "--json" else "yaml_flag"] == "missing"
    marker = tmp_path / "provider-called"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for provider in ("gh", "glab", "kubectl", "oc"):
        script = fake_bin / provider
        script.write_text(
            f"#!/bin/sh\nprintf called >> '{marker}'\nexit 99\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
    args = ["check", "--provider", "github", flag] if name == "bin/ci-api" else [flag]
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(
        [str(REPO_ROOT / row["entrypoint"]), *args],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        timeout=10,
    )
    assert result.returncode == 64, (name, flag, result.stderr)
    assert flag in result.stderr, (name, flag)
    assert not marker.exists(), (name, flag)
