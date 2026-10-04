"""Tests for identifying the skill code that actually ran.

The old resolution accepted `--revision`, `CI_COMMIT_SHA` or `GITHUB_SHA` after
checking only that the value was 40 hex characters. Two consequences are pinned
here: another project's CI commit must never become the skill's revision, and a
dirty subtree must never silently claim the commit that does not contain it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from tests.python.conftest import SCRIPT_ROOT, import_script_module

SKILL_ROOT = SCRIPT_ROOT.parent
FOREIGN_SHA = "b" * 40


def _provenance():
    return import_script_module("core.provenance")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=True, text=True
    )
    return result.stdout.strip()


@pytest.fixture
def installed(tmp_path: Path) -> Path:
    """A copied skill tree with no Git metadata, as installation produces."""
    root = tmp_path / "installed"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: unit\n---\n", encoding="utf-8")
    scripts = root / "scripts" / "core"
    scripts.mkdir(parents=True)
    (scripts / "thing.py").write_text("value = 1\n", encoding="utf-8")
    return root


def test_the_consuming_projects_ci_sha_is_never_the_skill_revision(
    installed, monkeypatch
):
    """A skill run inside another project's CI must not borrow its commit."""
    provenance = _provenance()
    monkeypatch.setenv("CI_COMMIT_SHA", FOREIGN_SHA)

    identity = provenance.skill_identity(installed, None)

    assert identity["revision"]["value"] is None
    assert identity["revision"]["verified"] is False
    assert identity["consuming_project"] == {
        "commit": FOREIGN_SHA,
        "source": "env:CI_COMMIT_SHA",
    }


def test_an_operator_claim_is_recorded_as_a_claim_not_as_evidence(installed):
    """`--revision` labels the receipt; it cannot verify itself."""
    identity = _provenance().skill_identity(installed, "c" * 40)

    assert identity["revision"] == {
        "value": "c" * 40,
        "source": "argv:--revision",
        "verified": False,
    }


def test_a_malformed_claim_is_refused(installed):
    provenance = _provenance()

    with pytest.raises(provenance.ProvenanceError, match="full commit SHA"):
        provenance.skill_identity(installed, "not-a-sha")


def test_this_checkout_reports_a_verified_revision():
    """A clean subtree in the repository that tracks it is verified."""
    identity = _provenance().skill_identity(SKILL_ROOT, None)

    assert identity["revision"]["source"] == "git_head"
    assert len(identity["revision"]["value"]) == 40


def test_a_dirty_skill_subtree_is_reported_not_silently_claimed(tmp_path):
    """New code must never claim the commit that does not contain it."""
    provenance = _provenance()
    repo = tmp_path / "repo"
    skill = repo / "skills" / "unit"
    skill.mkdir(parents=True)
    _git(repo, "init", "-q", ".")
    _git(repo, "config", "user.email", "unit@example.test")
    _git(repo, "config", "user.name", "unit")
    (skill / "SKILL.md").write_text("---\nname: unit\n---\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "baseline")

    clean = provenance.skill_identity(skill, None)
    assert clean["revision"]["verified"] is True

    (skill / "SKILL.md").write_text("---\nname: unit\n---\nedited\n", encoding="utf-8")
    dirty = provenance.skill_identity(skill, None)

    assert dirty["revision"]["verified"] is False
    assert dirty["revision"]["detail"] == "skill_subtree_dirty"
    assert dirty["revision"]["value"] == clean["revision"]["value"]
    assert dirty["digest"] != clean["digest"]


def test_an_installed_copy_inside_an_unrelated_repository_claims_nothing(tmp_path):
    """Containment is not provenance: the repository must TRACK the skill."""
    provenance = _provenance()
    enclosing = tmp_path / "other"
    copy = enclosing / "vendor" / "skill"
    copy.mkdir(parents=True)
    _git(enclosing, "init", "-q", ".")
    _git(enclosing, "config", "user.email", "unit@example.test")
    _git(enclosing, "config", "user.name", "unit")
    (enclosing / "unrelated.txt").write_text("x\n", encoding="utf-8")
    _git(enclosing, "add", "unrelated.txt")
    _git(enclosing, "commit", "-qm", "unrelated")
    (copy / "SKILL.md").write_text("---\nname: unit\n---\n", encoding="utf-8")

    identity = provenance.skill_identity(copy, None)

    assert identity["revision"]["value"] is None


def test_the_digest_changes_when_one_executed_byte_changes(installed):
    provenance = _provenance()
    before = provenance.tree_digest(installed)["digest"]

    (installed / "scripts" / "core" / "thing.py").write_text(
        "value = 2\n", encoding="utf-8"
    )

    assert provenance.tree_digest(installed)["digest"] != before


def test_the_digest_ignores_bytecode_caches(installed):
    """A used install must digest identically to a fresh one."""
    provenance = _provenance()
    before = provenance.tree_digest(installed)
    cache = installed / "scripts" / "core" / "__pycache__"
    cache.mkdir()
    (cache / "thing.cpython-311.pyc").write_bytes(b"\x00\x01")

    assert provenance.tree_digest(installed) == before


def test_the_digest_covers_the_path_not_only_the_content(installed):
    """Moving a file must change the digest even though bytes are identical."""
    provenance = _provenance()
    before = provenance.tree_digest(installed)["digest"]
    source = installed / "scripts" / "core" / "thing.py"
    source.rename(source.with_name("renamed.py"))

    assert provenance.tree_digest(installed)["digest"] != before


def test_an_empty_or_missing_tree_blocks(tmp_path):
    provenance = _provenance()
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(provenance.ProvenanceError, match="skill_root_empty"):
        provenance.tree_digest(empty)
    with pytest.raises(provenance.ProvenanceError, match="skill_root_missing"):
        provenance.tree_digest(tmp_path / "absent")


@pytest.mark.parametrize(
    ("variable", "expected"),
    (("CI_COMMIT_SHA", "env:CI_COMMIT_SHA"), ("GITHUB_SHA", "env:GITHUB_SHA")),
)
def test_each_consumer_variable_is_recorded_with_its_source(variable, expected):
    assert _provenance().consuming_project({variable: FOREIGN_SHA}) == {
        "commit": FOREIGN_SHA,
        "source": expected,
    }


def test_no_consumer_variable_reports_nothing():
    assert _provenance().consuming_project({}) == {"commit": None, "source": None}
