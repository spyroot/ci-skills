#!/usr/bin/env python3
"""Install the repository's CI skill into a local Codex skills directory.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SKILL_NAME = "ci-skills"
JOURNAL_NAME = f".{SKILL_NAME}.install-transaction.json"
LOCK_NAME = f".{SKILL_NAME}.install.lock"
LOCK_WAIT_SECONDS = 30
SOURCE = Path(__file__).resolve().parents[1] / SKILL_NAME
sys.path.insert(0, str(SOURCE / "lib" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_profile import AgentProfile, AgentProfileError, AgentProfileReason
from core.provenance import _git, _included, skill_identity, tree_digest
from core.signals import recoverable_signals as _recoverable_signals

RECOVERY = {
    "skill_manifest_missing": "Use a checkout containing ci-skills/SKILL.md.",
    "skill_symlink_unexpected": "Remove the symlink from the source skill tree and retry.",
    "source_revision_unverified": "Install from a clean checkout of the merged skill revision.",
    "destination_exists": "Inspect the installed skill, then use --upgrade --apply --confirm-upgrade.",
    "destination_symlink": "Choose a real skills directory; the installed skill cannot be a symlink.",
    "destination_not_directory": "Inspect the existing path; an installed skill must be a directory.",
    "incomplete_install_recover": "Run --recover --apply --confirm-recover, then retry installation.",
    "install_recovery_unsafe": "Inspect the installed skill and transaction journal before changing either.",
    "install_io_failure": "Run --recover --apply --confirm-recover, then retry installation.",
    "install_lock_timeout": "Wait for the other installer process to finish, then retry.",
    AgentProfileReason.DEADLINE_EXPIRED: "Make a new plan and choose a longer --timeout.",
    AgentProfileReason.FINGERPRINT_CHANGED: "Make a new plan from the current committed source and confirm it.",
    "installed_digest_mismatch": "Inspect source changes and retry from a clean checkout.",
    "pyyaml_unavailable": "Install PyYAML in the project environment or select --json.",
    "log_file_unwritable": "Inspect the requested log path and installed destination before retrying.",
    AgentProfileReason.PROFILE_INVALID: "Repair the agent profile in toolchain-dependencies.json.",
    AgentProfileReason.DESTINATION_UNSAFE: "Choose a real repository-local agent skills directory.",
    AgentProfileReason.REPOSITORY_UNAVAILABLE: "Run the installer from the repository that will receive the agent skill.",
    AgentProfileReason.SKILL_CONFLICT: "Inspect the existing agent skill; its bytes differ from the glab bundle.",
    AgentProfileReason.SKILL_INVALID: "Inspect the glab bundle and retry without replacing the existing skill.",
    AgentProfileReason.COMMAND_FAILED: "Run the reported command on this host to inspect its error, then retry.",
    AgentProfileReason.GLAB_UNAVAILABLE: "Install glab using the host package manager, then retry.",
    AgentProfileReason.GLAB_INCOMPATIBLE: "Upgrade glab to a release with the declared bundled skill, then rerun the plan.",
    AgentProfileReason.GLAB_REPLAN_REQUIRED: "glab is installed. Rerun --dry-run and confirm the new fingerprint to install its skill.",
    AgentProfileReason.BREW_UNAVAILABLE: "Install Homebrew from https://brew.sh/, then rerun the installer.",
    AgentProfileReason.PACKAGE_MANAGER_UNAVAILABLE: "Install glab with this Linux distribution's package manager, then retry.",
}


def skills_directory() -> Path:
    """Resolve the user's configured Codex home at invocation time."""
    codex_home = os.environ.get("CODEX_HOME")
    return (
        Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    ) / "skills"


def plan_fingerprint(plan: dict[str, Any]) -> str:
    """Bind a confirmed install to its source bytes, revision and destination."""
    revision = plan["revision"].get("value") or ""
    values = (plan["source"], plan["destination"], revision, plan["digest"])
    return hashlib.sha256("\0".join(values).encode()).hexdigest()


