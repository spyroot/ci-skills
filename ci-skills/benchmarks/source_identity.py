"""Prove the exact clean repository subtree used by a benchmark process.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import MutableSequence, Sequence
from pathlib import Path
from typing import TypedDict

SHA_PATTERN = re.compile(r"\A[0-9a-fA-F]{40}\Z")
DIGEST_ALGORITHM = "sha256-tree-v1"
EXCLUDED_DIRECTORIES = ("__pycache__",)
EXCLUDED_SUFFIXES = (".pyc", ".pyo")
EXCLUDED_NAMES = (".DS_Store",)


class TreeIdentity(TypedDict):
    """Describe measured source bytes and executable bits.

    :param algorithm: Canonical tree-hash algorithm identifier.
    :param digest: Hexadecimal SHA-256 of the selected file records.
    :param file_count: Number of files included in the measurement.

    .. attribute :: algorithm
        :type: str

        Canonical tree-hash algorithm identifier.

    .. attribute :: digest
        :type: str

        Hexadecimal SHA-256 of the selected file records.

    .. attribute :: file_count
        :type: int

        Number of files included in the measurement.
    """

    algorithm: str
    digest: str
    file_count: int


class SourceIdentity(TreeIdentity):
    """Bind measured tree bytes to their verified repository revision.

    TreeIdentity documents the inherited digest fields.

    :param revision: Full lowercase commit SHA verified against checkout HEAD.
    :param subtree: Repository-relative directory whose bytes were measured.

    .. attribute :: revision
        :type: str

        Full lowercase commit SHA verified against checkout HEAD.

    .. attribute :: subtree
        :type: str

        Repository-relative directory whose bytes were measured.
    """

    revision: str
    subtree: str


class SourceIdentityError(ValueError):
    """A benchmark source subtree cannot be tied to one clean revision."""


def _git(root: Path, *arguments: str) -> str:
    """Read one Git value from the selected benchmark source checkout.

    :param root: Checkout used for the Git query.
    :param *arguments: Git subcommand and its arguments.
    :returns: Trimmed command stdout.
    :raises SourceIdentityError: Git exits nonzero.
    :raises OSError: Git cannot start.
    :raises subprocess.TimeoutExpired: Git exceeds its 15-second bound.
    """  # noqa: DOC503 - Bounded Git execution may fail or time out.
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode:
        raise SourceIdentityError("source_git_read_failed")
    return result.stdout.strip()


def _included(root: Path) -> Sequence[Path]:
    """Select regular source files for an executable-tree digest.

    :param root: Source subtree to walk recursively.
    :returns: Sorted files excluding symlinks, bytecode, cache directories,
        and platform metadata.
    """
    found: MutableSequence[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES or path.name in EXCLUDED_NAMES:
            continue
        found.append(path)
    return sorted(found)


def tree_digest(root: Path) -> TreeIdentity:
    """Fingerprint the actual bytes and executable bits in one source subtree.

    :param root: Directory whose current contents are measured.
    :returns: Digest algorithm, SHA-256 hex value, and included file count.
    :raises SourceIdentityError: Subtree is missing or contains no included files.
    :raises OSError: An included file cannot be read.
    """  # noqa: DOC503 - Path.read_bytes can fail for selected source files.
    root = root.resolve()
    if not root.is_dir():
        raise SourceIdentityError("source_subtree_missing")
    digest = hashlib.sha256()
    count = 0
    for path in _included(root):
        relative = path.relative_to(root).as_posix()
        executable = "1" if os.access(path, os.X_OK) else "0"
        content = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(f"{relative}\0{executable}\0{content}\0".encode())
        count += 1
    if not count:
        raise SourceIdentityError("source_subtree_empty")
    return {
        "algorithm": DIGEST_ALGORITHM,
        "digest": digest.hexdigest(),
        "file_count": count,
    }


def source_identity(root: Path, revision: str, subtree: Path) -> SourceIdentity:
    """Verify one clean tracked source subtree against an exact commit.

    :param root: Repository checkout containing the source subtree.
    :param revision: Required full 40-character commit SHA.
    :param subtree: Tracked subtree whose current bytes will be measured.
    :returns: Source-tree digest, lowercase revision, and relative subtree path.
    :raises SourceIdentityError: SHA, checkout HEAD, tracking state, cleanliness,
        or subtree containment does not match the requested source.
    :raises OSError: Git cannot start or an included source file cannot be read.
    :raises subprocess.TimeoutExpired: A Git query exceeds its time bound.
    """  # noqa: DOC503 - Bounded Git execution may fail or time out.
    root = root.resolve()
    subtree = subtree.resolve()
    if not SHA_PATTERN.fullmatch(revision):
        raise SourceIdentityError("source_revision_not_full_sha")
    try:
        relative = subtree.relative_to(root)
    except ValueError as exc:
        raise SourceIdentityError("source_subtree_outside_repository") from exc
    head = _git(root, "rev-parse", "HEAD").lower()
    if head != revision.lower():
        raise SourceIdentityError("source_revision_mismatch")
    if not _git(root, "ls-files", "--", relative.as_posix()):
        raise SourceIdentityError("source_subtree_untracked")
    if _git(
        root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        relative.as_posix(),
    ):
        raise SourceIdentityError("source_subtree_dirty")
    return {
        **tree_digest(subtree),
        "revision": revision.lower(),
        "subtree": relative.as_posix(),
    }
