"""Identify the skill code that actually ran.

A commit SHA cannot be verified at the moment it is claimed. The old resolution
accepted `--revision`, `CI_COMMIT_SHA` or `GITHUB_SHA` after checking only that
the string was 40 hex characters, so a skill invoked inside another project's CI
labelled its receipt with THAT project's commit, and a stale installed copy
could be labelled with a newer SHA. Reading the source repository's `HEAD`
without checking that the skill subtree is clean has the mirror problem: new
code claiming an older commit.

So this module separates the claim from the evidence:

* `digest` is computed from the bytes that ran. It cannot be forged without
  changing those bytes, and an installed copy with no Git metadata still has
  one.
* `revision` is a label. It is `verified` only when it came from a clean Git
  subtree in the repository that actually contains this skill, or from the
  installer's record whose measured digest still matches the installed bytes.
  An operator's `--revision` is recorded as a claim, never as evidence.
* `consuming_project` records the CI commit of whatever repository invoked the
  skill, kept strictly apart from the skill's own revision.

Verification against the claim happens where the Git objects are -- see
`tools/check_live_acceptance.py`, which recomputes the digest from the claimed
revision's tree and compares.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping, MutableSequence, Sequence
from pathlib import Path
from typing import NotRequired, Optional, TypedDict

SHA_PATTERN = re.compile(r"\A[0-9a-fA-F]{40}\Z")
DIGEST_ALGORITHM = "sha256-tree-v1"
INSTALLATION_IDENTITY_NAME = "INSTALLATION.json"
INSTALLATION_IDENTITY_SCHEMA_VERSION = "1.0"
INSTALLATION_IDENTITY_KIND = "skill_install"
INSTALLATION_IDENTITY_STATUS = "PASS"

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

    :param revision: Skill revision evidence for the measured file tree.
    :param consuming_project: Separate invoking-project commit claim.

    .. attribute :: revision
        :type: Revision

        Skill revision evidence for the measured file tree.

    .. attribute :: consuming_project
        :type: ConsumerRevision

        Separate invoking-project commit claim.
    """

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


def tree_digest(root: Path) -> TreeDigest:
    """Hash installed skill paths, executable bits, and file contents.

    :param root: Skill directory whose current bytes are being identified.
    :returns: Digest algorithm, SHA-256 hex digest, and included file count.
    :raises ProvenanceError: The skill directory is missing or contains no
        includable files.
    :raises OSError: An included file cannot be read.
    """  # noqa: DOC503 - Reading included source files can propagate OSError.
    root = Path(root).resolve()
    if not root.is_dir():
        raise ProvenanceError(f"skill_root_missing:{root}")
    overall = hashlib.sha256()
    count = 0
    for path in _included(root):
        relative = path.relative_to(root).as_posix()
        executable = "1" if os.access(path, os.X_OK) else "0"
        content = hashlib.sha256(path.read_bytes()).hexdigest()
        overall.update(f"{relative}\0{executable}\0{content}\0".encode())
        count += 1
    if not count:
        raise ProvenanceError(f"skill_root_empty:{root}")
    return {
        "algorithm": DIGEST_ALGORITHM,
        "digest": overall.hexdigest(),
        "file_count": count,
    }


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
        record: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    revision = record.get("revision")
    if (
        record.get("schema_version") != INSTALLATION_IDENTITY_SCHEMA_VERSION
        or record.get("kind") != INSTALLATION_IDENTITY_KIND
        or record.get("status") != INSTALLATION_IDENTITY_STATUS
        or any(record.get(key) != digest[key] for key in digest)
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
    """Identify executed skill bytes and any defensible revision label.

    :param skill_root: Skill directory whose bytes are measured.
    :param claimed: Optional caller-supplied full commit SHA when no verified
        revision is available.
    :param environ: Optional invoking project environment for separate CI commit.
    :returns: Tree digest, revision provenance and verification status, and
        invoking project commit fields.
    :raises ProvenanceError: Skill bytes cannot be measured or the fallback claim
        is not a full SHA.
    :raises OSError: An included skill file cannot be read.
    """  # noqa: DOC503 - Reading included source files can propagate OSError.
    digest = tree_digest(skill_root)
    root = Path(skill_root).resolve()
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
        "revision": revision,
        "consuming_project": consuming_project(environ),
    }
