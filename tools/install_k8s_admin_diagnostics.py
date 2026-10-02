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
    "destination_exists": "Inspect the installed skill, then use --upgrade --apply --confirm-upgrade.",
    "destination_symlink": "Choose a real skills directory; the installed skill cannot be a symlink.",
    "destination_not_directory": "Inspect the existing path; an installed skill must be a directory.",
    "previous_version_exists": "Inspect the preserved previous version before another upgrade.",
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
    upgrade: bool = False,
) -> dict[str, Any]:
    """Stage and verify a skill, preserving the prior install on upgrade."""
    skills_dir = skills_dir.expanduser().resolve()
    destination = skills_dir / SKILL_NAME
    previous = skills_dir / f".{SKILL_NAME}.previous"
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "skill_install",
        "source": str(source.resolve()),
        "destination": str(destination),
        "action": "upgrade" if upgrade else "install",
    }
    try:
        files = package_files(source)
        identity = skill_identity(source, environ={})
        if require_verified and identity["revision"].get("verified") is not True:
            raise ValueError("source_revision_unverified")
        expected_digest = tree_digest(source)
        result.update(**expected_digest, revision=identity["revision"])
        if destination.is_symlink():
            raise ValueError("destination_symlink")
        if destination.exists() and not destination.is_dir():
            raise ValueError("destination_not_directory")
        if destination.exists() and not upgrade:
            raise ValueError("destination_exists")
        if destination.exists() and (previous.exists() or previous.is_symlink()):
            raise ValueError("previous_version_exists")
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
            if destination.exists():
                destination.rename(previous)
                try:
                    staging.rename(destination)
                    if tree_digest(destination) != expected_digest:
                        raise ValueError("installed_digest_mismatch")
                except BaseException:
                    if destination.exists():
                        shutil.rmtree(destination)
                    previous.rename(destination)
                    raise
                result["previous_version"] = str(previous)
            else:
                staging.rename(destination)
        except BaseException:
            if staging.exists():
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="show the install plan (default)"
    )
    mode.add_argument("--apply", action="store_true", help="copy the verified skill")
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="replace an installed skill while preserving its previous version",
    )
    parser.add_argument(
        "--confirm-install",
        action="store_true",
        help="confirm a new installation with --apply",
    )
    parser.add_argument(
        "--confirm-upgrade",
        action="store_true",
        help="confirm replacement with --apply --upgrade",
    )
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
    if args.apply and not (
        args.confirm_upgrade if args.upgrade else args.confirm_install
    ):
        result = {
            "schema_version": "1.0",
            "kind": "skill_install",
            "status": "BLOCKED",
            "source": str(SOURCE.resolve()),
            "destination": str(args.skills_dir.expanduser().resolve() / SKILL_NAME),
            "action": "upgrade" if args.upgrade else "install",
            "reason": "confirmation_required",
            "safe_next_step": (
                "Use --confirm-upgrade with --upgrade --apply, "
                "or --confirm-install with --apply."
            ),
        }
    else:
        result = install(
            SOURCE,
            args.skills_dir,
            dry_run=not args.apply,
            upgrade=args.upgrade,
        )
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
