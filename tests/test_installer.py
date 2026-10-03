"""Deterministic contract checks for the local skill installer."""

from __future__ import annotations

from pathlib import Path

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
