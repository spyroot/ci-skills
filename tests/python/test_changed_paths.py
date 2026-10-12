"""Tests for changed-path classification.

Validation skips the Python gates when a range touched only Markdown, so the
classification decides whether the Python checks run at all. Two shapes used to
make a Python change read as Markdown-only: deleting a module, and renaming
`a.py` to `a.md`. Each is exercised here against a real fixture repository,
because a string check on the workflow cannot tell whether the counting is
right.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import REPO_ROOT, git, load_module

CLASSIFY = load_module(
    "classify_changed_paths", REPO_ROOT / "tools" / "classify_changed_paths.py"
)


if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Create a Git fixture with Markdown and Python files at baseline.

    :param tmp_path: Pytest temporary directory for the fixture repository.
    :returns: Repository path with one committed baseline revision.
    """
    root = tmp_path / "fixture"
    root.mkdir()
    git(root, "init", "-q", ".")
    git(root, "config", "user.email", "unit@example.test")
    git(root, "config", "user.name", "unit")
    (root / "keep.md").write_text("# keep\n", encoding="utf-8")
    (root / "module.py").write_text("value = 1\n", encoding="utf-8")
    (root / "doomed.py").write_text("doomed = True\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "baseline")
    return root


def _classify(root: Path) -> JSONObject:
    """Classify the fixture's latest commit against its parent.

    :param root: Fixture repository containing at least two commits.
    :returns: Changed-path report for ``HEAD~1`` through ``HEAD``.
    """
    return CLASSIFY.classify(root, git(root, "rev-parse", "HEAD~1"), "HEAD")


def test_a_deleted_python_file_is_counted(repo: Path) -> None:
    """Require a deleted Python module to count as a gated code change.

    :param repo: Committed fixture repository containing ``doomed.py``.

    :returns: None; assertions in the body validate the regression.
    """
    git(repo, "rm", "-q", "doomed.py")
    git(repo, "commit", "-qm", "delete a module")

    data = _classify(repo)

    assert data["non_markdown"] == ["doomed.py"]
    assert data["non_markdown_count"] == 1


def test_a_python_to_markdown_rename_counts_the_source(repo: Path) -> None:
    """Require a Python-to-Markdown rename to retain its code source path.

    :param repo: Committed fixture repository containing ``module.py``.

    :returns: None; assertions in the body validate the regression.
    """
    git(repo, "mv", "module.py", "module.md")
    git(repo, "commit", "-qm", "rename a module to markdown")

    data = _classify(repo)

    assert "module.py" in data["non_markdown"]
    assert data["non_markdown_count"] == 1
    assert "module.md" in data["changed"]


def test_a_type_change_is_counted(repo: Path) -> None:
    """Require a module changed to a symlink to count as Python work.

    :param repo: Committed fixture repository containing ``module.py``.

    :returns: None; assertions in the body validate the regression.
    """
    target = repo / "module.py"
    target.unlink()
    target.symlink_to("keep.md")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "turn a module into a symlink")

    data = _classify(repo)

    assert data["non_markdown"] == ["module.py"]


def test_a_markdown_only_change_stays_zero(repo: Path) -> None:
    """Keep Markdown-only changes outside the Python gate count.

    :param repo: Committed fixture repository containing ``keep.md``.

    :returns: None; assertions in the body validate the regression.
    """
    (repo / "keep.md").write_text("# keep\n\nmore\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "edit markdown")

    data = _classify(repo)

    assert data["changed_count"] == 1
    assert data["non_markdown_count"] == 0


def test_a_markdown_to_python_rename_counts_the_destination(repo: Path) -> None:
    """Require a Markdown-to-Python rename to count its new module path.

    :param repo: Committed fixture repository containing ``keep.md``.

    :returns: None; assertions in the body validate the regression.
    """
    git(repo, "mv", "keep.md", "keep.py")
    git(repo, "commit", "-qm", "rename markdown to a module")

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
def test_markdown_is_decided_by_suffix_case_insensitively(
    name: str, expected: bool
) -> None:
    """Check accepted Markdown suffixes and rejected lookalikes.

    :param name: Parametrized filename, including mixed-case Markdown and Python.
    :param expected: Whether the filename should classify as Markdown.

    :returns: None; assertions in the body validate the regression.
    """
    assert CLASSIFY.is_markdown(name) is expected


def test_an_unreadable_range_blocks(repo: Path) -> None:
    """Require an unknown base revision to raise instead of reporting no changes.

    :param repo: Committed fixture repository used for an invalid Git range.

    :returns: None; assertions in the body validate the regression.
    """
    with pytest.raises(CLASSIFY.ClassifyError):
        CLASSIFY.classify(repo, "0" * 40, "HEAD")


def test_github_output_receives_the_counts(repo: Path, tmp_path: Path) -> None:
    """Verify the classifier writes base SHA and change counts for CI.

    :param repo: Committed fixture repository before a Python file deletion.
    :param tmp_path: Pytest directory for the simulated GitHub output file.

    :returns: None; assertions in the body validate the regression.
    """
    git(repo, "rm", "-q", "doomed.py")
    git(repo, "commit", "-qm", "delete a module")
    output = tmp_path / "github_output"
    base = git(repo, "rev-parse", "HEAD~1")

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
