"""Check contracts for the local skill installer.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Final, Optional

import pytest
from conftest import REPO_ROOT, git, load_module
from jsonschema import Draft202012Validator

if TYPE_CHECKING:
    from lib.python.core.provenance import SkillIdentity

    from tools.install_ci_skills import InstallerCLI

REPOSITORY_UNAVAILABLE_STEP: Final[str] = (
    "Run the installer from the repository that will receive the agent skill."
)
INSTALLATION_IDENTITY_SCHEMA = json.loads(
    (REPO_ROOT / "schemas" / "skill-installation-identity.schema.json").read_text(
        encoding="utf-8"
    )
)
Draft202012Validator.check_schema(INSTALLATION_IDENTITY_SCHEMA)


def _installer() -> ModuleType:
    """Load the package installer module from the repository tools directory.

    :returns: Imported installer module exposing install and recovery operations.
    """
    return load_module("skill_installer", REPO_ROOT / "tools" / "install_ci_skills.py")


INSTALL_SCOPE = _installer().InstallScope
PROJECT_SCOPES: Final[tuple[str, ...]] = (
    INSTALL_SCOPE.CLAUDE_PROJECT.value,
    INSTALL_SCOPE.ANTIGRAVITY_PROJECT.value,
)


def _source(tmp_path: Path) -> Path:
    """Create a minimal skill package with a manifest and one script.

    :param tmp_path: Pytest temporary directory containing the source package.
    :returns: Path to the generated package root.
    """
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: unit\n---\n", encoding="utf-8")
    (source / "scripts" / "check.py").write_text("print('ok')\n", encoding="utf-8")
    return source


def _top_level_manifests(skills_dir: Path) -> list[Path]:
    """List one-level ``SKILL.md`` manifests discovered under a skills directory.

    :param skills_dir: Parent skills directory scanned one level deep.
    :returns: Sorted ``SKILL.md`` paths found in immediate child directories.
    """
    return sorted(
        entry / "SKILL.md"
        for entry in skills_dir.iterdir()
        if entry.is_dir() and (entry / "SKILL.md").is_file()
    )


def test_install_copies_all_package_files_and_reads_back_digest(tmp_path: Path) -> None:
    """Copy package files and verify the installed tree digest by read-back.

    :param tmp_path: Pytest directory for source and installed skill trees.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    source = _source(tmp_path)
    skills_dir = tmp_path / "codex" / "skills"

    result = installer.install(
        source, skills_dir, dry_run=False, require_verified=False
    )

    destination = skills_dir / installer.SKILL_NAME
    assert result["status"] == "PASS"
    assert result["file_count"] == 2
    assert result["algorithm"] == "sha256-tree-v1"
    assert (destination / "SKILL.md").read_bytes() == (source / "SKILL.md").read_bytes()
    assert (destination / "scripts" / "check.py").read_bytes() == (
        source / "scripts" / "check.py"
    ).read_bytes()
    assert result["digest"] == installer.tree_digest(destination)["digest"]


def test_verified_install_records_source_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Record verified source revision and require that record for idempotence.

    :param monkeypatch: Pytest fixture supplying a verified source identity.
    :param tmp_path: Pytest directory for source, destination, and identity record.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    source = _source(tmp_path)
    skills_dir = tmp_path / "skills"
    revision = {"value": "a" * 40, "source": "git_head", "verified": True}
    original_identity = installer.skill_identity

    def verified_identity(
        root: Path,
        claimed: Optional[str] = None,
        environ: Optional[Mapping[str, str]] = None,
    ) -> SkillIdentity:
        """Attach a verified revision only to the test source package.

        :param root: Package root whose identity the installer requests.
        :param claimed: Optional claimed revision forwarded to the original resolver.
        :param environ: Optional environment forwarded to the original resolver.
        :returns: Original skill identity with source revision when root matches source.
        """
        identity = original_identity(root, claimed, environ)
        if Path(root) == source:
            identity["revision"] = revision
        return identity

    monkeypatch.setattr(installer, "skill_identity", verified_identity)

    result = installer.install(source, skills_dir, dry_run=False)

    destination = skills_dir / installer.SKILL_NAME
    record = json.loads(
        (destination / installer.INSTALLATION_IDENTITY_NAME).read_text(encoding="utf-8")
    )
    assert record == {
        "schema_version": "1.0",
        "kind": "skill_install",
        "status": "PASS",
        "algorithm": result["algorithm"],
        "digest": result["digest"],
        "file_count": result["file_count"],
        "revision": revision,
    }
    Draft202012Validator(INSTALLATION_IDENTITY_SCHEMA).validate(record)
    assert installer.skill_identity(destination, environ={})["revision"] == {
        "value": "a" * 40,
        "source": "installer_record",
        "verified": True,
    }

    again = installer.install(source, skills_dir, dry_run=False, upgrade=True)
    assert again["status"] == "PASS"
    assert again["already_installed"] is True

    (destination / installer.INSTALLATION_IDENTITY_NAME).unlink()
    without_record = installer.install(source, skills_dir, dry_run=True, upgrade=True)
    assert without_record["status"] == "DRY_RUN"
    assert "already_installed" not in without_record


