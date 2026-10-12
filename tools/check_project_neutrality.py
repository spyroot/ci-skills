#!/usr/bin/env python3
"""Fail when any versioned path or byte contains the prohibited project marker.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import MutableSequence, Sequence
from pathlib import Path
from typing import TypedDict


class NeutralityResult(TypedDict):
    """Report a repository marker scan without retaining file contents.

    :param schema_version: Existing project-neutrality evidence version.
    :param kind: Stable project-neutrality result identifier.
    :param status: FAIL when any prohibited marker was found, otherwise PASS.
    :param files_scanned: Count of inventoried nonempty Git paths.
    :param violations: Ordered path/content locations containing the marker.

    .. attribute :: schema_version
        :type: str

        Existing project-neutrality evidence version.

    .. attribute :: kind
        :type: str

        Stable project-neutrality result identifier.

    .. attribute :: status
        :type: str

        FAIL when any prohibited marker was found, otherwise PASS.

    .. attribute :: files_scanned
        :type: int

        Count of inventoried nonempty Git paths.

    .. attribute :: violations
        :type: Sequence[str]

        Ordered path/content locations containing the marker.
    """

    schema_version: str
    kind: str
    status: str
    files_scanned: int
    violations: Sequence[str]


def scan(root: Path) -> NeutralityResult:
    """Scan tracked and pending Git paths and bytes for the prohibited marker.

    :param root: Repository root whose non-ignored files are inventoried.
    :returns: Result mapping with pass/fail status, file count, and violations.
    :raises RuntimeError: If Git returns a failed inventory or a listed file cannot be read.
    :raises OSError: If the Git executable cannot start.
    """  # noqa: DOC503 - Starting the Git inventory process can raise OSError.
    marker = (b"GALI" + b"LEO").lower()
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
    violations: MutableSequence[str] = []
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
    return {
        "schema_version": "1.0",
        "kind": "project_neutrality",
        "status": "FAIL" if violations else "PASS",
        "files_scanned": count,
        "violations": violations,
    }


def main() -> int:
    """Print a repository neutrality result in human, JSON, or YAML form.

    :returns: Zero for a clean scan or one for violations.
    :raises SystemExit: If arguments are invalid, help is requested, or scanning/rendering fails.
    :raises UnicodeError: If selected output cannot be encoded by stdout.
    """  # noqa: DOC502 - Argument parsing, parser.exit, and output encoding can fail.
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
