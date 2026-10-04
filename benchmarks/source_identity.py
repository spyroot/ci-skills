"""Prove the exact clean repository subtree used by a benchmark process."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any

SHA_PATTERN = re.compile(r"\A[0-9a-fA-F]{40}\Z")
DIGEST_ALGORITHM = "sha256-tree-v1"
EXCLUDED_DIRECTORIES = ("__pycache__",)
EXCLUDED_SUFFIXES = (".pyc", ".pyo")
EXCLUDED_NAMES = (".DS_Store",)


class SourceIdentityError(ValueError):
    """A benchmark source subtree cannot be tied to one clean revision."""


def _git(root: Path, *arguments: str) -> str:
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


def _included(root: Path) -> list[Path]:
    found: list[Path] = []
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


def tree_digest(root: Path) -> dict[str, Any]:
    """Digest a tree over relative path, executable bit, and content bytes."""
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


def source_identity(root: Path, revision: str, subtree: Path) -> dict[str, Any]:
    """Return exact revision and content identity for one clean tracked subtree."""
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