def test_package_files_ignores_generated_and_platform_noise(tmp_path: Path) -> None:
    """Exclude platform metadata and compiled cache files from package inventory.

    :param tmp_path: Pytest directory holding the package and noise files.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    source = _source(tmp_path)
    (source / ".DS_Store").write_text("noise", encoding="utf-8")
    (source / "scripts" / "check.pyc").write_bytes(b"compiled")
    pycache = source / "scripts" / "__pycache__"
    pycache.mkdir()
    (pycache / "check.cpython-312.pyc").write_bytes(b"compiled")

    files = installer.package_files(source)

    assert files == [Path("SKILL.md"), Path("scripts/check.py")]


def test_missing_manifest_blocks_without_creating_destination(tmp_path: Path) -> None:
    """Block an install with no skill manifest before creating a destination.

    :param tmp_path: Pytest directory holding manifest-free source and destination.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "scripts" / "check.py").write_text("print('ok')\n", encoding="utf-8")
    skills_dir = tmp_path / "skills"

    result = installer.install(
        source, skills_dir, dry_run=False, require_verified=False
    )

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "skill_manifest_missing"
    assert result["safe_next_step"]
    assert not skills_dir.exists()


def test_unverified_source_revision_blocks_before_install(tmp_path: Path) -> None:
    """Require a verified source revision before default installation.

    :param tmp_path: Pytest directory holding an unverified source package.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    source = _source(tmp_path)

    result = installer.install(source, tmp_path / "skills", dry_run=False)

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "source_revision_unverified"
    assert result["safe_next_step"]
    assert not (tmp_path / "skills").exists()


def test_dry_run_and_existing_destination_are_non_destructive(tmp_path: Path) -> None:
    """Preserve an existing skill and avoid writes during installation planning.

    :param tmp_path: Pytest directory holding source and a preexisting destination.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    source = _source(tmp_path)
    skills_dir = tmp_path / "skills"
    destination = skills_dir / installer.SKILL_NAME

    plan = installer.install(source, skills_dir, dry_run=True, require_verified=False)
    assert plan["status"] == "DRY_RUN"
    assert not skills_dir.exists()

    destination.mkdir(parents=True)
    marker = destination / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    blocked = installer.install(
        source, skills_dir, dry_run=False, require_verified=False
    )
    assert blocked["status"] == "BLOCKED"
    assert blocked["reason"] == "destination_exists"
    assert "--upgrade --dry-run" in blocked["safe_next_step"]
    assert "--confirm-upgrade FINGERPRINT" in blocked["safe_next_step"]
    assert marker.read_text(encoding="utf-8") == "keep"


def test_upgrade_preserves_previous_skill_and_verifies_new_copy(tmp_path: Path) -> None:
    """Preserve the previous skill while activating and verifying an upgrade.

    :param tmp_path: Pytest directory holding source and versioned installations.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    source = _source(tmp_path)
    skills_dir = tmp_path / "skills"
    first = installer.install(source, skills_dir, dry_run=False, require_verified=False)
    assert first["status"] == "PASS"
    source_skill = source / "SKILL.md"
    source_skill.write_text("---\nname: unit\n---\n# Updated\n", encoding="utf-8")

    planned = installer.install(
        source, skills_dir, dry_run=True, require_verified=False, upgrade=True
    )
    assert planned["status"] == "DRY_RUN"
    assert (skills_dir / installer.SKILL_NAME / "SKILL.md").read_text() != (
        source_skill.read_text()
    )

    upgraded = installer.install(
        source, skills_dir, dry_run=False, require_verified=False, upgrade=True
    )
    previous = Path(upgraded["previous_version"])
    destination = skills_dir / installer.SKILL_NAME
    assert upgraded["status"] == "PASS"
    assert upgraded["previous_version"] == str(previous)
    assert previous.parent == skills_dir / installer.BACKUP_DIRECTORY_NAME
    assert "# Updated" in (destination / "SKILL.md").read_text()
    assert "# Updated" not in (previous / "SKILL.md").read_text()
    assert upgraded["digest"] == installer.tree_digest(destination)["digest"]
    assert _top_level_manifests(skills_dir) == [destination / "SKILL.md"]


def test_upgrade_rolls_back_when_new_copy_cannot_be_activated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore the previous skill when staged upgrade activation fails.

    :param tmp_path: Pytest directory holding source and installed skill.
    :param monkeypatch: Pytest fixture injecting a staged-directory rename failure.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    source = _source(tmp_path)
    skills_dir = tmp_path / "skills"
    installer.install(source, skills_dir, dry_run=False, require_verified=False)
    destination = skills_dir / installer.SKILL_NAME
    before = (destination / "SKILL.md").read_bytes()
    (source / "SKILL.md").write_text("new source\n", encoding="utf-8")

    original_rename = Path.rename

    def fail_activation(path: Path, target: Path) -> Path:
        """Fail only the staged rename into the active skill destination.

        :param path: Source path passed to ``Path.rename``.
        :param target: Rename destination compared with the active skill path.
        :returns: Result of the original rename for all other paths.
        :raises OSError: When activation renames the staged skill into place.
        """
        if (
            path.name.startswith(f".{installer.SKILL_NAME}.stage-")
            and target == destination
        ):
            raise OSError("injected activation failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_activation)
    result = installer.install(
        source, skills_dir, dry_run=False, require_verified=False, upgrade=True
    )
    assert result["status"] == "BLOCKED"
    assert (destination / "SKILL.md").read_bytes() == before
    assert not (skills_dir / installer.JOURNAL_NAME).exists()
    assert not list(skills_dir.glob(f"{installer.LEGACY_PREVIOUS_ENTRY_PREFIX}*"))
    backup_dir = skills_dir / installer.BACKUP_DIRECTORY_NAME
    assert not backup_dir.exists() or not list(
        backup_dir.glob(f"{installer.PREVIOUS_ENTRY_PREFIX}*")
    )


def test_repeated_upgrade_preserves_each_immediate_previous_version(
    tmp_path: Path,
) -> None:
    """Keep the immediate predecessor after each successive skill upgrade.

    :param tmp_path: Pytest directory holding three successive package versions.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    source = _source(tmp_path)
    skills_dir = tmp_path / "skills"
    installer.install(source, skills_dir, dry_run=False, require_verified=False)
    source_skill = source / "SKILL.md"
    source_skill.write_text("version two\n", encoding="utf-8")
    second = installer.install(
        source, skills_dir, dry_run=False, require_verified=False, upgrade=True
    )
    source_skill.write_text("version three\n", encoding="utf-8")
    third = installer.install(
        source, skills_dir, dry_run=False, require_verified=False, upgrade=True
    )

    assert second["status"] == third["status"] == "PASS"
    assert "name: unit" in (Path(second["previous_version"]) / "SKILL.md").read_text()
    assert (Path(third["previous_version"]) / "SKILL.md").read_text() == "version two\n"
    assert (
        skills_dir / installer.SKILL_NAME / "SKILL.md"
    ).read_text() == "version three\n"
    assert _top_level_manifests(skills_dir) == [
        skills_dir / installer.SKILL_NAME / "SKILL.md"
    ]


