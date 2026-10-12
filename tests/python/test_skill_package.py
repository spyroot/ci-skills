"""Skill package structure and frontmatter validation tests.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT

SKILL_ROOT = REPO_ROOT / "ci-skills"
REQUIRED_PACKAGE_PATHS = (
    "SKILL.md",
    # Declared by SKILL.md's own frontmatter as the manifest, and step 2 of the
    # instructions is reading it, so an install without it ships a skill that
    # points at a contract that is not there.
    "tools.json",
    "references/access.md",
    "bin/access_check.py",
    "bin/gitlab_job.py",
    "bin/gitlab_access.py",
    "bin/gitlab_milestone.py",
    "bin/gitlab_issue.py",
    "bin/gitlab_wiki.py",
    "bin/gitlab_runner.py",
    "bin/storage_report.py",
    "bin/event_trace.py",
    "bin/cilium_status.py",
)


def _frontmatter(path: Path) -> Mapping[str, str]:
    """Parse YAML frontmatter from a selected skill document.

    :param path: SKILL.md file expected to start and end a YAML header.
    :returns: Parsed frontmatter mapping.
    :raises ValueError: Opening or closing frontmatter marker is missing.
    :raises TypeError: Parsed YAML header is not a mapping.
    """
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


def _validate_skill_package(root: Path) -> Mapping[str, str]:
    """Validate installer-visible skill metadata, files, and reference link.

    :param root: Skill package directory to inspect.
    :returns: Parsed frontmatter after name, description, required files, and
        access-reference checks.
    :raises ValueError: Name, description, required path, or reference is invalid.
    """
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


def test_skill_package_frontmatter_references_and_scripts_are_valid() -> None:
    """The committed skill package has installer-visible metadata and files.
    :returns: None; assertions in the body validate the regression.
    """
    frontmatter = _validate_skill_package(SKILL_ROOT)

    assert frontmatter["name"] == "ci-skills"
    assert "Kubernetes" in frontmatter["description"]


def test_skill_package_validation_rejects_malformed_package(tmp_path: Path) -> None:
    """Require a mismatched frontmatter name to reject a package.

    :param tmp_path: Directory for the malformed scratch skill.

    :returns: None; assertions in the body validate the regression.
    """
    root = tmp_path / "broken-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: other-name\ndescription: broken\n---\n# Broken\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="skill name"):
        _validate_skill_package(root)


def test_docs_define_gitlab_token_file_storage_contract() -> None:
    """Package docs describe per-host dotfile token storage without inline secrets.
    :returns: None; assertions in the body validate the regression.
    """
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
