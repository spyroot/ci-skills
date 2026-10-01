#!/usr/bin/env python3
"""Install the repository's diagnostic skill into a local Codex skills directory."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

SKILL_NAME = "k8s-admin-diagnostics"
SOURCE = Path(__file__).resolve().parents[1] / "skills" / SKILL_NAME
sys.path.insert(0, str(SOURCE / "scripts"))

from core.provenance import _included, skill_identity, tree_digest

RECOVERY = {
    "skill_manifest_missing": "Use a checkout containing skills/k8s-admin-diagnostics/SKILL.md.",
    "skill_symlink_unexpected": "Remove the symlink from the source skill tree and retry.",
    "source_revision_unverified": "Install from a clean checkout of the merged skill revision.",
    "destination_exists": "Inspect the existing skill directory before choosing another --skills-dir.",
    "installed_digest_mismatch": "Inspect source changes and retry from a clean checkout.",
    "pyyaml_unavailable": "Install PyYAML in the project environment or select --json.",
}


def skills_directory() -> Path:
    """Resolve the user's configured Codex home at invocation time."""
    codex_home = os.environ.get("CODEX_HOME")
    return (
        Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    ) / "skills"


def package_files(source: Path) -> list[Path]:
    """Select installable files and refuse links to paths outside the skill."""
    if not (source / "SKILL.md").is_file():
        raise ValueError("skill_manifest_missing")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("skill_symlink_unexpected")
    return [path.relative_to(source) for path in _included(source)]


def install(
    source: Path,
    skills_dir: Path,
    *,
    dry_run: bool,
    require_verified: bool = True,
) -> dict[str, Any]:
    """Copy a complete skill once and read back its file digest."""
    skills_dir = skills_dir.expanduser().resolve()
    destination = skills_dir / SKILL_NAME
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "skill_install",
        "source": str(source.resolve()),
        "destination": str(destination),
    }
    try:
        files = package_files(source)
        identity = skill_identity(source, environ={})
        if require_verified and identity["revision"].get("verified") is not True:
            raise ValueError("source_revision_unverified")
        expected_digest = tree_digest(source)
        result.update(**expected_digest, revision=identity["revision"])
        if destination.exists() or destination.is_symlink():
            raise ValueError("destination_exists")
        if dry_run:
            result["status"] = "DRY_RUN"
            return result
        skills_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}-", dir=skills_dir))
        try:
            for relative in files:
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / relative, target)
            if tree_digest(staging) != expected_digest:
                raise ValueError("installed_digest_mismatch")
            staging.rename(destination)
        except BaseException:
            shutil.rmtree(staging)
            raise
        result["status"] = "PASS"
    except (OSError, ValueError) as exc:
        reason = str(exc)
        result.update(
            status="BLOCKED",
            reason=reason,
            safe_next_step=RECOVERY.get(
                reason, "Inspect the source and destination, then retry."
            ),
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install k8s-admin-diagnostics from this checkout. Audience: human and agent.",
        epilog="Example: tools/install_k8s_admin_diagnostics.py --dry-run --json",
    )
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=skills_directory(),
        help="Codex skills directory (default: $CODEX_HOME/skills or ~/.codex/skills)",
    )
    parser.add_argument("--dry-run", action="store_true", help="show the install plan")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", help="print JSON")
    modes.add_argument("--yaml", action="store_true", help="print YAML")
    args = parser.parse_args()
    if args.yaml:
        try:
            import yaml
        except ImportError:
            print(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "kind": "skill_install",
                        "status": "BLOCKED",
                        "reason": "pyyaml_unavailable",
                        "safe_next_step": RECOVERY["pyyaml_unavailable"],
                    },
                    sort_keys=True,
                )
            )
            return 2
    result = install(SOURCE, args.skills_dir, dry_run=args.dry_run)
    if args.yaml:
        print(yaml.safe_dump(result, sort_keys=True), end="")
    elif args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"{result['status']}: {result['destination']}")
        if result.get("reason"):
            print(result["reason"], file=sys.stderr)
    return 0 if result["status"] in {"PASS", "DRY_RUN"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