def _stage_interrupted_upgrade(
    installer: ModuleType,
    tmp_path: Path,
    *,
    nonce: str,
    layout: Optional[str] = None,
    activate_new: bool = False,
) -> tuple[Path, Path, Path, str, str]:
    """Create an interrupted upgrade state with a journal and moved prior skill.

    :param installer: Loaded installer module under test.
    :param tmp_path: Pytest directory holding source and skills directories.
    :param nonce: 32-hex transaction identifier written to the journal.
    :param layout: Optional ``previous_layout`` value written to the journal;
        omitting it simulates a legacy v1.0 journal.
    :param activate_new: Whether to copy the updated files into ``destination``.
    :returns: Tuple of ``(skills_dir, destination, previous, old_digest, new_digest)``.
    """
    source = _source(tmp_path)
    skills_dir = tmp_path / "skills"
    installer.install(source, skills_dir, dry_run=False, require_verified=False)
    destination = skills_dir / installer.SKILL_NAME
    old_digest = installer.tree_digest(destination)["digest"]
    (source / "SKILL.md").write_text("new\n", encoding="utf-8")
    new_digest = installer.tree_digest(source)["digest"]
    if layout == installer.PreviousLayout.BACKUPS_DIR.value:
        backup_dir = skills_dir / installer.BACKUP_DIRECTORY_NAME
        backup_dir.mkdir(mode=0o700, exist_ok=True)
        previous = backup_dir / f"{installer.PREVIOUS_ENTRY_PREFIX}{nonce}"
    else:
        previous = skills_dir / f"{installer.LEGACY_PREVIOUS_ENTRY_PREFIX}{nonce}"
    journal: dict[str, object] = {
        "schema_version": "1.0",
        "nonce": nonce,
        "old_digest": old_digest,
        "new_digest": new_digest,
    }
    if layout is not None:
        journal[installer.JOURNAL_PREVIOUS_LAYOUT_KEY] = layout
    installer._write_journal(skills_dir, journal)
    destination.rename(previous)
    if activate_new:
        destination.mkdir()
        (destination / "SKILL.md").write_bytes((source / "SKILL.md").read_bytes())
        (destination / "scripts").mkdir()
        (destination / "scripts" / "check.py").write_bytes(
            (source / "scripts" / "check.py").read_bytes()
        )
    return skills_dir, destination, previous, old_digest, new_digest


@pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "apply"])
@pytest.mark.parametrize(
    "unsafe_kind",
    ["symlink-dir", "broken-symlink", "regular-file"],
)
def test_upgrade_blocks_when_backup_directory_is_unsafe(
    tmp_path: Path, dry_run: bool, unsafe_kind: str
) -> None:
    """Reject upgrade during preflight and apply when .ci-skills.backups is unsafe.

    :param tmp_path: Pytest directory holding source, destination, and unsafe backup path.
    :param dry_run: Whether to run in preflight dry-run mode or apply mode.
    :param unsafe_kind: Kind of unsafe filesystem entry placed at ``.ci-skills.backups``.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    source = _source(tmp_path)
    skills_dir = tmp_path / "skills"
    first = installer.install(source, skills_dir, dry_run=False, require_verified=False)
    assert first["status"] == "PASS"
    destination = skills_dir / installer.SKILL_NAME
    before = (destination / "SKILL.md").read_bytes()
    (source / "SKILL.md").write_text("new version\n", encoding="utf-8")
    backup_path = skills_dir / installer.BACKUP_DIRECTORY_NAME
    escape = tmp_path / "outside"
    if unsafe_kind == "symlink-dir":
        escape.mkdir()
        backup_path.symlink_to(escape, target_is_directory=True)
    elif unsafe_kind == "broken-symlink":
        backup_path.symlink_to(escape / "missing", target_is_directory=True)
    else:
        backup_path.write_text("not a directory\n", encoding="utf-8")

    blocked = installer.install(
        source, skills_dir, dry_run=dry_run, require_verified=False, upgrade=True
    )

    assert blocked["status"] == "BLOCKED"
    assert blocked["reason"] == "install_recovery_unsafe"
    assert (destination / "SKILL.md").read_bytes() == before
    assert not (skills_dir / installer.JOURNAL_NAME).exists()
    if unsafe_kind == "symlink-dir":
        assert list(escape.iterdir()) == []


@pytest.mark.parametrize(
    ("activate_new", "expected_action"),
    [
        pytest.param(False, "restore_previous", id="restore-previous"),
        pytest.param(
            True, "complete_verified_activation", id="complete-verified-activation"
        ),
    ],
)
def test_recovery_handles_backups_dir_layout(
    tmp_path: Path, activate_new: bool, expected_action: str
) -> None:
    """Recover interrupted upgrades stored under the nested backup directory.

    :param tmp_path: Pytest directory holding journal, backup, and destination trees.
    :param activate_new: Whether the new destination copy is already in place.
    :param expected_action: Recovery action expected for the interrupted state.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    skills_dir, destination, previous, old_digest, new_digest = (
        _stage_interrupted_upgrade(
            installer,
            tmp_path,
            nonce="c" * 32,
            layout=installer.PreviousLayout.BACKUPS_DIR.value,
            activate_new=activate_new,
        )
    )

    recovered = installer.recover_install(skills_dir, dry_run=False)

    assert recovered["status"] == "PASS"
    assert recovered["action"] == expected_action
    assert recovered["digest"] == (new_digest if activate_new else old_digest)
    assert destination.exists()
    assert previous.exists() is activate_new
    assert not (skills_dir / installer.JOURNAL_NAME).exists()


@pytest.mark.parametrize(
    "invalid_layout",
    [
        pytest.param("unknown", id="unknown-string"),
        pytest.param(123, id="non-string"),
    ],
)
def test_recovery_blocks_on_invalid_journal_previous_layout(
    tmp_path: Path, invalid_layout: object
) -> None:
    """Reject a recovery journal declaring an unknown or non-string backup layout.

    :param tmp_path: Pytest directory holding the journaled install state.
    :param invalid_layout: Invalid value written under ``previous_layout``.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    skills_dir, destination, _previous, _old_digest, _new_digest = (
        _stage_interrupted_upgrade(
            installer,
            tmp_path,
            nonce="d" * 32,
            layout=None,
        )
    )
    journal_path = skills_dir / installer.JOURNAL_NAME
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal[installer.JOURNAL_PREVIOUS_LAYOUT_KEY] = invalid_layout
    journal_path.unlink()
    installer._write_journal(skills_dir, journal)

    blocked = installer.recover_install(skills_dir, dry_run=False)

    assert blocked["status"] == "BLOCKED"
    assert blocked["reason"] == "install_recovery_unsafe"
    assert not destination.exists()
    assert journal_path.exists()


@pytest.mark.parametrize("unsafe_kind", ["symlink-dir", "regular-file"])
def test_recovery_blocks_when_backup_directory_is_unsafe(
    tmp_path: Path, unsafe_kind: str
) -> None:
    """Reject recovery when .ci-skills.backups is a symlink or regular file.

    :param tmp_path: Pytest directory holding journal and unsafe backup entry.
    :param unsafe_kind: Unsafe entry replacing ``.ci-skills.backups``.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    skills_dir, destination, previous, _old_digest, _new_digest = (
        _stage_interrupted_upgrade(
            installer,
            tmp_path,
            nonce="e" * 32,
            layout=installer.PreviousLayout.BACKUPS_DIR.value,
        )
    )
    backup_dir = skills_dir / installer.BACKUP_DIRECTORY_NAME
    outside = tmp_path / "outside-backups"
    backup_dir.rename(outside)
    if unsafe_kind == "symlink-dir":
        backup_dir.symlink_to(outside, target_is_directory=True)
    else:
        backup_dir.write_text("not a directory\n", encoding="utf-8")

    blocked = installer.recover_install(skills_dir, dry_run=False)

    assert blocked["status"] == "BLOCKED"
    assert blocked["reason"] == "install_recovery_unsafe"
    assert not destination.exists()
    assert (skills_dir / installer.JOURNAL_NAME).exists()
    assert (outside / previous.name).is_dir()


