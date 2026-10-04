"""Tests for the manifest generator.

`tools/render_manifest.py` is the only file-mutating tool in `tools/`, and it is
the one that keeps the committed `tools.json` equal to its declaration. Two
things need proving: `--check` reports staleness without writing anything, and a
write makes `--check` pass. `tests/test_catalog.py` covers the repository's own
manifest being current; these cover the tool that makes it so.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT

TOOL = REPO_ROOT / "tools" / "render_manifest.py"
MANIFEST = Path("skills") / "ci-skills" / "tools.json"


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _copy_repo(tmp_path: Path) -> Path:
    """Copy the skill tree and the manifest into a scratch root."""
    root = tmp_path / "repo"
    (root / MANIFEST.parent).mkdir(parents=True)
    shutil.copytree(
        REPO_ROOT / "skills" / "ci-skills" / "scripts",
        root / "skills" / "ci-skills" / "scripts",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy2(REPO_ROOT / MANIFEST, root / MANIFEST)
    return root


def test_check_reports_current_for_the_committed_manifest():
    """The repository's own manifest must be current, in the tool's own terms."""
    result = _run(REPO_ROOT, "--check", "--json")

    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "CURRENT"


def test_check_reports_stale_without_writing_anything(tmp_path):
    """`--check` is a read: it must not quietly repair what it reports."""
    root = _copy_repo(tmp_path)
    mangled = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    mangled["commands"].pop("event_trace.py")
    before = json.dumps(mangled, indent=2, sort_keys=True) + "\n"
    (root / MANIFEST).write_text(before, encoding="utf-8")

    result = _run(root, "--check", "--json")

    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "STALE"
    assert (root / MANIFEST).read_text(encoding="utf-8") == before


def test_a_reindented_manifest_is_stale_rather_than_merely_different(tmp_path):
    """The tool compares bytes, so the test that guards the repo must too.

    A hand-reindented manifest parses to the same object. One authority calling
    that current while the other calls it stale is worse than either rule.
    """
    root = _copy_repo(tmp_path)
    parsed = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    (root / MANIFEST).write_text(
        json.dumps(parsed, indent=4, sort_keys=True) + "\n", encoding="utf-8"
    )

    assert json.loads(_run(root, "--check", "--json").stdout)["status"] == "STALE"


def test_rendering_makes_check_pass_and_is_idempotent(tmp_path):
    """Write, then check, then write again: the second write changes nothing."""
    root = _copy_repo(tmp_path)
    (root / MANIFEST).write_text("{}\n", encoding="utf-8")

    first = json.loads(_run(root, "--json").stdout)
    second = json.loads(_run(root, "--json").stdout)

    assert first["status"] == "WRITTEN"
    assert second["status"] == "UNCHANGED"
    assert json.loads(_run(root, "--check", "--json").stdout)["status"] == "CURRENT"


def test_a_root_without_the_skill_fails_loudly(tmp_path):
    """A wrong --root must not render an empty manifest over a real one."""
    result = _run(tmp_path, "--check", "--json")

    assert result.returncode != 0
    assert "no catalog module" in result.stderr
