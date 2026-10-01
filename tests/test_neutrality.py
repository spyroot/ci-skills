"""Tests for the public repository neutrality gate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, forbidden_project_term

NEUTRALITY_TOOL = REPO_ROOT / "tools" / "check_project_neutrality.py"


def _init_repo(root: Path) -> None:
    """Create a temporary Git repository for path and byte scans."""
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True)


def _run_neutrality(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the neutrality checker against a fixture repository."""
    return subprocess.run(
        [sys.executable, str(NEUTRALITY_TOOL), "--root", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_neutrality_scan_passes_clean_tracked_and_dot_files(tmp_path):
    """Clean tracked files and dotfiles produce a PASS result."""
    root = tmp_path / "repo"
    _init_repo(root)
    (root / "README.md").write_text("portable skill\n", encoding="utf-8")
    (root / ".config-note").write_text("still neutral\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "add", "."], check=True, capture_output=True
    )

    result = _run_neutrality(root, "--json")
    data = json.loads(result.stdout)

    assert result.returncode == 0
    assert data["status"] == "PASS"
    assert data["files_scanned"] == 2
    assert data["violations"] == []


def test_neutrality_scan_rejects_marker_in_path_and_file_bytes(tmp_path):
    """The scan checks both file names and contents, including dotfiles."""
    marker = forbidden_project_term()
    root = tmp_path / "repo"
    _init_repo(root)
    (root / f"{marker}-path.txt").write_text("neutral body\n", encoding="utf-8")
    (root / ".hidden").write_text(marker.upper(), encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "add", "."], check=True, capture_output=True
    )

    result = _run_neutrality(root, "--json")
    data = json.loads(result.stdout)

    assert result.returncode == 1
    assert data["status"] == "FAIL"
    assert f"{marker}-path.txt:path" in data["violations"]
    assert ".hidden:content" in data["violations"]


def test_neutrality_checker_source_does_not_store_marker_literal():
    """The checker must not exempt itself by storing the prohibited term."""
    source = NEUTRALITY_TOOL.read_text(encoding="utf-8").casefold()

    assert forbidden_project_term() not in source
