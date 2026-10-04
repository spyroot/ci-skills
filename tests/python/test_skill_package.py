"""Skill package structure and frontmatter validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from tests.python.conftest import REPO_ROOT

SKILL_ROOT = REPO_ROOT / "skills" / "ci-skills"
REQUIRED_PACKAGE_PATHS = (
    "SKILL.md",
    # Declared by SKILL.md's own frontmatter as the manifest, and step 2 of the
    # instructions is reading it, so an install without it ships a skill that
    # points at a contract that is not there.
    "tools.json",
    "references/access.md",
    "scripts/access_check.py",
    "scripts/gitlab_job.py",
    "scripts/gitlab_access.py",
    "scripts/gitlab_milestone.py",
    "scripts/gitlab_issue.py",
    "scripts/gitlab_wiki.py",
    "scripts/gitlab_runner.py",
    "scripts/storage_report.py",
    "scripts/event_trace.py",
    "scripts/cilium_status.py",
)


def _frontmatter(path: Path) -> dict[str, str]:
    """Read YAML frontmatter from a skill file."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("frontmatter missing")
    _start, marker, rest = text.partition("---\n")
    frontmatter, marker, _body = rest.partition("\n---\n")
    if not marker:
        raise ValueError("frontmatter terminator missing")
    data = yaml.safe_load(frontmatter)
    if not isinstance(data, dict):
        raise TypeError("frontmatter must be a mapping")
    return data


def _validate_skill_package(root: Path) -> dict[str, str]:
    """Validate the package files that the skill installer consumes."""
    skill_file = root / "SKILL.md"
    frontmatter = _frontmatter(skill_file)
    if frontmatter.get("name") != root.name:
        raise ValueError("skill name must match package directory")
    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("skill description is required")
    missing = [
        relative
        for relative in REQUIRED_PACKAGE_PATHS
        if not (root / relative).is_file()
    ]
    if missing:
        raise ValueError(f"missing package paths: {', '.join(missing)}")
    skill_text = skill_file.read_text(encoding="utf-8")
    if "references/access.md" not in skill_text:
        raise ValueError("access reference link is required")
    return frontmatter


def test_skill_package_frontmatter_references_and_scripts_are_valid():
    """The committed skill package has installer-visible metadata and files."""
    frontmatter = _validate_skill_package(SKILL_ROOT)

    assert frontmatter["name"] == "ci-skills"
    assert "Kubernetes" in frontmatter["description"]


def test_skill_package_validation_rejects_malformed_package(tmp_path):
    """Malformed packages fail with a concrete validation error."""
    root = tmp_path / "broken-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: other-name\ndescription: broken\n---\n# Broken\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="skill name"):
        _validate_skill_package(root)


def test_docs_define_gitlab_token_file_storage_contract():
    """Package docs describe per-host dotfile token storage without inline secrets."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    access_doc = (SKILL_ROOT / "references" / "access.md").read_text(encoding="utf-8")
    combined = readme + "\n" + access_doc

    assert "gitlab.token_file" in combined
    assert "GITLAB_TOKEN" in combined
    assert "~/.ci-skills/" in combined
    assert "outside this repository" in combined
    assert "no fallback" in combined.casefold()
    assert "token =" not in combined
    assert "/Users/" not in combined
