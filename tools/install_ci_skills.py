#!/usr/bin/env python3
"""Install the repository's CI skill into a local Codex or Claude skills directory.

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
import runpy
import shutil
import stat
import sys
import tempfile
import time
import uuid
from collections.abc import (
    Callable,
    Iterator,
    Mapping,
    MutableMapping,
    MutableSequence,
    Sequence,
)
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Final, Optional

SKILL_NAME = "ci-skills"
SKILLS_DIRECTORY_NAME: Final = "skills"
CODEX_HOME_DIRECTORY_NAME: Final = ".codex"
CLAUDE_HOME_DIRECTORY_NAME: Final = ".claude"
JOURNAL_NAME = f".{SKILL_NAME}.install-transaction.json"
LOCK_NAME = f".{SKILL_NAME}.install.lock"
LOCK_WAIT_SECONDS = 30
SOURCE = Path(__file__).resolve().parents[1] / SKILL_NAME
runpy.run_path(str(SOURCE / "bin" / "_bootstrap.py"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_profile import AgentProfile, AgentProfileError, AgentProfileReason
from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json_object,
    json_string,
)
from lib.python.core.provenance import (
    INSTALLATION_IDENTITY_KIND,
    INSTALLATION_IDENTITY_NAME,
    INSTALLATION_IDENTITY_SCHEMA_VERSION,
    INSTALLATION_IDENTITY_STATUS,
    Revision,
    TreeDigest,
    _git,
    _included,
    skill_identity,
    tree_digest,
)
from lib.python.sys.signals import recoverable_signals as _recoverable_signals

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
    """Resolve the default Codex skills directory at invocation time.

    :returns: CODEX_HOME/skills when configured, otherwise ~/.codex/skills.
    """
    codex_home = os.environ.get("CODEX_HOME")
    return (
        Path(codex_home).expanduser()
        if codex_home
        else Path.home() / CODEX_HOME_DIRECTORY_NAME
    ) / SKILLS_DIRECTORY_NAME


class InstallScope(StrEnum):
    """Named skills locations accepted by ``--scope``."""

    CODEX_HOME = "codex-home"
    CLAUDE_USER = "claude-user"
    CLAUDE_PROJECT = "claude-project"


def repository_root(working_directory: Path) -> Path:
    """Resolve the Git repository that contains the working directory.

    :param working_directory: Directory inside the consuming repository.
    :returns: Repository root reported by ``git rev-parse --show-toplevel``.
    :raises AgentProfileError: ``REPOSITORY_UNAVAILABLE`` when the directory is not
        inside a Git repository or Git cannot be run.
    """
    repository = _git(working_directory, "rev-parse", "--show-toplevel")
    if repository is None:
        raise AgentProfileError(AgentProfileReason.REPOSITORY_UNAVAILABLE)
    return Path(repository)


def claude_user_skills_directory() -> Path:
    """Resolve the Claude user skills directory at invocation time.

    :returns: ``~/.claude/skills``.
    """
    return Path.home() / CLAUDE_HOME_DIRECTORY_NAME / SKILLS_DIRECTORY_NAME


def claude_project_skills_directory(working_directory: Path) -> Path:
    """Resolve the Claude project skills directory of the enclosing Git repository.

    :param working_directory: Directory inside the repository that receives the skill.
    :returns: ``<git root>/.claude/skills``.
    :raises AgentProfileError: ``REPOSITORY_UNAVAILABLE`` when ``working_directory``
        is not inside a Git repository or Git cannot be run.
    """  # noqa: DOC502 - repository_root raises AgentProfileError.
    return (
        repository_root(working_directory)
        / CLAUDE_HOME_DIRECTORY_NAME
        / SKILLS_DIRECTORY_NAME
    )


SCOPE_SKILLS_DIRECTORIES: Final[Mapping[InstallScope, Callable[[Path], Path]]] = (
    MappingProxyType(
        {
            InstallScope.CODEX_HOME: lambda _working_directory: skills_directory(),
            InstallScope.CLAUDE_USER: (
                lambda _working_directory: claude_user_skills_directory()
            ),
            InstallScope.CLAUDE_PROJECT: claude_project_skills_directory,
        }
    )
)


def scope_skills_directory(scope: InstallScope, working_directory: Path) -> Path:
    """Resolve the parent skills directory named by one ``--scope`` value.

    :param scope: Requested install scope.
    :param working_directory: Directory used to find the Git repository for
        ``claude-project``; user scopes ignore it.
    :returns: Directory that receives the ``ci-skills`` entry.
    :raises AgentProfileError: ``REPOSITORY_UNAVAILABLE`` when ``claude-project`` is
        requested outside a Git repository.
    """  # noqa: DOC502 - The claude-project resolver raises AgentProfileError.
    return SCOPE_SKILLS_DIRECTORIES[scope](working_directory)


def plan_fingerprint(plan: JSONObject) -> str:
    """Bind an installation decision to source, destination, revision, and bytes.

    :param plan: Install plan containing revision, source, destination, and digest.
    :returns: SHA-256 hexadecimal confirmation fingerprint.
    :raises KeyError: If the plan lacks revision, source, destination, or digest.
    :raises TypeError: If revision is not an object or a fingerprint field is not text.
    """  # noqa: DOC503 - Required plan fields are accessed by key.
    revision = plan["revision"]
    if not isinstance(revision, Mapping):
        raise TypeError("invalid_install_revision")
    values = (
        plan["source"],
        plan["destination"],
        revision.get("value") or "",
        plan["digest"],
    )
    fields: MutableSequence[str] = []
    for value in values:
        if not isinstance(value, str):
            raise TypeError("invalid_install_fingerprint")
        fields.append(value)
    return hashlib.sha256("\0".join(fields).encode()).hexdigest()


def confirmed_fingerprint(core_fingerprint: str, agent_plan: JSONObject) -> str:
    """Bind confirmation to agent state and both installer implementations.

    :param core_fingerprint: Digest of the skill source and destination plan.
    :param agent_plan: Complete agent host and bundled-skill plan.
    :returns: SHA-256 fingerprint of the combined installation decision.
    :raises OSError: If either installer implementation cannot be read for hashing.
    """  # noqa: DOC502 - Installer implementation files are read for hashing.
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
    """Treat an existing path or broken symlink as an occupied install slot.

    :param path: Candidate skill destination or transaction path.
    :returns: True if the path exists or is a symlink.
    """
    return path.is_symlink() or path.exists()


def _entry_digest(path: Path) -> str:
    """Digest a skill directory or the text of a preserved symlink.

    :param path: Existing directory or symlink to identify without following links.
    :returns: SHA-256 hexadecimal digest for the selected entry.
    """
    if path.is_symlink():
        return hashlib.sha256(b"symlink\0" + os.readlink(path).encode()).hexdigest()
    return tree_digest(path)["digest"]


def package_files(source: Path) -> Sequence[Path]:
    """Select installable skill files after rejecting missing manifests and links.

    :param source: Source skill directory to inventory.
    :returns: Included file paths relative to the source directory.
    :raises ValueError: If SKILL.md is missing or any source entry is a symlink.
    """
    if not (source / "SKILL.md").is_file():
        raise ValueError("skill_manifest_missing")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("skill_symlink_unexpected")
    return [path.relative_to(source) for path in _included(source)]


def _write_installation_identity(
    staging: Path, digest: TreeDigest, revision: Revision
) -> None:
    """Write the verified source identity beside staged skill bytes.

    :param staging: Newly created staging directory receiving the identity record.
    :param digest: Source tree digest, algorithm, and file count.
    :param revision: Source revision identity; unverified revisions are skipped.
    :raises OSError: If the exclusive identity file cannot be created or synced.
    """  # noqa: DOC502 - Identity creation, writing, and syncing propagate OSError.
    if revision.get("verified") is not True:
        return
    record = {
        "schema_version": INSTALLATION_IDENTITY_SCHEMA_VERSION,
        "kind": INSTALLATION_IDENTITY_KIND,
        "status": INSTALLATION_IDENTITY_STATUS,
        **digest,
        "revision": revision,
    }
    path = staging / INSTALLATION_IDENTITY_NAME
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(record, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _installed_matches(
    destination: Path,
    expected_digest: TreeDigest,
    expected_revision: Revision,
) -> bool:
    """Check installed bytes and verified revision before an idempotent upgrade.

    :param destination: Existing installed skill directory.
    :param expected_digest: Digest mapping computed from the source skill.
    :param expected_revision: Source revision identity required for a match.
    :returns: True if bytes match and verified revision matches when required.
    """
    if tree_digest(destination) != expected_digest:
        return False
    if expected_revision.get("verified") is not True:
        return True
    installed = skill_identity(destination, environ={})
    revision = installed.get("revision")
    return (
        isinstance(revision, dict)
        and revision.get("verified") is True
        and revision.get("value") == expected_revision.get("value")
    )


def _journal_path(skills_dir: Path) -> Path:
    """Locate the install transaction journal inside the skills directory.

    :param skills_dir: Parent directory for the installed skill.
    :returns: Path to the fixed transaction journal name.
    """
    return skills_dir / JOURNAL_NAME


@contextmanager
def _mutation_lock(
    skills_dir: Path, *, deadline: Optional[float] = None
) -> Iterator[None]:
    """Hold an exclusive file lock while inspecting and changing a skill install.

    :param skills_dir: Parent directory containing the installer lock file.
    :param deadline: Optional absolute monotonic deadline for lock acquisition.
    :yields: Control to the caller while the exclusive lock is held.
    :raises ValueError: If the lock file is unsafe or acquisition times out.
    :raises OSError: If the lock file cannot be opened or locking fails.
    :ytype: None
    """
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


def _write_journal(skills_dir: Path, data: JSONObject) -> None:
    """Publish a complete install transaction through an exclusive hard link.

    :param skills_dir: Directory receiving the transaction journal.
    :param data: Transaction nonce, digests, and previous-entry kind.
    :raises OSError: If the temporary journal cannot be written or published.
    """  # noqa: DOC502 - Journal staging, linking, and cleanup propagate OSError.
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


def _read_journal(skills_dir: Path) -> tuple[JSONObject, Path, Path]:
    """Validate an interrupted-install journal and derive its transaction paths.

    :param skills_dir: Directory holding the journal and staged entries.
    :returns: Validated journal data, staging path, and previous-version path.
    :raises ValueError: If the journal or related symlink state is unsafe.
    :raises OSError: If the journal metadata cannot be read.
    """  # noqa: DOC503 - Journal metadata access outside the decoder guard can fail.
    journal = _journal_path(skills_dir)
    metadata = journal.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        raise ValueError("install_recovery_unsafe")
    try:
        data = response_decoder().decode(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("install_recovery_unsafe") from exc
    if not isinstance(data, Mapping) or data.get("schema_version") != "1.0":
        raise ValueError("install_recovery_unsafe")
    nonce = data.get("nonce")
    if (
        not isinstance(nonce, str)
        or len(nonce) != 32
        or any(char not in "0123456789abcdef" for char in nonce)
    ):
        raise ValueError("install_recovery_unsafe")
    for key in ("old_digest", "new_digest"):
        digest = data.get(key)
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("install_recovery_unsafe")
    staging = skills_dir / f".{SKILL_NAME}.stage-{nonce}"
    previous = skills_dir / f".{SKILL_NAME}.previous-{nonce}"
    old_kind = data.get("old_kind", "directory")
    if not isinstance(old_kind, str) or old_kind not in {"directory", "symlink"}:
        raise ValueError("install_recovery_unsafe")
    if staging.is_symlink() or (previous.is_symlink() and old_kind != "symlink"):
        raise ValueError("install_recovery_unsafe")
    return data, staging, previous


def _recover_install_unlocked(
    skills_dir: Path, *, dry_run: bool, deadline: Optional[float] = None
) -> MutableMapping[str, JSONValue]:
    """Reconcile a journaled install from verified active and previous entries.

    :param skills_dir: Parent directory containing the interrupted transaction.
    :param dry_run: Whether to report the recovery action without changing paths.
    :param deadline: Optional absolute monotonic deadline before recovery starts.
    :returns: Recovery result with action and PASS, DRY_RUN, or BLOCKED status.
    :raises OSError: If the selected parent path cannot be resolved before recovery.

    Journal validation and transaction I/O failures become a BLOCKED result.
    """  # noqa: DOC503 - transaction ValueError is caught; initial path resolution can raise.
    skills_dir = skills_dir.expanduser().resolve()
    destination = skills_dir / SKILL_NAME
    result: MutableMapping[str, JSONValue] = {
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
    skills_dir: Path, *, dry_run: bool, deadline: Optional[float] = None
) -> MutableMapping[str, JSONValue]:
    """Run install recovery under the shared mutation lock when applying.

    :param skills_dir: Parent directory containing an interrupted install.
    :param dry_run: Whether to inspect and plan without modifying paths.
    :param deadline: Optional absolute monotonic deadline for lock and recovery.
    :returns: Recovery result with PASS, DRY_RUN, or BLOCKED status.
    """
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
    deadline: Optional[float] = None,
    expected_fingerprint: Optional[str] = None,
) -> MutableMapping[str, JSONValue]:
    """Stage, verify, activate, and journal one installation decision.

    :param source: Source skill tree whose bytes and identity are installed.
    :param skills_dir: Parent directory receiving the installed skill.
    :param dry_run: Whether to return a plan without changing install paths.
    :param require_verified: Whether source revision proof is mandatory.
    :param upgrade: Whether an occupied destination may be replaced.
    :param deadline: Optional absolute monotonic deadline before activation.
    :param expected_fingerprint: Optional digest of the confirmed source plan.
    :returns: Install result with PASS or DRY_RUN status, or BLOCKED for I/O,
        validation, or unsuccessful recovery within the transaction.
    :raises BaseException: If an interruption or an error outside the I/O and
        validation handlers escapes; activation failures trigger rollback first.
    """  # noqa: DOC503 - Explicit ValueError paths are caught and reported as BLOCKED.
    skills_dir = skills_dir.expanduser().resolve()
    destination = skills_dir / SKILL_NAME
    result: MutableMapping[str, JSONValue] = {
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
        result.update(
            copy_json_object(expected_digest),
            revision=copy_json_object(identity["revision"]),
        )
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
            and _installed_matches(destination, expected_digest, identity["revision"])
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
                _write_installation_identity(
                    staging, expected_digest, identity["revision"]
                )
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
                    if (
                        plan_fingerprint(copy_json_object(current))
                        != expected_fingerprint
                    ):
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
    deadline: Optional[float] = None,
    expected_fingerprint: Optional[str] = None,
) -> MutableMapping[str, JSONValue]:
    """Preflight an install and serialize its confirmed mutation under a lock.

    :param source: Source skill tree to fingerprint and copy.
    :param skills_dir: Parent directory receiving the installed skill.
    :param dry_run: Whether to return only the installation plan.
    :param require_verified: Whether source revision proof is mandatory.
    :param upgrade: Whether an occupied destination may be replaced.
    :param deadline: Optional absolute monotonic deadline for apply.
    :param expected_fingerprint: Optional digest bound to the earlier plan.
    :returns: Install result with PASS, DRY_RUN, or BLOCKED status.
    """
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


@dataclass(frozen=True)
class PreparedInstall:
    """Required apply inputs created together by successful install planning.

    :param profile: Agent-profile installer bound to the consuming repository.
    :param agent_plan: Agent dependencies and bundled-skill observations to confirm.
    :param core_fingerprint: Confirmation digest for source and destination skill bytes.

    .. attribute :: profile
        :type: AgentProfile

        Agent-profile installer bound to the consuming repository.

    .. attribute :: agent_plan
        :type: JSONObject

        Agent dependencies and bundled-skill observations to confirm.

    .. attribute :: core_fingerprint
        :type: str

        Confirmation digest for source and destination skill bytes.
    """

    profile: AgentProfile
    agent_plan: JSONObject
    core_fingerprint: str


class InstallerCLI:
    """Thin command-line adapter for the reusable installer operations."""

    def __init__(
        self,
        parser: argparse.ArgumentParser,
        args: argparse.Namespace,
        started: float,
    ):
        """Normalize parsed arguments without changing installer state.

        :param parser: Parser used to report argument errors consistently.
        :param args: Parsed command-line namespace from ``argparse``.
        :param started: Monotonic timestamp captured before parsing began.
        :raises SystemExit: If the explicit destination is not an absolute skill path,
            or ``--scope claude-project`` runs outside a Git repository.
        :raises OSError: If the destination cannot be resolved on this filesystem.
        """  # noqa: DOC502 - Destination resolution and parser errors propagate failures.
        self.parser = parser
        self.args = args
        self.started = started
        self.skills_dir = self._resolve_skills_dir()
        self.destination = self.skills_dir.expanduser().resolve() / SKILL_NAME
        self.explicit_logging = any(
            value is not None
            for value in (
                args.log_format,
                args.log_level,
                args.log_file,
                args.run_id,
            )
        )
        self.args.log_format = self.args.log_format or "text"
        self.args.log_level = self.args.log_level or "info"
        self.yaml: Optional[ModuleType] = None

    @staticmethod
    def build_parser() -> argparse.ArgumentParser:
        """Build the non-interactive installer argument parser.

        :returns: Parser with install, upgrade, recovery, logging, and output flags.
        """
        parser = argparse.ArgumentParser(
            description=(
                "Install ci-skills from this checkout. Audience: human and agent."
            ),
            epilog="Example: tools/install_ci_skills.py --dry-run --json",
        )
        location = parser.add_mutually_exclusive_group()
        location.add_argument(
            "--skills-dir",
            type=Path,
            help=(
                "Codex skills directory "
                "(default: $CODEX_HOME/skills or ~/.codex/skills)"
            ),
        )
        location.add_argument(
            "--scope",
            choices=tuple(scope.value for scope in InstallScope),
            help=(
                "named skills directory: codex-home ($CODEX_HOME/skills or "
                "~/.codex/skills), claude-user (~/.claude/skills), "
                "claude-project (<git root>/.claude/skills of the current directory)"
            ),
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
        mode.add_argument(
            "--apply", action="store_true", help="copy the verified skill"
        )
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
        parser.add_argument(
            "--log-level", choices=("debug", "info", "warning", "error")
        )
        parser.add_argument("--log-file", type=Path)
        parser.add_argument("--run-id")
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument("--json", action="store_true", help="print JSON")
        modes.add_argument("--yaml", action="store_true", help="print YAML")
        return parser

    @classmethod
    def from_command_line(cls, started: float) -> InstallerCLI:
        """Parse process arguments and create the CLI adapter.

        :param started: Monotonic timestamp captured before parser construction.
        :returns: Initialized command-line adapter.
        :raises SystemExit: If argparse rejects the supplied command line.
        :raises OSError: If initialization cannot resolve the destination on this filesystem.
        """  # noqa: DOC502 - Parsing and adapter initialization propagate failures.
        parser = cls.build_parser()
        return cls(parser, parser.parse_args(), started)

    def run(self) -> int:
        """Execute the requested installer mode and emit one bounded result.

        :returns: Zero for PASS or DRY_RUN results, otherwise two.
        """
        if not self._ensure_yaml_available():
            return 2
        result = self._dispatch()
        result = self._log_if_requested(result)
        self._emit(result)
        return 0 if result["status"] in {"PASS", "DRY_RUN"} else 2

    def _resolve_skills_dir(self) -> Path:
        """Resolve the parent skills directory from mutually exclusive inputs.

        :returns: Directory containing the installed ``ci-skills`` entry.
        :raises SystemExit: If ``--destination`` is not an absolute ci-skills path,
            or a repository scope runs outside a Git repository.
        """  # noqa: DOC502 - parser.error exits when the destination is invalid.
        if self.args.scope is not None:
            try:
                return scope_skills_directory(InstallScope(self.args.scope), Path.cwd())
            except AgentProfileError as exc:
                self.parser.error(
                    f"--scope {self.args.scope}: {exc.reason}: {RECOVERY[exc.reason]}"
                )
        skills_dir = self.args.skills_dir or skills_directory()
        if self.args.destination is not None:
            if (
                not self.args.destination.is_absolute()
                or self.args.destination.name != SKILL_NAME
            ):
                self.parser.error(
                    "--destination must be an absolute path ending in /ci-skills"
                )
            skills_dir = self.args.destination.parent
        return skills_dir

    def _ensure_yaml_available(self) -> bool:
        """Load PyYAML only when YAML output was requested.

        :returns: True when output can proceed; false after emitting BLOCKED JSON.
        """
        if not self.args.yaml:
            return True
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
            return False
        self.yaml = yaml
        return True

    def _dispatch(self) -> MutableMapping[str, JSONValue]:
        """Route the command to recovery or install planning/apply logic.

        :returns: Structured result ready for optional logging and output.
        """
        if self.args.recover:
            return self._recover()
        return self._install_or_plan()

    def _recover(self) -> MutableMapping[str, JSONValue]:
        """Plan or apply interrupted-install recovery.

        :returns: Recovery result preserving the legacy validation order.
        """
        if self.args.upgrade:
            return {
                "schema_version": "1.0",
                "kind": "skill_install_recovery",
                "status": "BLOCKED",
                "destination": str(self.destination),
                "reason": "recover_and_upgrade_conflict",
                "safe_next_step": (
                    "Run --recover first, then start a separate --upgrade."
                ),
            }
        if self.args.apply and not self.args.confirm_recover:
            return {
                "schema_version": "1.0",
                "kind": "skill_install_recovery",
                "status": "BLOCKED",
                "source": str(SOURCE.resolve()),
                "destination": str(self.destination),
                "action": "recover",
                "reason": "confirmation_required",
                "safe_next_step": "Use --confirm-recover with --recover --apply.",
            }
        if self.args.apply and not self._valid_timeout():
            return {
                "schema_version": "1.0",
                "kind": "skill_install_recovery",
                "status": "BLOCKED",
                "destination": str(self.destination),
                "reason": "timeout_required",
                "safe_next_step": "Pass --timeout DURATION, for example 10s.",
            }
        deadline = self._deadline() if self.args.apply else None
        return recover_install(
            self.skills_dir,
            dry_run=not self.args.apply,
            deadline=deadline,
        )

    def _install_or_plan(self) -> MutableMapping[str, JSONValue]:
        """Build a plan, then apply it only after timeout and fingerprint checks.

        :returns: Installation plan, completed installation, or classified failure.
        :raises KeyError: If a core plan lacks its required revision or confirmation.
        :raises TypeError: If the core plan supplies a non-object revision.
        """  # noqa: DOC503 - Required plan fields are indexed outside validation handlers.
        plan = install(SOURCE, self.skills_dir, dry_run=True, upgrade=self.args.upgrade)
        if plan["status"] != "DRY_RUN":
            return plan
        revision = plan["revision"]
        if not isinstance(revision, Mapping):
            raise TypeError("invalid_install_revision")
        plan.update(
            source_revision=revision["value"], mode="dry-run", mutable_link=False
        )
        try:
            prepared = self._prepare_install(plan)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            reason = (
                exc.reason.value
                if isinstance(exc, AgentProfileError)
                else AgentProfileReason.PROFILE_INVALID.value
            )
            plan.update(
                status="BLOCKED", reason=reason, safe_next_step=RECOVERY[reason]
            )
            return plan
        if not self.args.apply:
            return plan
        if not self._valid_timeout():
            return {
                **plan,
                "status": "BLOCKED",
                "reason": "timeout_required",
                "safe_next_step": "Pass --timeout DURATION, for example 10s.",
            }
        if self._confirmation() != plan["fingerprint"]:
            return {
                **plan,
                "status": "BLOCKED",
                "reason": "confirmation_required",
                "safe_next_step": (
                    "Pass the current dry-run fingerprint with the matching "
                    "confirmation option."
                ),
            }
        return self._apply_confirmed(plan, prepared)

    def _prepare_install(self, plan: MutableMapping[str, JSONValue]) -> PreparedInstall:
        """Attach agent-profile evidence and confirmation to a valid source plan.

        :param plan: Successful core dry-run plan, updated with agent and fingerprint fields.
        :returns: Concrete profile, agent plan, and fingerprint required for apply.
        :raises AgentProfileError: If the consuming repository or its agent profile
            cannot be resolved, or declared dependencies cannot be inspected.
        :raises OSError: If source files cannot be read while planning or fingerprinting.
        :raises KeyError: If source or profile metadata lacks a required field.
        :raises TypeError: If source or profile metadata has an invalid shape.
        :raises ValueError: If source or profile metadata cannot be decoded or validated.
        """  # noqa: DOC503 - Profile construction and fingerprint helpers propagate failures.
        profile = AgentProfile(SOURCE.parent, repository_root(Path.cwd()))
        agent_plan = profile.plan()
        fingerprint = plan_fingerprint(plan)
        plan["agent"] = agent_plan
        plan["fingerprint"] = confirmed_fingerprint(fingerprint, agent_plan)
        return PreparedInstall(profile, agent_plan, fingerprint)

    def _apply_confirmed(
        self,
        plan: Mapping[str, JSONValue],
        prepared: PreparedInstall,
    ) -> MutableMapping[str, JSONValue]:
        """Apply a confirmed core and agent plan under the existing installer lock.

        :param plan: Dry-run report whose confirmation matched the command line.
        :param prepared: Required profile and fingerprints produced during planning.
        :returns: Applied installation or a BLOCKED result for profile, lock, or
            installation failure, retaining the confirmation and observed revision.
        :raises KeyError: If a caller supplies a plan without its confirmation field.
        :raises ValueError: If timeout text is malformed before the protected apply begins.
        """  # noqa: DOC503 - AgentProfileError is caught as ValueError; plan indexing may fail.
        deadline = self._deadline()
        try:
            with _mutation_lock(
                self.skills_dir.expanduser().resolve(), deadline=deadline
            ):
                if (
                    confirmed_fingerprint(
                        prepared.core_fingerprint, prepared.agent_plan
                    )
                    != plan["fingerprint"]
                ):
                    raise AgentProfileError(AgentProfileReason.FINGERPRINT_CHANGED)
                agent_result = prepared.profile.apply(prepared.agent_plan, deadline)
                result = _install_unlocked(
                    SOURCE,
                    self.skills_dir,
                    dry_run=False,
                    upgrade=self.args.upgrade,
                    deadline=deadline,
                    expected_fingerprint=prepared.core_fingerprint,
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
        revision = result.get("revision", {})
        result["fingerprint"] = plan["fingerprint"]
        result["source_revision"] = (
            revision.get("value") if isinstance(revision, Mapping) else None
        )
        result["mutable_link"] = False
        return result

    def _log_if_requested(
        self, result: MutableMapping[str, JSONValue]
    ) -> MutableMapping[str, JSONValue]:
        """Write the optional CLI log event after installer execution.

        :param result: Structured result to report and possibly modify on log failure.
        :returns: The same result mapping, updated if log-file writing is blocked.
        """
        if not self.explicit_logging:
            return result
        from lib.python.core.cli import log_event

        try:
            log_event(
                self.args,
                json_string(result.get("kind", "skill_install")),
                "finished",
                json_string(result["status"]),
                elapsed=time.monotonic() - self.started,
                error_class=json_string(result["reason"])
                if "reason" in result
                else None,
            )
        except OSError:
            result.update(
                status="BLOCKED",
                reason="log_file_unwritable",
                safe_next_step=RECOVERY["log_file_unwritable"],
            )
        return result

    def _emit(self, result: Mapping[str, JSONValue]) -> None:
        """Emit the final result in the selected bounded format.

        :param result: Structured result returned by installer execution.
        """
        if self.args.yaml:
            assert self.yaml is not None
            print(self.yaml.safe_dump(result, sort_keys=True), end="")
        elif self.args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            print(f"{result['status']}: {result['destination']}")
            if result.get("reason"):
                print(result["reason"], file=sys.stderr)

    def _valid_timeout(self) -> bool:
        """Check the legacy seconds-only timeout syntax.

        :returns: True when ``--timeout`` is a positive integer followed by ``s``.
        """
        return bool(
            self.args.timeout and re.fullmatch(r"[1-9][0-9]*s", self.args.timeout)
        )

    def _timeout_seconds(self) -> int:
        """Parse the already validated seconds-only timeout.

        :returns: Timeout seconds from the command line.
        :raises ValueError: If the timeout text before its final suffix is not an integer.
        """  # noqa: DOC502 - Integer conversion rejects malformed timeout text.
        return int(self.args.timeout[:-1])

    def _deadline(self) -> float:
        """Convert the timeout argument into an absolute monotonic deadline.

        :returns: Absolute deadline for apply or recovery operations.
        :raises ValueError: If the timeout text before its final suffix is not an integer.
        """  # noqa: DOC502 - Timeout parsing propagates ValueError.
        return self.started + self._timeout_seconds()

    def _confirmation(self) -> Optional[str]:
        """Select the confirmation flag matching install or upgrade mode.

        :returns: Confirmation fingerprint from the command line, if any.
        """
        return (
            self.args.confirm_upgrade
            if self.args.upgrade
            else self.args.confirm_install
        )


def main() -> int:
    """Plan or apply a confirmed skill and agent-profile installation.

    :returns: Zero for a valid plan or complete install, otherwise two.
    :raises SystemExit: If argument parsing reports invalid input.
    :raises OSError: If initialization cannot resolve the destination on this filesystem.
    """  # noqa: DOC502 - Argument parsing and adapter construction propagate failures.
    started = time.monotonic()
    return InstallerCLI.from_command_line(started).run()


if __name__ == "__main__":
    raise SystemExit(main())
