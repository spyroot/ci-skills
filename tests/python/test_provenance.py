"""Tests for identifying the skill code that actually ran.

The old resolution accepted `--revision`, `CI_COMMIT_SHA` or `GITHUB_SHA` after
checking only that the value was 40 hex characters. Two consequences are pinned
here: another project's CI commit must never become the skill's revision, and a
dirty subtree must never silently claim the commit that does not contain it.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest
from conftest import SCRIPT_ROOT, git, import_script_module

SKILL_ROOT = SCRIPT_ROOT.parent
FOREIGN_SHA = "b" * 40


def _provenance() -> ModuleType:
    """Load skill byte-identity and consuming-project helpers.

    :returns: Imported provenance module under test.
    """
    return import_script_module("lib.python.core.provenance")


@pytest.fixture
def installed(tmp_path: Path) -> Path:
    """Create a copied skill tree without Git metadata.

    :param tmp_path: Scratch directory receiving skill metadata and script bytes.
    :returns: Root of the installed-like skill copy.
    """
    root = tmp_path / "installed"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: unit\n---\n", encoding="utf-8")
    scripts = root / "scripts" / "core"
    scripts.mkdir(parents=True)
    (scripts / "thing.py").write_text("value = 1\n", encoding="utf-8")
    return root


def test_the_consuming_projects_ci_sha_is_never_the_skill_revision(
    installed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep another project's CI commit separate from skill revision.

    :param installed: Copied skill tree with no own Git metadata.
    :param monkeypatch: Pytest fixture setting the consuming CI commit.

    :returns: None; assertions in the body validate the regression.
    """
    provenance = _provenance()
    monkeypatch.setenv("CI_COMMIT_SHA", FOREIGN_SHA)

    identity = provenance.skill_identity(installed, None)

    assert identity["revision"]["value"] is None
    assert identity["revision"]["verified"] is False
    assert identity["consuming_project"] == {
        "commit": FOREIGN_SHA,
        "source": "env:CI_COMMIT_SHA",
    }


def test_an_operator_claim_is_recorded_as_a_claim_not_as_evidence(
    installed: Path,
) -> None:
    """Label a --revision argument as unverified operator claim.

    :param installed: Copied skill tree with no own Git metadata.

    :returns: None; assertions in the body validate the regression.
    """
    identity = _provenance().skill_identity(installed, "c" * 40)

    assert identity["revision"] == {
        "value": "c" * 40,
        "source": "argv:--revision",
        "verified": False,
    }


def test_matching_installer_record_verifies_an_installed_copy(installed: Path) -> None:
    """Verify copied skill bytes when the root installer record matches.

    :param installed: Copied skill tree receiving the identity record.

    :returns: None; assertions in the body validate the regression.
    """
    provenance = _provenance()
    digest = provenance.tree_digest(installed)
    (installed / provenance.INSTALLATION_IDENTITY_NAME).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "skill_install",
                "status": "PASS",
                **digest,
                "revision": {
                    "value": "a" * 40,
                    "source": "git_head",
                    "verified": True,
                },
            }
        ),
        encoding="utf-8",
    )

    identity = provenance.skill_identity(installed, None)

    assert identity["digest"] == digest["digest"]
    assert identity["revision"] == {
        "value": "a" * 40,
        "source": "installer_record",
        "verified": True,
    }


def test_installer_record_with_different_bytes_fails_closed(installed: Path) -> None:
    """Reject installer identity after executed script bytes change.

    :param installed: Copied skill tree whose script is changed after record.

    :returns: None; assertions in the body validate the regression.
    """
    provenance = _provenance()
    digest = provenance.tree_digest(installed)
    record = {
        "schema_version": "1.0",
        "kind": "skill_install",
        "status": "PASS",
        **digest,
        "revision": {
            "value": "a" * 40,
            "source": "git_head",
            "verified": True,
        },
    }
    (installed / provenance.INSTALLATION_IDENTITY_NAME).write_text(
        json.dumps(record), encoding="utf-8"
    )
    (installed / "scripts" / "core" / "thing.py").write_text(
        "value = 2\n", encoding="utf-8"
    )

    identity = provenance.skill_identity(installed, "b" * 40)

    assert identity["revision"] == {
        "value": "b" * 40,
        "source": "argv:--revision",
        "verified": False,
    }


def test_a_malformed_claim_is_refused(installed: Path) -> None:
    """Reject a revision claim that is not a full commit SHA.

    :param installed: Copied skill tree used for identity resolution.

    :returns: None; assertions in the body validate the regression.
    """
    provenance = _provenance()

    with pytest.raises(provenance.ProvenanceError, match="full commit SHA"):
        provenance.skill_identity(installed, "not-a-sha")


def test_this_checkout_reports_a_verified_revision() -> None:
    """A clean subtree in the repository that tracks it is verified.
    :returns: None; assertions in the body validate the regression.
    """
    identity = _provenance().skill_identity(SKILL_ROOT, None)

    assert identity["revision"]["source"] == "git_head"
    assert len(identity["revision"]["value"]) == 40


