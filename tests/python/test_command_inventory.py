"""Record the advertised interfaces and current source-tree failures."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

INVENTORY = json.loads(
    (REPO_ROOT / "inventory" / "command-interfaces.json").read_text(encoding="utf-8")
)
MANIFEST = json.loads(
    (REPO_ROOT / "ci-skills" / "tools.json").read_text(encoding="utf-8")
)
PROBES = (("--help",), ("--dry-run", "--json"), ("--dry-run", "--yaml"))


def test_inventory_matches_binding_manifest_and_current_paths() -> None:
    """Keep the inventory tied to the pinned contracts and all 17 commands."""
    binding = yaml.safe_load(
        (REPO_ROOT / "standards-binding.yaml").read_text(encoding="utf-8")
    )
    assert INVENTORY["standards_revision"] == binding["spec"]["source"]["revision"]
    assert INVENTORY["applicable_contracts"] == binding["spec"]["requiredContracts"]
    assert re.fullmatch(r"[0-9a-f]{40}", INVENTORY["base_revision"])
    assert INVENTORY["evidence_class"] == (
        "source-tree interface observation; no live system action"
    )
    shipped = {
        path.name if path.suffix == ".py" else f"bin/{path.name}"
        for path in (REPO_ROOT / "ci-skills" / "bin").iterdir()
        if path.is_file() and path.name != "__init__.py"
    }
    assert set(INVENTORY["commands"]) == set(MANIFEST["commands"]) == shipped
    surface = hashlib.sha256()
    for path in sorted((REPO_ROOT / "ci-skills" / "bin").iterdir()):
        if not path.is_file() or path.name == "__init__.py":
            continue
        surface.update(path.name.encode())
        surface.update(b"\0")
        surface.update(path.read_bytes())
    assert INVENTORY["command_surface_sha256"] == surface.hexdigest()

    for name, row in INVENTORY["commands"].items():
        declared = MANIFEST["commands"][name]
        assert row["entrypoint"] == f"ci-skills/bin/{name.removeprefix('bin/')}"
        for field in ("entrypoint", "implementation"):
            path = Path(row[field])
            assert not path.is_absolute() and ".." not in path.parts
            assert (REPO_ROOT / path).is_file(), (name, field)
        assert row["report_kind"] == declared["report_kind"]
        assert row["requires_authorities"] == declared["requires_authorities"]
        assert row["read_only_declared"] == declared.get("read_only")
        for flag, field in (("--json", "json_flag"), ("--yaml", "yaml_flag")):
            expected = (
                "declared_but_unreachable"
                if flag in declared["options"]
                else "not_declared"
            )
            assert row[field] == expected, (name, flag)
        assert row["help"] == "blocked_before_parse"
        assert row["evidence"] == "tests/python/test_command_inventory.py"
        assert row["observed_output"]["probes"] == [" ".join(p) for p in PROBES]


@pytest.mark.parametrize("name", sorted(INVENTORY["commands"]))
@pytest.mark.parametrize("flags", PROBES)
def test_source_invocation_matches_recorded_output(
    name: str, flags: tuple[str, ...], tmp_path: Path
) -> None:
    """Observe the current failure before any provider or cluster access.

    :param name: Manifest command key to inspect.
    :param flags: One read-only help or dry-run mode to execute.
    :param tmp_path: Isolated HOME directory for the invocation.
    """
    row = INVENTORY["commands"][name]
    command = REPO_ROOT / row["entrypoint"]
    if name.endswith(".py"):
        argv = [sys.executable, str(command), *flags]
    else:
        bash = shutil.which("bash")
        assert bash is not None
        argv = [bash, str(command), *flags]
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    for token in ("GITLAB_TOKEN", "GITLAB_ACCESS_TOKEN", "OAUTH_TOKEN", "CI_JOB_TOKEN"):
        env.pop(token, None)
    env["HOME"] = str(tmp_path)
    env["KUBECONFIG"] = ""
    result = subprocess.run(
        argv,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    observed = row["observed_output"]
    assert result.returncode == observed["exit_code"], (name, flags)
    assert bool(result.stdout) == observed["stdout_nonempty"], (name, flags)
    if observed["blocker"] == "missing_core_module":
        assert "No module named 'core'" in result.stderr, (name, flags)
    else:
        assert observed["blocker"] == "missing_bash_library"
        assert "No such file or directory" in result.stderr, (name, flags)