def test_previous_entry_path_fails_closed_on_unknown_layout(tmp_path: Path) -> None:
    """Reject an unsupported backup layout instead of falling back to legacy paths.

    :param tmp_path: Pytest directory supplying the skills directory.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()

    with pytest.raises(ValueError, match="install_recovery_unsafe"):
        installer._previous_entry_path(tmp_path, "f" * 32, "unsupported")


def test_recovery_restores_old_skill_after_interrupted_upgrade(tmp_path: Path) -> None:
    """Recover an interrupted upgrade by restoring the verified previous copy.

    :param tmp_path: Pytest directory holding an upgrade journal and previous copy.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    skills_dir, destination, previous, old_digest, _new_digest = (
        _stage_interrupted_upgrade(installer, tmp_path, nonce="a" * 32)
    )

    expired = installer.recover_install(skills_dir, dry_run=False, deadline=0.0)
    assert expired["status"] == "BLOCKED"
    assert expired["reason"] == "install_deadline_expired"
    assert previous.exists() and not destination.exists()
    assert (skills_dir / installer.JOURNAL_NAME).exists()

    planned = installer.recover_install(skills_dir, dry_run=True)
    assert planned["status"] == "DRY_RUN"
    assert planned["action"] == "restore_previous"
    assert not destination.exists()
    recovered = installer.recover_install(skills_dir, dry_run=False)
    assert recovered["status"] == "PASS"
    assert recovered["digest"] == old_digest
    assert destination.exists()
    assert not previous.exists()
    assert not (skills_dir / installer.JOURNAL_NAME).exists()


def test_recovery_finishes_verified_activation_without_rolling_it_back(
    tmp_path: Path,
) -> None:
    """Complete recovery when the new active copy already matches its journal.

    :param tmp_path: Pytest directory holding journal, previous, and active copies.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    skills_dir, destination, previous, _old_digest, _new_digest = (
        _stage_interrupted_upgrade(
            installer, tmp_path, nonce="b" * 32, activate_new=True
        )
    )

    recovered = installer.recover_install(skills_dir, dry_run=False)
    assert recovered["status"] == "PASS"
    assert recovered["action"] == "complete_verified_activation"
    assert destination.exists() and previous.exists()
    assert not (skills_dir / installer.JOURNAL_NAME).exists()


@pytest.mark.parametrize("interruption", ("SIGTERM", "SIGINT"))
def test_interrupted_upgrade_restores_last_good_install(
    tmp_path: Path, interruption: str
) -> None:
    """Restore the last good install after SIGTERM or SIGINT interrupts activation.

    :param tmp_path: Pytest directory holding source and active skill trees.
    :param interruption: Signal name delivered to the installer subprocess.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    source = _source(tmp_path)
    skills_dir = tmp_path / "skills"
    installer.install(source, skills_dir, dry_run=False, require_verified=False)
    destination = skills_dir / installer.SKILL_NAME
    before = (destination / "SKILL.md").read_bytes()
    (source / "SKILL.md").write_text("new version\n", encoding="utf-8")
    script = """
import importlib.util
import os
import signal
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("interrupted_installer", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
original = Path.rename
destination = Path(sys.argv[4])

def interrupt(path, target):
    if path.name.startswith(f".{module.SKILL_NAME}.stage-") and Path(target) == destination:
        os.kill(os.getpid(), getattr(signal, sys.argv[5]))
    return original(path, target)

Path.rename = interrupt
module.install(Path(sys.argv[2]), Path(sys.argv[3]), dry_run=False,
               require_verified=False, upgrade=True)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(REPO_ROOT / "tools" / "install_ci_skills.py"),
            str(source),
            str(skills_dir),
            str(destination),
            interruption,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert result.returncode == 128 + getattr(signal, interruption)
    assert (destination / "SKILL.md").read_bytes() == before
    assert not (skills_dir / installer.JOURNAL_NAME).exists()


def test_symlinked_source_file_is_rejected(tmp_path: Path) -> None:
    """Reject a source package containing a symlink before installing files.

    :param tmp_path: Pytest directory holding a skill with a linked source file.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    source = _source(tmp_path)
    (source / "scripts" / "linked.py").symlink_to(source / "scripts" / "check.py")

    result = installer.install(
        source, tmp_path / "skills", dry_run=False, require_verified=False
    )

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "skill_symlink_unexpected"
    assert result["safe_next_step"]
    assert not (tmp_path / "skills").exists()


