"""Tests for changed-path classification.

Validation skips the Python gates when a range touched only Markdown, so the
classification decides whether the Python checks run at all. Two shapes used to
make a Python change read as Markdown-only: deleting a module, and renaming
`a.py` to `a.md`. Each is exercised here against a real fixture repository,
because a string check on the workflow cannot tell whether the counting is
right.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, load_module

CLASSIFY = load_module(
    "classify_changed_paths", REPO_ROOT / "tools" / "classify_changed_paths.py"
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Build a fixture repository with one committed baseline."""
    root = tmp_path / "fixture"
    root.mkdir()
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "unit@example.test")
    _git(root, "config", "user.name", "unit")
    (root / "keep.md").write_text("# keep\n", encoding="utf-8")
    (root / "module.py").write_text("value = 1\n", encoding="utf-8")
    (root / "doomed.py").write_text("doomed = True\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "baseline")
    return root


def _classify(root: Path) -> dict:
    return CLASSIFY.classify(root, _git(root, "rev-parse", "HEAD~1"), "HEAD")


def test_a_deleted_python_file_is_counted(repo: Path):
    """A commit that only deletes a module must still run the Python gates."""
    _git(repo, "rm", "-q", "doomed.py")
    _git(repo, "commit", "-qm", "delete a module")

    data = _classify(repo)

    assert data["non_markdown"] == ["doomed.py"]
    assert data["non_markdown_count"] == 1


def test_a_python_to_markdown_rename_counts_the_source(repo: Path):
    """Only the rename DESTINATION is reported by name-only, hiding the source."""
    _git(repo, "mv", "module.py", "module.md")
    _git(repo, "commit", "-qm", "rename a module to markdown")

    data = _classify(repo)

    assert "module.py" in data["non_markdown"]
    assert data["non_markdown_count"] == 1
    assert "module.md" in data["changed"]


def test_a_type_change_is_counted(repo: Path):
    """Replacing a module with a symlink is a change to Python content."""
    target = repo / "module.py"
    target.unlink()
    target.symlink_to("keep.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "turn a module into a symlink")

    data = _classify(repo)

    assert data["non_markdown"] == ["module.py"]


def test_a_markdown_only_change_stays_zero(repo: Path):
    """The skip rule itself is preserved: Markdown alone runs no Python gate."""
    (repo / "keep.md").write_text("# keep\n\nmore\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "edit markdown")

    data = _classify(repo)

    assert data["changed_count"] == 1
    assert data["non_markdown_count"] == 0


def test_a_markdown_to_python_rename_counts_the_destination(repo: Path):
    """The mirror case: a new module arriving by rename must be gated."""
    _git(repo, "mv", "keep.md", "keep.py")
    _git(repo, "commit", "-qm", "rename markdown to a module")

    data = _classify(repo)

    assert "keep.py" in data["non_markdown"]


@pytest.mark.parametrize(
    ("name", "expected"),
    (
        ("a.md", True),
        ("a.markdown", True),
        ("A.MD", True),
        ("a.py", False),
        ("a.md.py", False),
        ("notes", False),
    ),
)
def test_markdown_is_decided_by_suffix_case_insensitively(name, expected):
    assert CLASSIFY.is_markdown(name) is expected


def test_an_unreadable_range_blocks(repo: Path):
    """A range that cannot be read must raise, never report zero changes."""
    with pytest.raises(CLASSIFY.ClassifyError):
        CLASSIFY.classify(repo, "0" * 40, "HEAD")


def test_github_output_receives_the_counts(repo: Path, tmp_path: Path):
    """The workflow step consumes these three keys."""
    _git(repo, "rm", "-q", "doomed.py")
    _git(repo, "commit", "-qm", "delete a module")
    output = tmp_path / "github_output"
    base = _git(repo, "rev-parse", "HEAD~1")

    argv = sys.argv
    sys.argv = [
        "classify_changed_paths.py",
        "--root",
        str(repo),
        "--base",
        base,
        "--head",
        "HEAD",
        "--github-output",
        str(output),
    ]
    try:
        assert CLASSIFY.main() == 0
    finally:
        sys.argv = argv

    written = output.read_text(encoding="utf-8")
    assert f"base_sha={base}\n" in written
    assert "changed_count=1\n" in written
    assert "non_markdown_count=1\n" in written
