#!/usr/bin/env python3
"""Fail when any versioned path or byte contains the prohibited project marker."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


# Summary: recognize published skill documentation; Arguments: relative path
# Environment inputs: none; Stdout: none; Stderr: none; Exit classes: boolean
# Side effects: none; Idempotency: stable path; Cleanup: none
def _published_skill_doc(relative: str) -> bool:
    """Select public entrypoint and reference Markdown for naming checks.

    :param relative: Repository-relative file path.
    :returns: Whether this path is published skill documentation.
    """
    return relative in {"README.md", "skills/ci-skills/SKILL.md"} or (
        relative.startswith("skills/ci-skills/references/")
        and relative.endswith(".md")
    )


# Summary: find prohibited marker in repository paths and bytes; Arguments: root
# Environment inputs: Git index and worktree; Stdout: none; Stderr: none
# Exit classes: result map or scan error; Side effects: read-only
# Idempotency: stable checkout; Cleanup: Git child exits
def scan(root: Path) -> dict[str, object]:
    """Inspect tracked and pending bytes for prohibited public identifiers.

    :param root: Repository checkout whose public files are inspected.
    :returns: Scan status, file count, and exact violation locations.
    :raises RuntimeError: Git inventory or a file read fails.
    """
    marker = (b"GALI" + b"LEO").lower()
    legacy_names = (
        (b"k8s-admin-" + b"diagnostics").lower(),
        (b"K8S_ADMIN_" + b"DIAGNOSTICS_BINDING").lower(),
    )
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("cannot inventory repository paths")
    violations: list[str] = []
    count = 0
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        count += 1
        relative = os.fsdecode(raw)
        path = root / relative
        if marker in raw.lower():
            violations.append(relative + ":path")
        try:
            content = (
                os.fsencode(os.readlink(path))
                if path.is_symlink()
                else path.read_bytes()
            )
        except OSError as exc:
            raise RuntimeError(f"cannot scan {relative}") from exc
        if marker in content.lower():
            violations.append(relative + ":content")
        if _published_skill_doc(relative) and any(
            legacy in raw.lower() or legacy in content.lower()
            for legacy in legacy_names
        ):
            violations.append(relative + ":legacy-name")
    return {
        "schema_version": "1.0",
        "kind": "project_neutrality",
        "status": "FAIL" if violations else "PASS",
        "files_scanned": count,
        "violations": violations,
    }


# Summary: report neutrality scan; Arguments: CLI argv
# Environment inputs: repository tree; Stdout: selected format; Stderr: errors
# Exit classes: 0 pass, 1 finding, 2 blocked; Side effects: read-only
# Idempotency: stable checkout; Cleanup: file and process handles close
def main() -> int:
    cli = argparse.ArgumentParser(
        description="Scan every tracked and pending repository file, including dotfiles.",
        epilog="Example: check_project_neutrality.py --root . --json",
    )
    cli.add_argument("--root", required=True, metavar="PATH", help="repository root")
    modes = cli.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", help="print JSON")
    modes.add_argument("--yaml", action="store_true", help="print YAML")
    args = cli.parse_args()
    try:
        data = scan(Path(args.root).resolve())
        if args.json:
            print(json.dumps(data, indent=2, sort_keys=True))
        elif args.yaml:
            import yaml

            print(yaml.safe_dump(data, sort_keys=True), end="")
        else:
            print(
                f"Project neutrality: {data['status']} ({data['files_scanned']} files)"
            )
            for finding in data["violations"]:
                print(f"  {finding}")
        return 0 if data["status"] == "PASS" else 1
    except (RuntimeError, OSError, ImportError) as exc:
        cli.exit(2, f"BLOCKED: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
