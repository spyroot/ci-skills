"""Deterministic contract checks for the local skill installer."""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, load_module


def _installer():
    return load_module(
        "skill_installer", REPO_ROOT / "tools" / "install_k8s_admin_diagnostics.py"
    )


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: unit\n---\n", encoding="utf-8")
    (source / "scripts" / "check.py").write_text("print('ok')\n", encoding="utf-8")
    return source


def test_install_copies_all_package_files_and_reads_back_digest(tmp_path):
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


def test_package_files_ignores_generated_and_platform_noise(tmp_path):
    installer = _installer()
    source = _source(tmp_path)
    (source / ".DS_Store").write_text("noise", encoding="utf-8")
    (source / "scripts" / "check.pyc").write_bytes(b"compiled")
    pycache = source / "scripts" / "__pycache__"
    pycache.mkdir()
    (pycache / "check.cpython-312.pyc").write_bytes(b"compiled")

    files = installer.package_files(source)

    assert files == [Path("SKILL.md"), Path("scripts/check.py")]


def test_missing_manifest_blocks_without_creating_destination(tmp_path):
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


def test_unverified_source_revision_blocks_before_install(tmp_path):
    installer = _installer()
    source = _source(tmp_path)

    result = installer.install(source, tmp_path / "skills", dry_run=False)

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "source_revision_unverified"
    assert result["safe_next_step"]
    assert not (tmp_path / "skills").exists()


def test_dry_run_and_existing_destination_are_non_destructive(tmp_path):
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
    assert blocked["safe_next_step"]
    assert marker.read_text(encoding="utf-8") == "keep"


def test_upgrade_preserves_previous_skill_and_verifies_new_copy(tmp_path):
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
    assert "# Updated" in (destination / "SKILL.md").read_text()
    assert "# Updated" not in (previous / "SKILL.md").read_text()
    assert upgraded["digest"] == installer.tree_digest(destination)["digest"]


def test_upgrade_rolls_back_when_new_copy_cannot_be_activated(tmp_path, monkeypatch):
    installer = _installer()
    source = _source(tmp_path)
    skills_dir = tmp_path / "skills"
    installer.install(source, skills_dir, dry_run=False, require_verified=False)
    destination = skills_dir / installer.SKILL_NAME
    before = (destination / "SKILL.md").read_bytes()
    (source / "SKILL.md").write_text("new source\n", encoding="utf-8")

    original_rename = Path.rename

    def fail_activation(path: Path, target: Path):
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
    assert not list(skills_dir.glob(f".{installer.SKILL_NAME}.previous-*"))


def test_repeated_upgrade_preserves_each_immediate_previous_version(tmp_path):
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


def test_recovery_restores_old_skill_after_interrupted_upgrade(tmp_path):
    installer = _installer()
    source = _source(tmp_path)
    skills_dir = tmp_path / "skills"
    installer.install(source, skills_dir, dry_run=False, require_verified=False)
    destination = skills_dir / installer.SKILL_NAME
    old_digest = installer.tree_digest(destination)["digest"]
    (source / "SKILL.md").write_text("new\n", encoding="utf-8")
    nonce = "a" * 32
    previous = skills_dir / f".{installer.SKILL_NAME}.previous-{nonce}"
    installer._write_journal(
        skills_dir,
        {
            "schema_version": "1.0",
            "nonce": nonce,
            "old_digest": old_digest,
            "new_digest": installer.tree_digest(source)["digest"],
        },
    )
    destination.rename(previous)

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


def test_recovery_finishes_verified_activation_without_rolling_it_back(tmp_path):
    installer = _installer()
    source = _source(tmp_path)
    skills_dir = tmp_path / "skills"
    installer.install(source, skills_dir, dry_run=False, require_verified=False)
    destination = skills_dir / installer.SKILL_NAME
    old_digest = installer.tree_digest(destination)["digest"]
    (source / "SKILL.md").write_text("new\n", encoding="utf-8")
    nonce = "b" * 32
    previous = skills_dir / f".{installer.SKILL_NAME}.previous-{nonce}"
    installer._write_journal(
        skills_dir,
        {
            "schema_version": "1.0",
            "nonce": nonce,
            "old_digest": old_digest,
            "new_digest": installer.tree_digest(source)["digest"],
        },
    )
    destination.rename(previous)
    destination.mkdir()
    (destination / "SKILL.md").write_bytes((source / "SKILL.md").read_bytes())
    (destination / "scripts").mkdir()
    (destination / "scripts" / "check.py").write_bytes(
        (source / "scripts" / "check.py").read_bytes()
    )

    recovered = installer.recover_install(skills_dir, dry_run=False)
    assert recovered["status"] == "PASS"
    assert recovered["action"] == "complete_verified_activation"
    assert destination.exists() and previous.exists()
    assert not (skills_dir / installer.JOURNAL_NAME).exists()


@pytest.mark.parametrize("interruption", ("SIGTERM", "SIGINT"))
def test_interrupted_upgrade_restores_last_good_install(tmp_path, interruption):
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
            str(REPO_ROOT / "tools" / "install_k8s_admin_diagnostics.py"),
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
    if interruption == "SIGTERM":
        assert result.returncode == -signal.SIGTERM
        assert not destination.exists()
        assert installer.recover_install(skills_dir, dry_run=False)["status"] == "PASS"
    assert (destination / "SKILL.md").read_bytes() == before
    assert not (skills_dir / installer.JOURNAL_NAME).exists()


def test_symlinked_source_file_is_rejected(tmp_path):
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


def test_default_destination_uses_codex_home(monkeypatch, tmp_path):
    installer = _installer()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    assert installer.skills_directory() == tmp_path / "codex-home" / "skills"

    monkeypatch.delenv("CODEX_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert installer.skills_directory() == tmp_path / "home" / ".codex" / "skills"
