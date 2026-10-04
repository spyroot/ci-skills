#!/usr/bin/env python3
"""Check the project binding against its exact checked-out Standards revision."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import jsonschema
import yaml
from validation_cli import ValidationArgumentParser, add_options, emit


# Summary: load a YAML mapping; Arguments: file path
# Environment inputs: file bytes; Stdout: none; Stderr: none
# Exit classes: mapping or read/shape error; Side effects: read-only
# Idempotency: same bytes give same mapping; Cleanup: file closes
def _read_yaml(path: Path) -> dict:
    """Read an authority or binding document as a YAML mapping.

    :param path: File containing the YAML document.
    :returns: Parsed mapping.
    :raises ValueError: The document is not a mapping.
    :raises OSError: The file cannot be read.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid_yaml_mapping:{path.name}")
    return data


# Summary: keep a declared path inside authority; Arguments: root and relative path
# Environment inputs: filesystem links; Stdout: none; Stderr: none
# Exit classes: resolved path or ValueError; Side effects: read-only
# Idempotency: same tree gives same path; Cleanup: none
def _inside(root: Path, relative: str) -> Path:
    """Resolve a declared path without escaping its authority root.

    :param root: Root directory owning the declaration.
    :param relative: Declared path under that root.
    :returns: Resolved path within the root.
    :raises ValueError: The path resolves outside the root.
    """
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"path_outside_authority:{relative}")
    return path


# Summary: normalize a Git remote identity; Arguments: URL or scp-style remote
# Environment inputs: none; Stdout: none; Stderr: none
# Exit classes: host and repository pair or parse error; Side effects: none
# Idempotency: same remote gives same pair; Cleanup: none
def _repository_identity(value: str) -> tuple[str, str]:
    """Normalize HTTPS or scp-style Git remotes for identity comparison.

    :param value: Git remote URL or scp-style address.
    :returns: Lowercase hostname and repository path without a .git suffix.
    :raises ValueError: A malformed scp-style address has no repository path.
    """
    if value.startswith("git@"):
        host, path = value.removeprefix("git@").split(":", 1)
    else:
        parsed = urlparse(value)
        host, path = parsed.hostname or "", parsed.path
    return host.lower(), path.strip("/").removesuffix(".git")


# Summary: compare binding with pinned Standards; Arguments: project and authority roots
# Environment inputs: Git objects, manifest, schemas; Stdout: none; Stderr: none
# Exit classes: problem list or unreadable input; Side effects: read-only
# Idempotency: same commits give same result; Cleanup: Git child processes exit
def check(root: Path, standards: Path) -> list[str]:
    """Validate binding and provider declarations against the pinned manifest.

    :param root: Project checkout containing standards-binding.yaml.
    :param standards: Checkout of the pinned Standards commit.
    :returns: Exact binding, schema, and provider mismatch reasons.
    :raises OSError: A required file cannot be read.
    :raises ValueError: A required YAML or JSON document is invalid.
    :raises subprocess.CalledProcessError: Git cannot read required identity.
    """
    problems: list[str] = []
    binding = _read_yaml(root / "standards-binding.yaml")
    source = binding.get("spec", {}).get("source", {})
    revision = source.get("revision")
    actual = subprocess.run(
        ["git", "-C", str(standards), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if actual != revision:
        problems.append("standards_revision_mismatch")
    manifest = _read_yaml(standards / "manifest.yaml")
    authority = manifest.get("spec", {}).get("authority", {})
    if source.get("localPath") != authority.get("localPath"):
        problems.append("standards_local_path_mismatch")
    if authority.get("revisionPolicy") != "exact-commit":
        problems.append("standards_revision_policy_invalid")
    consumer = manifest.get("spec", {}).get("consumer", {})
    if consumer.get("bindingFile") != "standards-binding.yaml":
        problems.append("manifest_binding_path_mismatch")
    schema_entries = {
        item["id"]: item for item in manifest.get("spec", {}).get("schemas", [])
    }
    binding_schema_id = consumer.get("bindingSchema")
    schema_entry = schema_entries.get(binding_schema_id)
    if not schema_entry:
        problems.append("binding_schema_missing")
        return problems
    schema_path = _inside(standards, schema_entry["path"])
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    for error in jsonschema.Draft202012Validator(schema).iter_errors(binding):
        location = "/".join(str(piece) for piece in error.absolute_path)
        problems.append(f"binding_schema_invalid:{location or 'root'}:{error.message}")
    required = [
        item["id"]
        for item in manifest.get("spec", {}).get("readOrder", [])
        if item.get("required") is True
    ]
    if binding.get("spec", {}).get("requiredContracts") != required:
        problems.append("required_contracts_mismatch")
    for item in manifest.get("spec", {}).get("readOrder", []):
        if item.get("required") and not _inside(standards, item["path"]).is_file():
            problems.append(f"required_contract_missing:{item['id']}")
    remote = subprocess.run(
        ["git", "-C", str(standards), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if _repository_identity(source.get("repository", "")) != _repository_identity(
        remote
    ):
        problems.append("standards_repository_mismatch")
    for relative in consumer.get("forbiddenTrackedPointers", []):
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", relative],
            capture_output=True,
            text=True,
        )
        if tracked.returncode == 0:
            problems.append(f"forbidden_tracked_pointer:{relative}")
    provider_id = consumer.get("providerBinding", {}).get("schema")
    provider_entry = schema_entries.get(provider_id)
    providers = binding.get("spec", {}).get("providers", [])
    if providers and not provider_entry:
        problems.append("provider_schema_missing")
    elif providers:
        provider_schema = json.loads(
            _inside(standards, provider_entry["path"]).read_text(encoding="utf-8")
        )
        for provider in providers:
            path = _inside(root, provider["binding"])
            if not path.is_file():
                problems.append(f"provider_binding_missing:{provider['binding']}")
                continue
            payload = _read_yaml(path)
            if payload.get("metadata", {}).get("name") != provider["name"]:
                problems.append(f"provider_name_mismatch:{provider['name']}")
            for error in jsonschema.Draft202012Validator(provider_schema).iter_errors(
                payload
            ):
                problems.append(
                    f"provider_schema_invalid:{provider['name']}:{error.message}"
                )
    for exception in binding.get("spec", {}).get("exceptions", []):
        try:
            if date.fromisoformat(exception["expires"]) < date.today():
                problems.append(f"exception_expired:{exception['id']}")
        except (KeyError, TypeError, ValueError):
            problems.append("exception_expiry_invalid")
    return sorted(set(problems))


# Summary: report binding status; Arguments: CLI paths and log options
# Environment inputs: selected checkouts; Stdout: status JSON; Stderr: safe log
# Exit classes: 0 pass, 2 invalid binding; Side effects: optional log append
# Idempotency: repeated checks agree; Cleanup: log handle closes
def main() -> int:
    """Report binding status using the shared validation output contract.

    :returns: Zero on pass or two when a binding check cannot pass.
    """
    parser = ValidationArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--standards", type=Path, required=True)
    add_options(parser)
    args = parser.parse_args()
    try:
        problems = check(args.root.resolve(), args.standards.resolve())
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        subprocess.CalledProcessError,
    ) as exc:
        problems = [f"authority_unreadable:{type(exc).__name__}"]
    emit(args, {"status": "PASS" if not problems else "BLOCKED", "problems": problems})
    return 0 if not problems else 2


if __name__ == "__main__":
    sys.exit(main())
