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

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Optional

from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.runtime import redact_tree

DIGEST_LENGTH = 12
# Fields that carry an absolute path on the execution host. Each is rewritten
# wherever it appears, at any depth.
PATH_VALUE_FIELDS = (
    "reference",
    "source",
    "token_file",
    "kubeconfig",
    "credential_source",
    "target_selection",
    "target_file",
)
PATH_LIST_FIELDS = ("kubeconfig_files",)
PATH_MAP_FIELDS = ("credential_sources",)
COMMAND_MAP_FIELDS = ("queries",)


def path_token(path: str) -> str:
    """Make a stable short SHA-256 token for a filesystem path string.

    :param path: Path text to identify without copying it into a receipt.
    :returns: First twelve hexadecimal characters of its UTF-8 SHA-256 digest.
    """
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def portable_reference(value: str) -> str:
    """Replace path portions of a source reference with path tokens.

    :param value: Credential or target source reference to transform.
    :returns: Reference with absolute file, binding, or command paths tokenized;
        references without path portions remain unchanged.
    """
    if value.startswith("binding:"):
        binding, mark, selected = value.removeprefix("binding:").partition("#")
        if mark:
            index, separator, source = selected.partition(":")
            if separator:
                return (
                    f"binding:path:{path_token(binding)}#{index}:"
                    f"{portable_reference(source)}"
                )
        return f"binding:path:{path_token(binding)}"
    if " -> file:" in value:
        selector, _, path = value.partition(" -> file:")
        return f"{portable_reference(selector)} -> file:path:{path_token(path)}"
    scheme, separator, remainder = value.partition(":")
    if scheme == "command" and remainder.startswith("/"):
        path, marker, digest = remainder.partition("#argv-sha256:")
        suffix = f"#argv-sha256:{digest}" if marker else ""
        return f"command:path:{path_token(path)}{suffix}"
    if separator and remainder.startswith("/"):
        return f"{scheme}:path:{path_token(remainder)}"
    if value.startswith("/"):
        return f"path:{path_token(value)}"
    return value


def portable_command(argv: Sequence[JSONValue]) -> Sequence[JSONValue]:
    """Tokenize kubeconfig paths in a recorded native argument vector.

    :param argv: Command arguments that may contain ``--kubeconfig PATH``.
    :returns: New argument list with each matching path replaced by a token.
    """
    result = list(argv)
    for index, item in enumerate(result[:-1]):
        argument = result[index + 1]
        if item == "--kubeconfig" and isinstance(argument, str):
            result[index + 1] = f"path:{path_token(argument)}"
    return result


def portable(value: JSONValue, key: Optional[str] = None) -> JSONValue:
    """Transform path-bearing receipt fields into committable values.

    :param value: Receipt value or nested part to transform.
    :param key: Parent field name used to identify path-bearing string values;
        ``None`` identifies the root document, which has no enclosing field.
    :returns: Transformed mapping or list, tokenized reference, or unchanged
        scalar; recognized command vectors tokenize kubeconfig paths.
    """
    if isinstance(value, str):
        return portable_reference(value) if key in PATH_VALUE_FIELDS else value
    if isinstance(value, Mapping):
        return {
            name: (
                {inner: portable_reference(item) for inner, item in child.items()}
                if name in PATH_MAP_FIELDS and isinstance(child, dict)
                else {inner: portable_command(item) for inner, item in child.items()}
                if name in COMMAND_MAP_FIELDS and isinstance(child, dict)
                else [portable_reference(item) for item in child]
                if name in PATH_LIST_FIELDS and isinstance(child, list)
                else portable(child, key=name)
            )
            for name, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [portable(item, key=key) for item in value]
    return value


def write_portable_receipt(data: JSONObject, selected: str) -> None:
    """Write a redacted, path-tokenized receipt through an atomic replacement.

    :param data: Receipt mapping to redact and transform before serialization.
    :param selected: Destination path; parent directories are created if needed.
    :raises OSError: Creating, writing, syncing, or replacing the destination
        fails.
    :raises TypeError: The transformed receipt cannot be serialized as JSON.
    """  # noqa: DOC502 - Serialization and atomic filesystem writes can fail.
    destination = Path(selected).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(portable(redact_tree(data)), indent=2, sort_keys=True) + "\n"
    temporary: Optional[Path] = None
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
