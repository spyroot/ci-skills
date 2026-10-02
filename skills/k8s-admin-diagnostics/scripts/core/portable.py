"""Render a receipt in the form that can be committed.

A receipt captured on a real host carries absolute filesystem paths: the
credential sources name where each credential came from, which is exactly the
evidence the gate is required to report. Those same paths carry the operator's
home directory, so the captured form cannot be committed to a public,
deliberately project-neutral repository.

The portable form keeps what acceptance compares -- the SCHEME of each
credential source, so `file:` stays distinguishable from `env:GITLAB_TOKEN` --
and replaces the path with a digest. The digest is stable for one path on one
host, so two receipts can still be seen to have used the same kubeconfig, while
the path never leaves the host.

The transform names the fields it rewrites rather than pattern-matching every
string. A path-shaped regex over arbitrary values also rewrites a URL authority
and a `owner/repository` pair, and acceptance compares those by exact equality.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .runtime import redact_tree

DIGEST_LENGTH = 12
# Fields that carry an absolute path on the execution host. Each is rewritten
# wherever it appears, at any depth.
PATH_VALUE_FIELDS = (
    "reference",
    "source",
    "token_file",
    "kubeconfig",
    "credential_source",
    "target_file",
)
PATH_LIST_FIELDS = ("kubeconfig_files",)
PATH_MAP_FIELDS = ("credential_sources",)


def path_token(path: str) -> str:
    """Return a stable, non-reversible token for one absolute path."""
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def portable_reference(value: str) -> str:
    """Rewrite one credential reference, keeping its scheme.

    `file:/home/op/gitlab.token` becomes `file:path:<digest>`; a reference that
    carries no path, such as `env:GITLAB_TOKEN` or
    `gh-credential-store:github.com`, is returned unchanged.
    """
    scheme, separator, remainder = value.partition(":")
    if separator and remainder.startswith("/"):
        return f"{scheme}:path:{path_token(remainder)}"
    if value.startswith("/"):
        return f"path:{path_token(value)}"
    return value


def portable(value: Any, *, key: str | None = None) -> Any:
    """Render a receipt, or any part of one, in committable form."""
    if isinstance(value, str):
        return portable_reference(value) if key in PATH_VALUE_FIELDS else value
    if isinstance(value, dict):
        return {
            name: (
                {inner: portable_reference(item) for inner, item in child.items()}
                if name in PATH_MAP_FIELDS and isinstance(child, dict)
                else [portable_reference(item) for item in child]
                if name in PATH_LIST_FIELDS and isinstance(child, list)
                else portable(child, key=name)
            )
            for name, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [portable(item, key=key) for item in value]
    return value


def write_portable_receipt(data: dict[str, Any], selected: str) -> None:
    """Atomically write redacted evidence to a caller-selected 0600 file."""
    destination = Path(selected).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(portable(redact_tree(data)), indent=2, sort_keys=True) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{destination.name}.",
            suffix=".partial",
            dir=destination.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
