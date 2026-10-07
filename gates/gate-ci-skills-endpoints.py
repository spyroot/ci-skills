#!/usr/bin/env python3
"""Refuse target-file keys that are not an endpoint or its access.

The allowed keys are declared once in `ci-skills/lib/python/core/endpoints.py`
(`TARGET_CONTRACT`); the rules are in docs/phases/CI03-GATES.md, section
gate-ci-skills-endpoints. Exit codes: 0 PASS, 1 FAIL, 2 the gate could not run.
"""

from __future__ import annotations

import argparse
import json
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The skill's one library locator puts `core` on the path; the gate's own
# library is the repository's maintenance package next to it.
runpy.run_path(str(ROOT / "ci-skills" / "bin" / "_bootstrap.py"))
sys.path.insert(0, str(ROOT / "tools"))

try:
    from skillkit.endpoint_gate import EndpointGate, ExitCode, StagedIndex, WorkingTree
except ImportError as exc:
    print(f"gate-ci-skills-endpoints: cannot import the gate: {exc}", file=sys.stderr)
    # ExitCode.UNUSABLE; the module that defines it did not import.
    raise SystemExit(2) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gate-ci-skills-endpoints",
        description=__doc__.splitlines()[0],
        epilog=(
            "Examples: gates/gate-ci-skills-endpoints.py --base origin/main --json; "
            "from a pre-commit hook: gates/gate-ci-skills-endpoints.py --staged"
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root (default: this checkout)",
    )
    parser.add_argument(
        "--base",
        default="HEAD",
        help="revision whose template counts as existing (default: HEAD)",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="check the staged index instead of the working tree (for a pre-commit hook)",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", help="print the JSON record")
    modes.add_argument("--yaml", action="store_true", help="print the YAML record")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    source = StagedIndex(root) if args.staged else WorkingTree(root)
    try:
        result = EndpointGate(source, args.base).run()
    except (OSError, UnicodeDecodeError) as exc:
        print(
            f"gate-ci-skills-endpoints: cannot read a document: {exc}", file=sys.stderr
        )
        return ExitCode.UNUSABLE
    if args.json:
        print(json.dumps(result.record(), indent=2, sort_keys=True))
    elif args.yaml:
        import yaml

        print(yaml.safe_dump(result.record(), sort_keys=True), end="")
    else:
        print(result.summary())
    return result.exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"gate-ci-skills-endpoints: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(ExitCode.UNUSABLE) from exc