def test_explicit_upgrade_preserves_previous_copy_and_is_idempotent(
    tmp_path: Path,
) -> None:
    """Preserve the prior script on upgrade and recognize a repeated install.

    :param tmp_path: Pytest directory holding source, active, and backup trees.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    source = _source(tmp_path)
    skills_dir = tmp_path / "skills"
    destination = skills_dir / installer.SKILL_NAME
    first = installer.install(source, skills_dir, dry_run=False, require_verified=False)
    assert first["status"] == "PASS"

    (source / "scripts" / "check.py").write_text("print('new')\n", encoding="utf-8")
    plan = installer.install(
        source, skills_dir, dry_run=True, require_verified=False, upgrade=True
    )
    assert plan["status"] == "DRY_RUN"
    assert (destination / "scripts" / "check.py").read_text() == "print('ok')\n"
    assert "previous_version" not in plan

    changed = installer.install(
        source, skills_dir, dry_run=False, require_verified=False, upgrade=True
    )
    assert changed["status"] == "PASS"
    assert (destination / "scripts" / "check.py").read_text() == "print('new')\n"
    backup = Path(changed["previous_version"])
    assert (backup / "scripts" / "check.py").read_text() == "print('ok')\n"
    assert changed["digest"] == installer.tree_digest(destination)["digest"]

    again = installer.install(
        source, skills_dir, dry_run=False, require_verified=False, upgrade=True
    )
    assert again["status"] == "PASS"
    assert again["already_installed"] is True
    assert (backup / "scripts" / "check.py").read_text() == "print('ok')\n"


def test_explicit_upgrade_preserves_symlink_destination(tmp_path: Path) -> None:
    """Back up a symlink destination while activating a real skill directory.

    :param tmp_path: Pytest directory holding source and linked destination.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    source = _source(tmp_path)
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    destination = skills_dir / installer.SKILL_NAME
    destination.symlink_to(source, target_is_directory=True)

    result = installer.install(
        source, skills_dir, dry_run=False, require_verified=False, upgrade=True
    )
    assert result["status"] == "PASS"
    assert destination.is_dir() and not destination.is_symlink()
    previous = Path(result["previous_version"])
    assert previous.is_symlink()
    assert previous.readlink() == source
    assert (destination / "SKILL.md").read_bytes() == (source / "SKILL.md").read_bytes()


def test_confirmed_install_rejects_changed_source_bytes(tmp_path: Path) -> None:
    """Reject installation when source bytes change after plan fingerprinting.

    :param tmp_path: Pytest directory holding planned source and destination.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    source = _source(tmp_path)
    skills_dir = tmp_path / "skills"
    plan = installer.install(source, skills_dir, dry_run=True, require_verified=False)
    fingerprint = installer.plan_fingerprint(plan)
    (source / "SKILL.md").write_text("changed after plan\n", encoding="utf-8")

    result = installer.install(
        source,
        skills_dir,
        dry_run=False,
        require_verified=False,
        expected_fingerprint=fingerprint,
    )
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "installation_fingerprint_changed"
    assert not (skills_dir / installer.SKILL_NAME).exists()


def test_default_destination_uses_codex_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Resolve the default skills directory from CODEX_HOME then HOME.

    :param monkeypatch: Pytest fixture setting the home environment variables.
    :param tmp_path: Pytest directory supplying isolated home paths.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    assert installer.skills_directory() == tmp_path / "codex-home" / "skills"

    monkeypatch.delenv("CODEX_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert installer.skills_directory() == tmp_path / "home" / ".codex" / "skills"


def _cli(installer: ModuleType, *arguments: str) -> InstallerCLI:
    """Build the installer CLI adapter from explicit arguments.

    :param installer: Loaded installer module.
    :param arguments: Command-line arguments after the program name.
    :returns: Initialized ``InstallerCLI`` with its destination resolved.
    :raises SystemExit: If argparse or destination resolution rejects the arguments.
    """  # noqa: DOC502 - InstallerCLI construction propagates parser exits.
    parser = installer.InstallerCLI.build_parser()
    return installer.InstallerCLI(parser, parser.parse_args(list(arguments)), 0.0)


def _git_repository(path: Path) -> Path:
    """Create an empty Git repository for project-scope resolution.

    :param path: Directory to initialize.
    :returns: Resolved repository root.
    """
    path.mkdir(parents=True)
    git(path, "init", "-q")
    return path.resolve()


@pytest.fixture
def isolated_homes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point HOME at a temporary directory, unset CODEX_HOME, and stop Git discovery at tmp_path.

    Git discovery is bounded so a test checkout above ``tmp_path`` cannot satisfy
    the project scope.

    :param monkeypatch: Pytest fixture setting the environment variables.
    :param tmp_path: Pytest directory supplying the isolated home.
    :returns: Temporary home directory.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return home


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("codex-home", Path(".codex") / "skills"),
        ("claude-user", Path(".claude") / "skills"),
        ("antigravity-user", Path(".gemini") / "config" / "skills"),
    ],
)
def test_user_scope_resolves_home_skills_directory(
    isolated_homes: Path, tmp_path: Path, scope: str, expected: Path
) -> None:
    """Map each user scope to its documented directory under HOME.

    :param isolated_homes: Temporary HOME with CODEX_HOME unset.
    :param tmp_path: Pytest directory used as the working directory.
    :param scope: ``--scope`` value under test.
    :param expected: Documented path below HOME.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()

    resolved = installer.scope_skills_directory(installer.InstallScope(scope), tmp_path)

    assert resolved == isolated_homes / expected


