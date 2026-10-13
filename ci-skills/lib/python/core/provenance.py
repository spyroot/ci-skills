"""Measure package integrity and identify the runtime covered by live evidence.

``digest`` identifies installed package files, including documentation but
excluding the installer record, symlinks, caches, and platform metadata. The
installer uses it to verify copied files and the recorded source revision.
``functional_digest`` identifies only ``bin/``, ``lib/``, and ``schemas/``.
Live receipts carrying skill identity and GitHub acceptance use the same
calculation to compare runtime code and schemas. Documentation, tests, and
bytecode caches do not contribute to it.

Both measurements include sorted relative paths, executable bits, and contents.
The source revision remains separate from the invoking project's CI revision.
An operator-supplied revision is a claim until the source checkout or installer
record verifies it.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from pathlib import Path
from typing import NotRequired, Optional, TypedDict

from lib.python.core.cli_api import response_decoder

SHA_PATTERN = re.compile(r"\A[0-9a-fA-F]{40}\Z")
DIGEST_ALGORITHM = "sha256-tree-v1"
INSTALLATION_IDENTITY_NAME = "INSTALLATION.json"
INSTALLATION_IDENTITY_SCHEMA_VERSION = "1.0"
INSTALLATION_IDENTITY_KIND = "skill_install"
INSTALLATION_IDENTITY_STATUS = "PASS"
FUNCTIONAL_ROOTS = ("bin", "lib", "schemas")
FUNCTIONAL_EXCLUDED_DIRECTORIES = ("docs", "references", "tests")
FUNCTIONAL_EXCLUDED_SUFFIXES = (".md",)

# Bytecode caches differ between a fresh install and a used one, so including
# them would make the digest unstable for identical source.
EXCLUDED_DIRECTORIES = ("__pycache__",)
EXCLUDED_SUFFIXES = (".pyc", ".pyo")
EXCLUDED_NAMES = (".DS_Store",)

CONSUMER_VARIABLES = ("CI_COMMIT_SHA", "GITHUB_SHA")


class ProvenanceError(ValueError):
    """The executed skill cannot be identified."""


class TreeDigest(TypedDict):
    """Identify measured skill bytes independently of a revision claim.

    :param algorithm: Digest procedure defined by this module.
    :param digest: Lowercase SHA-256 digest of the selected file tree.
    :param file_count: Number of regular files included in the measurement.

    .. attribute :: algorithm
        :type: str

        Digest procedure defined by this module.

    .. attribute :: digest
        :type: str

        Lowercase SHA-256 digest of the selected file tree.

    .. attribute :: file_count
        :type: int

        Number of regular files included in the measurement.
    """

    algorithm: str
    digest: str
    file_count: int


class Revision(TypedDict):
    """Record one skill revision and how it was verified.

    :param value: Commit SHA, or None when no revision is known.
    :param source: Evidence source, or None when no revision is known.
    :param verified: Whether measured source bytes prove this revision.
    :param detail: Optional reason a discovered revision remains unverified.

    .. attribute :: value
        :type: Optional[str]

        Commit SHA, or None when no revision is known.

    .. attribute :: source
        :type: Optional[str]

        Evidence source, or None when no revision is known.

    .. attribute :: verified
        :type: bool

        Whether measured source bytes prove this revision.

    .. attribute :: detail
        :type: NotRequired[str]

        Optional reason a discovered revision remains unverified.
    """

    value: Optional[str]
    source: Optional[str]
    verified: bool
    detail: NotRequired[str]


class ConsumerRevision(TypedDict):
    """Keep the invoking project's identity separate from the installed skill.

    :param commit: CI commit claim, or None outside a declared CI environment.
    :param source: CI variable reference, or None when no claim was supplied.

    .. attribute :: commit
        :type: Optional[str]

        CI commit claim, or None outside a declared CI environment.

    .. attribute :: source
        :type: Optional[str]

        CI variable reference, or None when no claim was supplied.
    """

    commit: Optional[str]
    source: Optional[str]


class SkillIdentity(TreeDigest):
    """Combine measured bytes with independent skill and consumer revisions.

    TreeDigest documents the inherited algorithm, digest, and file_count fields.

    :param functional_digest: Runtime code and schema checksum used by live acceptance.
    :param revision: Skill revision evidence for the measured file tree.
    :param consuming_project: Separate invoking-project commit claim.

    .. attribute :: functional_digest
        :type: str

        Runtime code and schema checksum used by live acceptance.

    .. attribute :: revision
        :type: Revision

        Skill revision evidence for the measured file tree.

    .. attribute :: consuming_project
        :type: ConsumerRevision

        Separate invoking-project commit claim.
    """

    functional_digest: str
    revision: Revision
    consuming_project: ConsumerRevision


def _included(root: Path) -> Sequence[Path]:
    """Select installed skill files that contribute to its source digest.

    :param root: Skill root to walk recursively.
    :returns: Sorted regular files excluding symlinks, installer identity,
        bytecode, cache directories, and platform metadata.
    """
    found: MutableSequence[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if relative == Path(INSTALLATION_IDENTITY_NAME):
            continue
        if any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES or path.name in EXCLUDED_NAMES:
            continue
        found.append(path)
    return sorted(found)


def _file_entries(root: Path, files: Sequence[Path]) -> Mapping[Path, bytes]:
    """Encode each selected file once using the existing tree hash format.

    :param root: Base directory for relative paths included in the hash.
    :param files: Sorted files selected by the package or runtime measurement.
    :returns: Encoded path, executable bit, and content digest for each file.
    :raises OSError: An included file cannot be read.
    """  # noqa: DOC502 - Reading included source files can propagate OSError.
    entries: MutableMapping[Path, bytes] = {}
    for path in files:
        relative = path.relative_to(root)
        executable = "1" if os.access(path, os.X_OK) else "0"
        content = hashlib.sha256(path.read_bytes()).hexdigest()
        entries[relative] = f"{relative.as_posix()}\0{executable}\0{content}\0".encode()
    return entries


def _digest_entries(entries: Mapping[Path, bytes]) -> TreeDigest:
    """Hash encoded file entries in their sorted relative-path order.

    :param entries: File entries produced by the shared encoder; may be empty.
    :returns: Digest algorithm, SHA-256 hex digest, and selected file count.
    """
    overall = hashlib.sha256()
    for relative in sorted(entries):
        overall.update(entries[relative])
    return {
        "algorithm": DIGEST_ALGORITHM,
        "digest": overall.hexdigest(),
        "file_count": len(entries),
    }


def _package_entries(root: Path) -> Mapping[Path, bytes]:
    """Encode package files while retaining the existing nonempty-package check.

    :param root: Existing resolved skill directory whose files are selected.
    :returns: Encoded file entries for the complete package selection.
    :raises ProvenanceError: The package contains no includable files.
    :raises OSError: An included file cannot be read.
    """  # noqa: DOC503 - Encoding package files can propagate OSError.
    entries = _file_entries(root, _included(root))
    if not entries:
        raise ProvenanceError(f"skill_root_empty:{root}")
    return entries


def _is_functional(relative: Path) -> bool:
    """Select runtime files from the three declared functional roots.

    :param relative: Included file path relative to the skill root.
    :returns: True for runtime files, excluding documentation and tests.
    """
    return (
        relative.parts[0] in FUNCTIONAL_ROOTS
        and not any(part in FUNCTIONAL_EXCLUDED_DIRECTORIES for part in relative.parts)
        and relative.suffix not in FUNCTIONAL_EXCLUDED_SUFFIXES
    )


def _identity_digests(root: Path) -> tuple[TreeDigest, str]:
    """Measure package and functional identity while reading each file once.

    :param root: Existing resolved skill directory whose identity is recorded.
    :returns: Whole-package measurement and functional checksum.
    :raises ProvenanceError: The package contains no includable files.
    :raises OSError: An included file cannot be read.
    """  # noqa: DOC502 - Encoding and digest helpers propagate failures.
    entries = _package_entries(root)
    functional = {
        relative: entry
        for relative, entry in entries.items()
        if _is_functional(relative)
    }
    return _digest_entries(entries), _digest_entries(functional)["digest"]


def _skill_root(root: Path) -> Path:
    """Resolve the existing directory whose package or runtime will be measured.

    :param root: Skill root supplied by an installed command or acceptance check.
    :returns: Resolved skill directory.
    :raises ProvenanceError: The skill directory is missing.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise ProvenanceError(f"skill_root_missing:{root}")
    return root


