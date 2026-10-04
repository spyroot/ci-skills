#!/usr/bin/env python3
"""Check the project binding against its exact checked-out Standards revision."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import jsonschema
import yaml
from validation_cli import add_options, emit


def _read_yaml(path: Path) -> dict:
    """Read YAML; arg is a file, no environment input or side effect; raises on error."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid_yaml_mapping:{path.name}")
    return data


def _inside(root: Path, relative: str) -> Path:
    """Resolve a declared path; no output, environment input, or side effect."""
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"path_outside_authority:{relative}")
    return path


def _repository_identity(value: str) -> tuple[str, str]:
    """Normalize a Git remote URL; no environment input, output, or side effect."""
    if value.startswith("git@"):
        host, path = value.removeprefix("git@").split(":", 1)
    else:
        parsed = urlparse(value)
        host, path = parsed.hostname or "", parsed.path
    return host.lower(), path.strip("/").removesuffix(".git")


def check(root: Path, standards: Path) -> list[str]:
    """Validate binding and provider declarations against the pinned manifest.

    Args: project root and checked-out Standards root. Environment: none.
    Stdout/stderr: none. Exit classes: returns problems or raises bad input.
    Side effects: none. Idempotency: repeated reads agree. Cleanup: none.
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


def main() -> int:
    """Render fail-closed JSON; argv only, stdout JSON, stderr errors, no writes."""
    parser = argparse.ArgumentParser(description=__doc__)
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
