#!/usr/bin/env python3
"""Install the repository's diagnostic skill into a local Codex skills directory."""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SKILL_NAME = "k8s-admin-diagnostics"
JOURNAL_NAME = f".{SKILL_NAME}.install-transaction.json"
LOCK_NAME = f".{SKILL_NAME}.install.lock"
LOCK_WAIT_SECONDS = 30
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
    "incomplete_install_recover": "Run --recover --apply --confirm-recover, then retry installation.",
    "install_recovery_unsafe": "Inspect the installed skill and transaction journal before changing either.",
    "install_io_failure": "Run --recover --apply --confirm-recover, then retry installation.",
    "install_lock_timeout": "Wait for the other installer process to finish, then retry.",
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


def _journal_path(skills_dir: Path) -> Path:
    return skills_dir / JOURNAL_NAME


@contextmanager
def _mutation_lock(skills_dir: Path):
    """Serialize destination inspection and mutation across local processes."""
    skills_dir.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        skills_dir / LOCK_NAME,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise ValueError("install_recovery_unsafe")
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                if time.monotonic() >= deadline:
                    raise ValueError("install_lock_timeout") from exc
                time.sleep(0.1)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _write_journal(skills_dir: Path, data: dict[str, Any]) -> None:
    """Publish a complete transaction atomically before moving the old skill."""
    descriptor, name = tempfile.mkstemp(
        prefix=f".{SKILL_NAME}.journal-", dir=skills_dir
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, _journal_path(skills_dir))
    finally:
        temporary.unlink(missing_ok=True)


def _read_journal(skills_dir: Path) -> tuple[dict[str, Any], Path, Path]:
    journal = _journal_path(skills_dir)
    metadata = journal.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        raise ValueError("install_recovery_unsafe")
    try:
        data = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("install_recovery_unsafe") from exc
    if not isinstance(data, dict) or data.get("schema_version") != "1.0":
        raise ValueError("install_recovery_unsafe")
    nonce = data.get("nonce")
    if (
        not isinstance(nonce, str)
        or len(nonce) != 32
        or any(char not in "0123456789abcdef" for char in nonce)
    ):
        raise ValueError("install_recovery_unsafe")
    if not all(
        isinstance(data.get(key), str) and len(data[key]) == 64
        for key in ("old_digest", "new_digest")
    ):
        raise ValueError("install_recovery_unsafe")
    staging = skills_dir / f".{SKILL_NAME}.stage-{nonce}"
    previous = skills_dir / f".{SKILL_NAME}.previous-{nonce}"
    if staging.is_symlink() or previous.is_symlink():
        raise ValueError("install_recovery_unsafe")
    return data, staging, previous