def tree_digest(root: Path) -> TreeDigest:
    """Hash included package files for installer integrity.

    :param root: Skill directory whose current bytes are being identified.
    :returns: Digest algorithm, SHA-256 hex digest, and included file count.
    :raises ProvenanceError: The skill directory is missing or contains no
        includable files.
    :raises OSError: An included file cannot be read.
    """  # noqa: DOC502 - Root validation and the shared encoder propagate failures.
    root = _skill_root(root)
    return _digest_entries(_package_entries(root))


def functional_digest(root: Path) -> str:
    """Hash runtime code and schemas using the package's existing tree encoder.

    The file selection is rooted at ``bin/``, ``lib/``, and ``schemas/``.
    Documentation, tests, bytecode caches, and platform metadata are excluded.

    :param root: Installed or checked-out skill directory to measure.
    :returns: SHA-256 digest of the selected relative paths, modes, and contents;
        the standard empty-input digest when no runtime files are selected.
    :raises ProvenanceError: The skill root is missing.
    :raises OSError: An included file cannot be read.
    """  # noqa: DOC502 - Root validation and the shared encoder propagate failures.
    root = _skill_root(root)
    files = [path for path in _included(root) if _is_functional(path.relative_to(root))]
    return _digest_entries(_file_entries(root, files))["digest"]


