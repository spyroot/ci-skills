#!/usr/bin/env python3
"""Fail when any versioned path or byte contains the prohibited project marker."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


def scan(root: Path) -> dict[str, object]:
    marker = (b"GALI" + b"LEO").lower()
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True, check=False,
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
            content = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"cannot scan {relative}") from exc
        if marker in content.lower():
            violations.append(relative + ":content")
    return {"schema_version": "1.0", "kind": "project_neutrality", "status": "FAIL" if violations else "PASS",
            "files_scanned": count, "violations": violations}


def main() -> int:
    cli = argparse.ArgumentParser(description="Scan every tracked and pending repository file, including dotfiles.",
                                  epilog="Example: check_project_neutrality.py --root . --json")
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
            print(f"Project neutrality: {data['status']} ({data['files_scanned']} files)")
            for finding in data["violations"]:
                print(f"  {finding}")
        return 0 if data["status"] == "PASS" else 1
    except (RuntimeError, OSError, ImportError) as exc:
        cli.exit(2, f"BLOCKED: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