def test_codex_home_scope_follows_codex_home(
    monkeypatch: pytest.MonkeyPatch, isolated_homes: Path, tmp_path: Path
) -> None:
    """Keep codex-home equal to the no-flag default when CODEX_HOME is set.

    :param monkeypatch: Pytest fixture setting CODEX_HOME.
    :param isolated_homes: Temporary HOME with CODEX_HOME unset.
    :param tmp_path: Pytest directory supplying CODEX_HOME.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    resolved = installer.scope_skills_directory(
        installer.InstallScope.CODEX_HOME, tmp_path
    )

    assert resolved == tmp_path / "codex-home" / "skills"
    assert resolved == installer.skills_directory()


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("claude-project", Path(".claude") / "skills"),
        ("antigravity-project", Path(".agents") / "skills"),
    ],
)
def test_project_scope_uses_git_root_from_subdirectory(
    isolated_homes: Path, tmp_path: Path, scope: str, expected: Path
) -> None:
    """Resolve each project scope to the repository root, not the working subdirectory.

    The installer can run from any directory inside the consuming repository.

    :param isolated_homes: Temporary HOME bounding Git discovery.
    :param tmp_path: Pytest directory holding the repository.
    :param scope: Repository-scoped ``--scope`` value under test.
    :param expected: Documented path below the repository root.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    repository = _git_repository(tmp_path / "consumer")
    subdirectory = repository / "nested" / "dir"
    subdirectory.mkdir(parents=True)

    resolved = installer.scope_skills_directory(
        installer.InstallScope(scope), subdirectory
    )

    assert resolved == repository / expected


@pytest.mark.parametrize("scope", PROJECT_SCOPES)
def test_project_scope_outside_repository_raises(
    isolated_homes: Path, tmp_path: Path, scope: str
) -> None:
    """Refuse project scopes when the working directory has no Git repository.

    :param isolated_homes: Temporary HOME bounding Git discovery.
    :param tmp_path: Pytest directory that is not a repository.
    :param scope: Repository-scoped ``--scope`` value under test.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()

    with pytest.raises(
        installer.AgentProfileError, match="agent_repository_unavailable"
    ):
        installer.scope_skills_directory(installer.InstallScope(scope), tmp_path)


def test_every_scope_has_one_resolver() -> None:
    """Keep the --scope choices and the resolver table in one-to-one agreement.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()

    assert set(installer.SCOPE_SKILLS_DIRECTORIES) == set(installer.InstallScope)
    assert {scope.value for scope in installer.InstallScope} == {
        "codex-home",
        "claude-user",
        "claude-project",
        "antigravity-user",
        "antigravity-project",
    }


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("claude-user", Path(".claude") / "skills" / "ci-skills"),
        ("antigravity-user", Path(".gemini") / "config" / "skills" / "ci-skills"),
    ],
)
def test_cli_scope_sets_destination_without_creating_it(
    isolated_homes: Path, scope: str, expected: Path
) -> None:
    """Resolve user --scope values to ci-skills destinations with no filesystem change.

    :param isolated_homes: Temporary HOME with CODEX_HOME unset.
    :param scope: User-scoped ``--scope`` value under test.
    :param expected: Expected ``ci-skills`` destination below HOME.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()

    cli = _cli(installer, "--scope", scope, "--dry-run")

    assert cli.destination == isolated_homes.resolve() / expected
    assert not isolated_homes.exists()


def test_cli_without_location_keeps_codex_default(isolated_homes: Path) -> None:
    """Keep the no-flag destination at ~/.codex/skills/ci-skills.

    :param isolated_homes: Temporary HOME with CODEX_HOME unset.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()

    cli = _cli(installer, "--dry-run")

    assert (
        cli.destination == isolated_homes.resolve() / ".codex" / "skills" / "ci-skills"
    )


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("claude-project", Path(".claude") / "skills" / "ci-skills"),
        ("antigravity-project", Path(".agents") / "skills" / "ci-skills"),
    ],
)
def test_cli_project_scope_uses_working_repository(
    monkeypatch: pytest.MonkeyPatch,
    isolated_homes: Path,
    tmp_path: Path,
    scope: str,
    expected: Path,
) -> None:
    """Install project scopes into the repository that contains the working directory.

    :param monkeypatch: Pytest fixture changing the working directory.
    :param isolated_homes: Temporary HOME bounding Git discovery.
    :param tmp_path: Pytest directory holding the repository.
    :param scope: Repository-scoped ``--scope`` value under test.
    :param expected: Expected ``ci-skills`` path below the repository root.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    repository = _git_repository(tmp_path / "consumer")
    monkeypatch.chdir(repository)

    cli = _cli(installer, "--scope", scope)

    assert cli.destination == repository / expected


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(
            ("--scope", INSTALL_SCOPE.CLAUDE_USER.value, "--skills-dir", "x"),
            id="with-skills-dir",
        ),
        pytest.param(
            ("--scope", INSTALL_SCOPE.ANTIGRAVITY_USER.value, "--skills-dir", "x"),
            id="antigravity-with-skills-dir",
        ),
        pytest.param(
            (
                "--scope",
                INSTALL_SCOPE.CLAUDE_USER.value,
                "--destination",
                "/x/ci-skills",
            ),
            id="with-destination",
        ),
        pytest.param(
            (
                "--scope",
                INSTALL_SCOPE.ANTIGRAVITY_USER.value,
                "--destination",
                "/x/ci-skills",
            ),
            id="antigravity-with-destination",
        ),
        pytest.param(("--scope", "claude"), id="unknown-scope"),
        pytest.param(("--scope", "antigravity"), id="unknown-antigravity-scope"),
    ],
)
def test_cli_rejects_invalid_scope_arguments(
    isolated_homes: Path, capsys: pytest.CaptureFixture[str], arguments: tuple[str, ...]
) -> None:
    """Reject a scope combined with another location or an unknown scope name.

    :param isolated_homes: Temporary HOME with CODEX_HOME unset.
    :param capsys: Pytest fixture capturing the argparse error.
    :param arguments: Invalid command-line arguments.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()

    with pytest.raises(SystemExit) as exit_info:
        _cli(installer, *arguments)

    assert exit_info.value.code == 2
    assert "--scope" in capsys.readouterr().err
    assert not isolated_homes.exists()