def _git(root: Path, *args: str) -> Optional[str]:
    """Read one Git result for optional source revision verification.

    :param root: Working directory passed to Git.
    :param *args: Git subcommand and arguments.
    :returns: Trimmed stdout on success, or ``None`` for process, timeout, or
        Git command failure.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        # Installed commands must still identify their own bytes when Git is
        # unavailable; the optional repository revision remains unverified.
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _git_revision(skill_root: Path) -> Optional[Revision]:
    """Identify the tracked skill's own Git revision and cleanliness.

    :param skill_root: Skill directory inside a possible source checkout.
    :returns: Revision record with verification status, or ``None`` when no
        suitable tracked repository or SHA exists.
    """
    toplevel = _git(skill_root, "rev-parse", "--show-toplevel")
    if not toplevel:
        return None
    repo = Path(toplevel).resolve()
    # The repository must actually track this skill; an installed copy sitting
    # inside an unrelated checkout must not borrow that repository's commit.
    if _git(skill_root, "ls-files", "--error-unmatch", "SKILL.md") is None:
        return None
    head = _git(repo, "rev-parse", "HEAD")
    if not head or not SHA_PATTERN.match(head):
        return None
    status = _git(repo, "status", "--porcelain", "--", str(skill_root))
    if status is None:
        return None
    if status:
        # New code must never claim the commit that does not contain it.
        return {
            "value": head.lower(),
            "source": "git_head",
            "verified": False,
            "detail": "skill_subtree_dirty",
        }
    return {"value": head.lower(), "source": "git_head", "verified": True}


def _installed_revision(skill_root: Path, digest: TreeDigest) -> Optional[Revision]:
    """Verify installer-recorded revision against current installed bytes.

    :param skill_root: Installed skill directory containing optional identity file.
    :param digest: Current algorithm, digest, and file count from ``tree_digest``.
    :returns: Verified installer revision, or ``None`` for missing, malformed,
        stale, or unverified installer identity.
    """
    path = skill_root / INSTALLATION_IDENTITY_NAME
    try:
        if not path.is_file() or path.is_symlink():
            return None
        record = response_decoder().decode(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    revision = record.get("revision")
    if (
        record.get("schema_version") != INSTALLATION_IDENTITY_SCHEMA_VERSION
        or record.get("kind") != INSTALLATION_IDENTITY_KIND
        or record.get("status") != INSTALLATION_IDENTITY_STATUS
        or any(record.get(key) != value for key, value in digest.items())
        or not isinstance(revision, dict)
        or revision.get("verified") is not True
        or revision.get("source") != "git_head"
        or not isinstance(revision.get("value"), str)
        or SHA_PATTERN.fullmatch(revision["value"]) is None
    ):
        return None
    return {
        "value": revision["value"].lower(),
        "source": "installer_record",
        "verified": True,
    }


def consuming_project(environ: Optional[Mapping[str, str]] = None) -> ConsumerRevision:
    """Record the invoking CI project's commit separately from skill identity.

    :param environ: Environment mapping, or ``None`` to use process variables.
    :returns: Lowercased first nonempty ``CI_COMMIT_SHA`` or ``GITHUB_SHA`` and
        its variable source; both fields are ``None`` when absent.
    """
    environ = os.environ if environ is None else environ
    for name in CONSUMER_VARIABLES:
        value = (environ.get(name) or "").strip()
        if value:
            return {"commit": value.lower(), "source": f"env:{name}"}
    return {"commit": None, "source": None}


def skill_identity(
    skill_root: Path,
    claimed: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> SkillIdentity:
    """Identify package bytes, functional bytes, and verified source provenance.

    :param skill_root: Skill directory whose bytes are measured.
    :param claimed: Optional caller-supplied full commit SHA when no verified
        revision is available.
    :param environ: Optional invoking project environment for separate CI commit.
    :returns: Package and functional digests, revision provenance and verification
        status, and invoking project commit fields.
    :raises ProvenanceError: Skill bytes cannot be measured or the fallback claim
        is not a full SHA.
    :raises OSError: An included skill file cannot be read.
    """  # noqa: DOC503 - Reading included source files can propagate OSError.
    root = _skill_root(skill_root)
    digest, runtime_digest = _identity_digests(root)
    revision = _git_revision(root) or _installed_revision(root, digest)
    if revision is None:
        if claimed:
            if not SHA_PATTERN.match(claimed):
                raise ProvenanceError("revision must be a full commit SHA")
            revision = {
                "value": claimed.lower(),
                "source": "argv:--revision",
                "verified": False,
            }
        else:
            revision = {"value": None, "source": None, "verified": False}
    return {
        **digest,
        "functional_digest": runtime_digest,
        "revision": revision,
        "consuming_project": consuming_project(environ),
    }