def test_a_dirty_skill_subtree_is_reported_not_silently_claimed(tmp_path: Path) -> None:
    """Mark a tracked skill subtree dirty after changing its bytes.

    :param tmp_path: Scratch Git repository and tracked skill directory.

    :returns: None; assertions in the body validate the regression.
    """
    provenance = _provenance()
    repo = tmp_path / "repo"
    skill = repo / "skills" / "unit"
    skill.mkdir(parents=True)
    git(repo, "init", "-q", ".")
    git(repo, "config", "user.email", "unit@example.test")
    git(repo, "config", "user.name", "unit")
    (skill / "SKILL.md").write_text("---\nname: unit\n---\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "baseline")

    clean = provenance.skill_identity(skill, None)
    assert clean["revision"]["verified"] is True

    (skill / "SKILL.md").write_text("---\nname: unit\n---\nedited\n", encoding="utf-8")
    dirty = provenance.skill_identity(skill, None)

    assert dirty["revision"]["verified"] is False
    assert dirty["revision"]["detail"] == "skill_subtree_dirty"
    assert dirty["revision"]["value"] == clean["revision"]["value"]
    assert dirty["digest"] != clean["digest"]


def test_an_installed_copy_inside_an_unrelated_repository_claims_nothing(
    tmp_path: Path,
) -> None:
    """Refuse provenance from a repository that does not track the skill.

    :param tmp_path: Scratch enclosing Git repository and untracked copy.

    :returns: None; assertions in the body validate the regression.
    """
    provenance = _provenance()
    enclosing = tmp_path / "other"
    copy = enclosing / "vendor" / "skill"
    copy.mkdir(parents=True)
    git(enclosing, "init", "-q", ".")
    git(enclosing, "config", "user.email", "unit@example.test")
    git(enclosing, "config", "user.name", "unit")
    (enclosing / "unrelated.txt").write_text("x\n", encoding="utf-8")
    git(enclosing, "add", "unrelated.txt")
    git(enclosing, "commit", "-qm", "unrelated")
    (copy / "SKILL.md").write_text("---\nname: unit\n---\n", encoding="utf-8")

    identity = provenance.skill_identity(copy, None)

    assert identity["revision"]["value"] is None


def test_the_digest_changes_when_one_executed_byte_changes(installed: Path) -> None:
    """Change tree digest after a Python script byte is modified.

    :param installed: Copied skill tree whose script is modified.

    :returns: None; assertions in the body validate the regression.
    """
    provenance = _provenance()
    before = provenance.tree_digest(installed)["digest"]

    (installed / "scripts" / "core" / "thing.py").write_text(
        "value = 2\n", encoding="utf-8"
    )

    assert provenance.tree_digest(installed)["digest"] != before


def test_the_digest_ignores_bytecode_caches(installed: Path) -> None:
    """Ignore Python bytecode cache files in installed skill digest.

    :param installed: Copied skill tree receiving a __pycache__ file.

    :returns: None; assertions in the body validate the regression.
    """
    provenance = _provenance()
    before = provenance.tree_digest(installed)
    cache = installed / "scripts" / "core" / "__pycache__"
    cache.mkdir()
    (cache / "thing.cpython-311.pyc").write_bytes(b"\x00\x01")

    assert provenance.tree_digest(installed) == before


def test_only_the_root_installer_record_is_excluded_from_the_digest(
    installed: Path,
) -> None:
    """Ignore only the root identity record, retaining nested namesakes.

    :param installed: Copied skill tree receiving root and nested records.

    :returns: None; assertions in the body validate the regression.
    """
    provenance = _provenance()
    before = provenance.tree_digest(installed)
    (installed / provenance.INSTALLATION_IDENTITY_NAME).write_text(
        "root record\n", encoding="utf-8"
    )
    assert provenance.tree_digest(installed) == before

    nested = installed / "scripts" / provenance.INSTALLATION_IDENTITY_NAME
    nested.write_text("ordinary nested payload\n", encoding="utf-8")
    assert provenance.tree_digest(installed) != before


def test_the_digest_covers_the_path_not_only_the_content(installed: Path) -> None:
    """Change tree digest when unchanged bytes move to another path.

    :param installed: Copied skill tree with a renamed Python file.

    :returns: None; assertions in the body validate the regression.
    """
    provenance = _provenance()
    before = provenance.tree_digest(installed)["digest"]
    source = installed / "scripts" / "core" / "thing.py"
    source.rename(source.with_name("renamed.py"))

    assert provenance.tree_digest(installed)["digest"] != before


def test_an_empty_or_missing_tree_blocks(tmp_path: Path) -> None:
    """Reject empty and missing roots before reporting a skill digest.

    :param tmp_path: Scratch empty root and absent path.

    :returns: None; assertions in the body validate the regression.
    """
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
def test_each_consumer_variable_is_recorded_with_its_source(
    variable: str, expected: str
) -> None:
    """Record CI or GitHub commit variable as consuming-project source.

    :param variable: CI variable name carrying the foreign commit.
    :param expected: Source label expected in the identity receipt.

    :returns: None; assertions in the body validate the regression.
    """
    assert _provenance().consuming_project({variable: FOREIGN_SHA}) == {
        "commit": FOREIGN_SHA,
        "source": expected,
    }


def test_no_consumer_variable_reports_nothing() -> None:
    """Leave consuming-project commit and source absent without CI variables.
    :returns: None; assertions in the body validate the regression.
    """
    assert _provenance().consuming_project({}) == {"commit": None, "source": None}