@pytest.mark.parametrize("scope", PROJECT_SCOPES)
@pytest.mark.parametrize(
    ("mode", "kind"),
    [
        pytest.param(("--dry-run",), "skill_install", id="install"),
        pytest.param(
            ("--apply", "--confirm-install", "0" * 64, "--timeout", "10s"),
            "skill_install",
            id="install-apply",
        ),
        pytest.param(("--recover",), "skill_install_recovery", id="recover"),
        pytest.param(
            ("--recover", "--apply", "--confirm-recover", "--timeout", "10s"),
            "skill_install_recovery",
            id="recover-apply",
        ),
    ],
)
def test_cli_project_scope_outside_repository_reports_blocked_json(
    monkeypatch: pytest.MonkeyPatch,
    isolated_homes: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    scope: str,
    mode: tuple[str, ...],
    kind: str,
) -> None:
    """Report project scopes outside a Git repository as BLOCKED JSON, not usage text.

    install.sh adds --json, so a caller parses stdout for every failure.

    :param monkeypatch: Pytest fixture changing the working directory.
    :param isolated_homes: Temporary HOME bounding Git discovery.
    :param tmp_path: Pytest directory that is not a repository.
    :param capsys: Pytest fixture capturing stdout and stderr.
    :param scope: Repository-scoped ``--scope`` value under test.
    :param mode: Installer mode arguments.
    :param kind: Result kind expected for the mode.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)

    cli = _cli(installer, "--scope", scope, *mode, "--json")
    status = cli.run()

    captured = capsys.readouterr()
    assert status == 2
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "schema_version": "1.0",
        "kind": kind,
        "status": "BLOCKED",
        "scope": scope,
        "reason": "agent_repository_unavailable",
        "safe_next_step": REPOSITORY_UNAVAILABLE_STEP,
    }
    assert list(outside.iterdir()) == []
    assert not isolated_homes.exists()


@pytest.mark.parametrize("scope", PROJECT_SCOPES)
def test_cli_unresolved_scope_text_output_names_scope(
    monkeypatch: pytest.MonkeyPatch,
    isolated_homes: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    scope: str,
) -> None:
    """Name the scope in text output when it has no destination.

    :param monkeypatch: Pytest fixture changing the working directory.
    :param isolated_homes: Temporary HOME bounding Git discovery.
    :param tmp_path: Pytest directory that is not a repository.
    :param capsys: Pytest fixture capturing stdout and stderr.
    :param scope: Repository-scoped ``--scope`` value under test.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    monkeypatch.chdir(tmp_path)

    status = _cli(installer, "--scope", scope).run()

    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == f"BLOCKED: {scope}\n"
    assert captured.err == "agent_repository_unavailable\n"


@pytest.mark.parametrize("scope", PROJECT_SCOPES)
def test_cli_unresolved_scope_has_no_destination(
    monkeypatch: pytest.MonkeyPatch, isolated_homes: Path, tmp_path: Path, scope: str
) -> None:
    """Refuse to expose a destination for a scope that could not be resolved.

    :param monkeypatch: Pytest fixture changing the working directory.
    :param isolated_homes: Temporary HOME bounding Git discovery.
    :param tmp_path: Pytest directory that is not a repository.
    :param scope: Repository-scoped ``--scope`` value under test.

    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()
    monkeypatch.chdir(tmp_path)

    cli = _cli(installer, "--scope", scope)

    with pytest.raises(
        installer.AgentProfileError, match="agent_repository_unavailable"
    ):
        _ = cli.destination
    with pytest.raises(
        installer.AgentProfileError, match="agent_repository_unavailable"
    ):
        _ = cli.skills_dir


def test_every_agent_failure_reason_has_one_recovery_step() -> None:
    """Keep every agent failure mapped to one actionable recovery message.
    :returns: None; assertions in the body validate the regression.
    """
    installer = _installer()

    for reason in installer.AgentProfileReason:
        assert reason in installer.RECOVERY
        assert isinstance(installer.RECOVERY[reason], str)