def confirmed_fingerprint(core_fingerprint: str, agent_plan: dict[str, Any]) -> str:
    """Bind confirmation to agent state and both installer implementations.

    :param core_fingerprint: Digest of the skill source and destination plan.
    :param agent_plan: Complete agent host and bundled-skill plan.
    :returns: SHA-256 fingerprint of the combined installation decision.
    """
    implementation = {
        name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ("install_ci_skills.py", "agent_profile.py")
    }
    decision = {
        "core": core_fingerprint,
        "agent": agent_plan,
        "implementation": implementation,
    }
    return hashlib.sha256(
        json.dumps(decision, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _present(path: Path) -> bool:
    """Include broken links when deciding whether an install path is occupied."""
    return path.is_symlink() or path.exists()


def _entry_digest(path: Path) -> str:
    """Identify a directory or preserved link without traversing link targets."""
    if path.is_symlink():
        return hashlib.sha256(b"symlink\0" + os.readlink(path).encode()).hexdigest()
    return tree_digest(path)["digest"]


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
def _mutation_lock(skills_dir: Path, *, deadline: float | None = None):
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
        lock_deadline = min(
            time.monotonic() + LOCK_WAIT_SECONDS,
            deadline if deadline is not None else float("inf"),
        )
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                if time.monotonic() >= lock_deadline:
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
    old_kind = data.get("old_kind", "directory")
    if old_kind not in {"directory", "symlink"}:
        raise ValueError("install_recovery_unsafe")
    if staging.is_symlink() or (previous.is_symlink() and old_kind != "symlink"):
        raise ValueError("install_recovery_unsafe")
    return data, staging, previous


def _recover_install_unlocked(
    skills_dir: Path, *, dry_run: bool, deadline: float | None = None
) -> dict[str, Any]:
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
        old_kind = data.get("old_kind", "directory")
        if (destination.is_symlink() and old_kind != "symlink") or (
            _present(destination)
            and not destination.is_symlink()
            and not destination.is_dir()
        ):
            raise ValueError("install_recovery_unsafe")
        if _present(destination) and _present(previous):
            if destination.is_symlink():
                raise ValueError("install_recovery_unsafe")
            if tree_digest(destination)["digest"] != data["new_digest"]:
                raise ValueError("install_recovery_unsafe")
            action = "complete_verified_activation"
        elif not _present(destination) and _present(previous):
            if _entry_digest(previous) != data["old_digest"]:
                raise ValueError("install_recovery_unsafe")
            action = "restore_previous"
        elif _present(destination) and not _present(previous):
            current = _entry_digest(destination)
            if current not in {data["old_digest"], data["new_digest"]}:
                raise ValueError("install_recovery_unsafe")
            action = "clear_unstarted_transaction"
        elif (
            not _present(destination)
            and not _present(previous)
            and data["old_digest"] == "0" * 64
        ):
            action = "clear_unstarted_transaction"
        else:
            raise ValueError("install_recovery_unsafe")
        result["action"] = action
        result["previous_version"] = str(previous) if _present(previous) else None
        if dry_run:
            result["status"] = "DRY_RUN"
            return result
        # Once recovery starts changing paths, finish it even if time expires.
        if deadline is not None and time.monotonic() >= deadline:
            raise ValueError("install_deadline_expired")
        if action == "restore_previous":
            previous.rename(destination)
        if staging.exists():
            shutil.rmtree(staging)
        _journal_path(skills_dir).unlink()
        result["status"] = "PASS"
        result["digest"] = _entry_digest(destination) if _present(destination) else None
    except (OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "install_io_failure"
        result.update(
            status="BLOCKED",
            reason=reason,
            safe_next_step=RECOVERY.get(reason, RECOVERY["install_recovery_unsafe"]),
        )
    return result


def recover_install(
    skills_dir: Path, *, dry_run: bool, deadline: float | None = None
) -> dict[str, Any]:
    """Recover under the same lock as installation and upgrade."""
    if dry_run:
        return _recover_install_unlocked(skills_dir, dry_run=True)
    try:
        with _mutation_lock(skills_dir.expanduser().resolve(), deadline=deadline):
            return _recover_install_unlocked(
                skills_dir, dry_run=False, deadline=deadline
            )
    except (OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "install_io_failure"
        return {
            "schema_version": "1.0",
            "kind": "skill_install_recovery",
            "destination": str(skills_dir.expanduser().resolve() / SKILL_NAME),
            "status": "BLOCKED",
            "reason": reason,
            "safe_next_step": RECOVERY.get(reason, RECOVERY["install_recovery_unsafe"]),
        }


def _install_unlocked(
    source: Path,
    skills_dir: Path,
    *,
    dry_run: bool,
    require_verified: bool = True,
    upgrade: bool = False,
    deadline: float | None = None,
    expected_fingerprint: str | None = None,
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
        if (
            expected_fingerprint is not None
            and plan_fingerprint(result) != expected_fingerprint
        ):
            raise ValueError("installation_fingerprint_changed")
        if destination.is_symlink() and not upgrade:
            raise ValueError("destination_symlink")
        if (
            _present(destination)
            and not destination.is_dir()
            and not destination.is_symlink()
        ):
            raise ValueError("destination_not_directory")
        if _present(destination) and not upgrade:
            raise ValueError("destination_exists")
        if _journal_path(skills_dir).exists():
            raise ValueError("incomplete_install_recover")
        if (
            upgrade
            and not destination.is_symlink()
            and destination.is_dir()
            and tree_digest(destination) == expected_digest
        ):
            result.update(
                status="DRY_RUN" if dry_run else "PASS", already_installed=True
            )
            return result
        if dry_run:
            result["status"] = "DRY_RUN"
            return result
        if deadline is not None and time.monotonic() >= deadline:
            raise ValueError("install_deadline_expired")
        skills_dir.mkdir(parents=True, exist_ok=True)
        nonce = uuid.uuid4().hex
        staging = skills_dir / f".{SKILL_NAME}.stage-{nonce}"
        previous = skills_dir / f".{SKILL_NAME}.previous-{nonce}"
        old_digest = _entry_digest(destination) if _present(destination) else "0" * 64
        old_kind = "symlink" if destination.is_symlink() else "directory"
        try:
            with _recoverable_signals():
                _write_journal(
                    skills_dir,
                    {
                        "schema_version": "1.0",
                        "nonce": nonce,
                        "old_digest": old_digest,
                        "old_kind": old_kind,
                        "new_digest": expected_digest["digest"],
                    },
                )
                staging.mkdir(mode=0o700)
                for relative in files:
                    target = staging / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source / relative, target)
                if tree_digest(staging) != expected_digest:
                    raise ValueError("installed_digest_mismatch")
                if expected_fingerprint is not None:
                    current_identity = skill_identity(source, environ={})
                    current = {
                        **tree_digest(source),
                        "revision": current_identity["revision"],
                        "source": str(source.resolve()),
                        "destination": str(destination),
                    }
                    if plan_fingerprint(current) != expected_fingerprint:
                        raise ValueError("installation_fingerprint_changed")
                if deadline is not None and time.monotonic() >= deadline:
                    raise ValueError("install_deadline_expired")
                if _present(destination):
                    destination.rename(previous)
                    result["previous_version"] = str(previous)
                staging.rename(destination)
                if tree_digest(destination) != expected_digest:
                    raise ValueError("installed_digest_mismatch")
                _journal_path(skills_dir).unlink()
        except BaseException:
            if _journal_path(skills_dir).exists():
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
    deadline: float | None = None,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Serialize the full install decision and mutation for one skill root."""
    preflight = _install_unlocked(
        source,
        skills_dir,
        dry_run=True,
        require_verified=require_verified,
        upgrade=upgrade,
        expected_fingerprint=expected_fingerprint,
    )
    if dry_run or preflight["status"] == "BLOCKED":
        return preflight
    try:
        with _mutation_lock(skills_dir.expanduser().resolve(), deadline=deadline):
            return _install_unlocked(
                source,
                skills_dir,
                dry_run=False,
                require_verified=require_verified,
                upgrade=upgrade,
                deadline=deadline,
                expected_fingerprint=expected_fingerprint,
            )
    except (OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "install_io_failure"
        return {
            **preflight,
            "status": "BLOCKED",
            "reason": reason,
            "safe_next_step": RECOVERY.get(reason, RECOVERY["install_recovery_unsafe"]),
        }


def main() -> int:
    """Plan or apply a confirmed skill and agent-profile installation.

    :returns: Zero for a valid plan or complete install, otherwise two.
    """
    started = time.monotonic()
    parser = argparse.ArgumentParser(
        description="Install ci-skills from this checkout. Audience: human and agent.",
        epilog="Example: tools/install_ci_skills.py --dry-run --json",
    )
    location = parser.add_mutually_exclusive_group()
    location.add_argument(
        "--skills-dir",
        type=Path,
        help="Codex skills directory (default: $CODEX_HOME/skills or ~/.codex/skills)",
    )
    location.add_argument(
        "--destination",
        type=Path,
        help="absolute destination ending in /ci-skills",
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
        metavar="FINGERPRINT",
        help="confirm a new installation with the dry-run fingerprint",
    )
    parser.add_argument(
        "--confirm-upgrade",
        metavar="FINGERPRINT",
        help="confirm replacement with the dry-run fingerprint",
    )
    parser.add_argument(
        "--confirm-recover",
        action="store_true",
        help="confirm recovery with --recover --apply",
    )
    parser.add_argument(
        "--timeout",
        help="apply deadline, for example 10s",
    )
    parser.add_argument("--log-format", choices=("text", "json"))
    parser.add_argument("--log-level", choices=("debug", "info", "warning", "error"))
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--run-id")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", help="print JSON")
    modes.add_argument("--yaml", action="store_true", help="print YAML")
    args = parser.parse_args()
    skills_dir = args.skills_dir or skills_directory()
    if args.destination is not None:
        if not args.destination.is_absolute() or args.destination.name != SKILL_NAME:
            parser.error("--destination must be an absolute path ending in /ci-skills")
        skills_dir = args.destination.parent
    explicit_logging = any(
        value is not None
        for value in (args.log_format, args.log_level, args.log_file, args.run_id)
    )
    args.log_format = args.log_format or "text"
    args.log_level = args.log_level or "info"
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
            "destination": str(skills_dir.expanduser().resolve() / SKILL_NAME),
            "reason": "recover_and_upgrade_conflict",
            "safe_next_step": "Run --recover first, then start a separate --upgrade.",
        }
    elif args.recover and args.apply and not args.confirm_recover:
        result = {
            "schema_version": "1.0",
            "kind": "skill_install_recovery" if args.recover else "skill_install",
            "status": "BLOCKED",
            "source": str(SOURCE.resolve()),
            "destination": str(skills_dir.expanduser().resolve() / SKILL_NAME),
            "action": "recover",
            "reason": "confirmation_required",
            "safe_next_step": "Use --confirm-recover with --recover --apply.",
        }
    elif (
        args.recover
        and args.apply
        and (not args.timeout or not re.fullmatch(r"[1-9][0-9]*s", args.timeout))
    ):
        result = {
            "schema_version": "1.0",
            "kind": "skill_install_recovery",
            "status": "BLOCKED",
            "destination": str(skills_dir.expanduser().resolve() / SKILL_NAME),
            "reason": "timeout_required",
            "safe_next_step": "Pass --timeout DURATION, for example 10s.",
        }
    elif args.recover:
        deadline = started + int(args.timeout[:-1]) if args.apply else None
        result = recover_install(skills_dir, dry_run=not args.apply, deadline=deadline)
    else:
        plan = install(SOURCE, skills_dir, dry_run=True, upgrade=args.upgrade)
        if plan["status"] == "DRY_RUN":
            try:
                repository = _git(Path.cwd(), "rev-parse", "--show-toplevel")
                if repository is None:
                    raise AgentProfileError(AgentProfileReason.REPOSITORY_UNAVAILABLE)
                agent_profile = AgentProfile(SOURCE.parent, Path(repository))
                plan["agent"] = agent_profile.plan()
            except (
                OSError,
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                reason = (
                    exc.reason.value
                    if isinstance(exc, AgentProfileError)
                    else AgentProfileReason.PROFILE_INVALID.value
                )
                plan.update(
                    status="BLOCKED",
                    reason=reason,
                    safe_next_step=RECOVERY[reason],
                )
            core_fingerprint = plan_fingerprint(plan)
            if plan["status"] == "DRY_RUN":
                plan["fingerprint"] = confirmed_fingerprint(
                    core_fingerprint, plan["agent"]
                )
            plan["source_revision"] = plan["revision"]["value"]
            plan["mode"] = "dry-run"
            plan["mutable_link"] = False
        if not args.apply or plan["status"] != "DRY_RUN":
            result = plan
        elif not args.timeout or not re.fullmatch(r"[1-9][0-9]*s", args.timeout):
            result = {
                **plan,
                "status": "BLOCKED",
                "reason": "timeout_required",
                "safe_next_step": "Pass --timeout DURATION, for example 10s.",
            }
        elif (args.confirm_upgrade if args.upgrade else args.confirm_install) != plan[
            "fingerprint"
        ]:
            result = {
                **plan,
                "status": "BLOCKED",
                "reason": "confirmation_required",
                "safe_next_step": "Pass the current dry-run fingerprint with the matching confirmation option.",
            }
        else:
            seconds = int(args.timeout[:-1])
            try:
                with _mutation_lock(
                    skills_dir.expanduser().resolve(), deadline=started + seconds
                ):
                    if (
                        confirmed_fingerprint(core_fingerprint, plan["agent"])
                        != plan["fingerprint"]
                    ):
                        raise AgentProfileError(AgentProfileReason.FINGERPRINT_CHANGED)
                    agent_result = agent_profile.apply(plan["agent"], started + seconds)
                    result = _install_unlocked(
                        SOURCE,
                        skills_dir,
                        dry_run=False,
                        upgrade=args.upgrade,
                        deadline=started + seconds,
                        expected_fingerprint=core_fingerprint,
                    )
                    result["agent"] = agent_result
            except (OSError, ValueError) as exc:
                reason = (
                    exc.reason.value
                    if isinstance(exc, AgentProfileError)
                    else AgentProfileReason.COMMAND_FAILED.value
                )
                result = {
                    **plan,
                    "status": "BLOCKED",
                    "reason": reason,
                    "safe_next_step": RECOVERY[reason],
                }
                if isinstance(exc, AgentProfileError) and exc.command:
                    result["failed_command"] = exc.command
                    result["failed_exit_code"] = exc.exit_code
            result["fingerprint"] = plan["fingerprint"]
            result["source_revision"] = result.get("revision", {}).get("value")
            result["mutable_link"] = False
    if explicit_logging:
        from core.cli import log_event

        try:
            log_event(
                args,
                result.get("kind", "skill_install"),
                "finished",
                result["status"],
                elapsed=time.monotonic() - started,
                error_class=result.get("reason"),
            )
        except OSError:
            result.update(
                status="BLOCKED",
                reason="log_file_unwritable",
                safe_next_step=RECOVERY["log_file_unwritable"],
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
