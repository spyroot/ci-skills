"""Tests for the manifest generator.

`tools/render_manifest.py` is the only file-mutating tool in `tools/`, and it is
the one that keeps the committed `tools.json` equal to its declaration. Two
things need proving: `--check` reports staleness without writing anything, and a
write makes `--check` pass. `tests/test_catalog.py` covers the repository's own
manifest being current; these cover the tool that makes it so.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT

TOOL = REPO_ROOT / "tools" / "render_manifest.py"
MANIFEST = Path("ci-skills") / "tools.json"


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the manifest generator against a selected scratch root.

    :param root: Repository-shaped directory passed as ``--root``.
    :param *args: Positional arguments forwarded to the helper under test.
    :returns: Child process status and captured output.

    """
    return subprocess.run(
        [sys.executable, str(TOOL), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _copy_repo(tmp_path: Path) -> Path:
    """Copy the installed catalogue surface and manifest into a scratch root.

    :param tmp_path: Pytest directory for a repository-shaped fixture.
    :returns: Scratch root containing bootstrap, library, and manifest bytes.
    """
    root = tmp_path / "repo"
    skill_root = root / "ci-skills"
    (root / MANIFEST.parent).mkdir(parents=True)
    (skill_root / "bin").mkdir(parents=True)
    (skill_root / "lib").mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "ci-skills" / "bin" / "__init__.py",
        skill_root / "bin" / "__init__.py",
    )
    shutil.copy2(
        REPO_ROOT / "ci-skills" / "bin" / "_bootstrap.py",
        skill_root / "bin" / "_bootstrap.py",
    )
    shutil.copy2(
        REPO_ROOT / "ci-skills" / "lib" / "__init__.py",
        skill_root / "lib" / "__init__.py",
    )
    shutil.copytree(
        REPO_ROOT / "ci-skills" / "lib" / "python",
        skill_root / "lib" / "python",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy2(REPO_ROOT / MANIFEST, root / MANIFEST)
    return root


def test_check_reports_current_for_the_committed_manifest() -> None:
    """The repository's own manifest must be current, in the tool's own terms.
    :returns: None; assertions in the body validate the regression.
    """
    result = _run(REPO_ROOT, "--check", "--json")

    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "CURRENT"


def test_check_reports_stale_without_writing_anything(tmp_path: Path) -> None:
    """Require check mode to report a removed command without repairing it.

    :param tmp_path: Directory for the copied and deliberately stale manifest.

    :returns: None; assertions in the body validate the regression.
    """
    root = _copy_repo(tmp_path)
    mangled = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    mangled["commands"].pop("event_trace.py")
    before = json.dumps(mangled, indent=2, sort_keys=True) + "\n"
    (root / MANIFEST).write_text(before, encoding="utf-8")

    result = _run(root, "--check", "--json")

    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "STALE"
    assert (root / MANIFEST).read_text(encoding="utf-8") == before


def test_a_reindented_manifest_is_stale_rather_than_merely_different(
    tmp_path: Path,
) -> None:
    """Require byte-level staleness for semantically equal reindentation.

    :param tmp_path: Directory for the copied manifest with changed formatting.

    :returns: None; assertions in the body validate the regression.
    """
    root = _copy_repo(tmp_path)
    parsed = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    (root / MANIFEST).write_text(
        json.dumps(parsed, indent=4, sort_keys=True) + "\n", encoding="utf-8"
    )

    assert json.loads(_run(root, "--check", "--json").stdout)["status"] == "STALE"


def test_rendering_makes_check_pass_and_is_idempotent(tmp_path: Path) -> None:
    """Require first render to write and second render to change nothing.

    :param tmp_path: Directory for a scratch manifest initialized to an empty object.

    :returns: None; assertions in the body validate the regression.
    """
    root = _copy_repo(tmp_path)
    (root / MANIFEST).write_text("{}\n", encoding="utf-8")

    first = json.loads(_run(root, "--json").stdout)
    second = json.loads(_run(root, "--json").stdout)

    assert first["status"] == "WRITTEN"
    assert second["status"] == "UNCHANGED"
    assert json.loads(_run(root, "--check", "--json").stdout)["status"] == "CURRENT"


def test_a_root_without_the_skill_fails_loudly(tmp_path: Path) -> None:
    """Require an unrelated root to fail instead of rendering an empty manifest.

    :param tmp_path: Empty directory lacking a catalogue module.

    :returns: None; assertions in the body validate the regression.
    """
    result = _run(tmp_path, "--check", "--json")

    assert result.returncode != 0
    assert "no catalog module" in result.stderr