def _recover_install_unlocked(skills_dir: Path, *, dry_run: bool) -> dict[str, Any]:
    """Reconcile one interrupted install without deleting an unknown skill."""
    skills_dir = skills_dir.expanduser().resolve()
    destination = skills_dir / SKILL_NAME
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "skill_install_recovery",
        "destination": str(destination),
    }
    try:
        data, staging, previous = _read_journal(skills_dir)
        if destination.is_symlink() or (
            destination.exists() and not destination.is_dir()
        ):
            raise ValueError("install_recovery_unsafe")
        if destination.exists() and previous.exists():
            if tree_digest(destination)["digest"] != data["new_digest"]:
                raise ValueError("install_recovery_unsafe")
            action = "complete_verified_activation"
        elif not destination.exists() and previous.exists():
            if tree_digest(previous)["digest"] != data["old_digest"]:
                raise ValueError("install_recovery_unsafe")
            action = "restore_previous"
        elif destination.exists() and not previous.exists():
            current = tree_digest(destination)["digest"]
            if current not in {data["old_digest"], data["new_digest"]}:
                raise ValueError("install_recovery_unsafe")
            action = "clear_unstarted_transaction"
        elif (
            not destination.exists()
            and not previous.exists()
            and data["old_digest"] == "0" * 64
        ):
            action = "clear_unstarted_transaction"
        else:
            raise ValueError("install_recovery_unsafe")
        result["action"] = action
        result["previous_version"] = str(previous) if previous.exists() else None
        if dry_run:
            result["status"] = "DRY_RUN"
            return result
        if action == "restore_previous":
            previous.rename(destination)
        if staging.exists():
            shutil.rmtree(staging)
        _journal_path(skills_dir).unlink()
        result["status"] = "PASS"
        result["digest"] = (
            tree_digest(destination)["digest"] if destination.exists() else None
        )
    except (OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "install_io_failure"
        result.update(
            status="BLOCKED",
            reason=reason,
            safe_next_step=RECOVERY.get(reason, RECOVERY["install_recovery_unsafe"]),
        )
    return result


def recover_install(skills_dir: Path, *, dry_run: bool) -> dict[str, Any]:
    """Recover under the same lock as installation and upgrade."""
    if dry_run:
        return _recover_install_unlocked(skills_dir, dry_run=True)
    with _mutation_lock(skills_dir.expanduser().resolve()):
        return _recover_install_unlocked(skills_dir, dry_run=False)


def _install_unlocked(
    source: Path,
    skills_dir: Path,
    *,
    dry_run: bool,
    require_verified: bool = True,
    upgrade: bool = False,
) -> dict[str, Any]:
    """Stage and verify a skill with a recoverable cross-rename journal."""
    skills_dir = skills_dir.expanduser().resolve()
    destination = skills_dir / SKILL_NAME
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
        if _journal_path(skills_dir).exists():
            raise ValueError("incomplete_install_recover")
        if dry_run:
            result["status"] = "DRY_RUN"
            return result
        skills_dir.mkdir(parents=True, exist_ok=True)
        nonce = uuid.uuid4().hex
        staging = skills_dir / f".{SKILL_NAME}.stage-{nonce}"
        previous = skills_dir / f".{SKILL_NAME}.previous-{nonce}"
        old_digest = (
            tree_digest(destination)["digest"] if destination.exists() else "0" * 64
        )
        _write_journal(
            skills_dir,
            {
                "schema_version": "1.0",
                "nonce": nonce,
                "old_digest": old_digest,
                "new_digest": expected_digest["digest"],
            },
        )
        try:
            staging.mkdir(mode=0o700)
            for relative in files:
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / relative, target)
            if tree_digest(staging) != expected_digest:
                raise ValueError("installed_digest_mismatch")
            if destination.exists():
                destination.rename(previous)
                result["previous_version"] = str(previous)
            staging.rename(destination)
            if tree_digest(destination) != expected_digest:
                raise ValueError("installed_digest_mismatch")
            _journal_path(skills_dir).unlink()
        except BaseException:
            recovered = _recover_install_unlocked(skills_dir, dry_run=False)
            if recovered["status"] != "PASS":
                raise ValueError("install_recovery_unsafe") from None
            raise
        result["status"] = "PASS"
    except (OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "install_io_failure"
        result.update(
            status="BLOCKED",
            reason=reason,
            safe_next_step=RECOVERY.get(
                reason, "Inspect the source and destination, then retry."
            ),
        )
    return result


def install(
    source: Path,
    skills_dir: Path,
    *,
    dry_run: bool,
    require_verified: bool = True,
    upgrade: bool = False,
) -> dict[str, Any]:
    """Serialize the full install decision and mutation for one skill root."""
    preflight = _install_unlocked(
        source,
        skills_dir,
        dry_run=True,
        require_verified=require_verified,
        upgrade=upgrade,
    )
    if dry_run or preflight["status"] == "BLOCKED":
        return preflight
    with _mutation_lock(skills_dir.expanduser().resolve()):
        return _install_unlocked(
            source,
            skills_dir,
            dry_run=False,
            require_verified=require_verified,
            upgrade=upgrade,
        )


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
        help="replace an installed skill and preserve its previous version",
    )
    parser.add_argument(
        "--recover",
        action="store_true",
        help="reconcile an interrupted install or upgrade",
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
    parser.add_argument(
        "--confirm-recover",
        action="store_true",
        help="confirm recovery with --recover --apply",
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
    if args.recover and args.upgrade:
        result = {
            "schema_version": "1.0",
            "kind": "skill_install_recovery",
            "status": "BLOCKED",
            "destination": str(args.skills_dir.expanduser().resolve() / SKILL_NAME),
            "reason": "recover_and_upgrade_conflict",
            "safe_next_step": "Run --recover first, then start a separate --upgrade.",
        }
    elif args.apply and not (
        args.confirm_recover
        if args.recover
        else args.confirm_upgrade
        if args.upgrade
        else args.confirm_install
    ):
        result = {
            "schema_version": "1.0",
            "kind": "skill_install_recovery" if args.recover else "skill_install",
            "status": "BLOCKED",
            "source": str(SOURCE.resolve()),
            "destination": str(args.skills_dir.expanduser().resolve() / SKILL_NAME),
            "action": "recover"
            if args.recover
            else "upgrade"
            if args.upgrade
            else "install",
            "reason": "confirmation_required",
            "safe_next_step": (
                "Use --confirm-recover with --recover --apply, "
                "--confirm-upgrade with --upgrade --apply, "
                "or --confirm-install with --apply."
            ),
        }
    elif args.recover:
        result = recover_install(args.skills_dir, dry_run=not args.apply)
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
