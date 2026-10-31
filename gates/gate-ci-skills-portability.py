#!/usr/bin/env python3
"""Reject fixed live selections in executable smoke and test sources.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "ci-skills")]

try:
    from skillkit.portability_gate import PortabilityGate
except ImportError as exc:
    print("gate-ci-skills-portability: cannot import the gate", file=sys.stderr)
    raise SystemExit(2) from exc


def main() -> int:
    """Read the existing NUL path inventory and render the shared gate result.

    :returns: Zero for success, one for findings, or two for unusable inputs.
    :raises SystemExit: Argument parsing rejects inputs or displays help.
    """
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog=(
            "Examples: inspect the selected snapshot: "
            "gate-ci-skills-portability.py --root SNAPSHOT --paths-file SELECTED --json. "
            "Output modes: human text by default, --json or --yaml."
        ),
    )
    parser.add_argument(
        "--root", type=Path, required=True, help="selected blessing snapshot root"
    )
    parser.add_argument(
        "--paths-file",
        type=Path,
        required=True,
        help="existing NUL-separated relative path inventory",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--json", action="store_true", help="print the redacted JSON result"
    )
    modes.add_argument(
        "--yaml", action="store_true", help="print the redacted YAML result"
    )
    args = parser.parse_args()
    try:
        inventory = args.paths_file.read_bytes()
        if inventory and not inventory.endswith(b"\0"):
            raise ValueError("path_inventory_not_nul_terminated")
        paths = tuple(item.decode("utf-8") for item in inventory.split(b"\0") if item)
        result = PortabilityGate(args.root).run(paths)
    except (OSError, ValueError, UnicodeError):
        print(
            "gate-ci-skills-portability: selected source inventory is unreadable",
            file=sys.stderr,
        )
        return 2
    if args.json:
        print(json.dumps(result.record(), indent=2, sort_keys=True))
    elif args.yaml:
        import yaml

        print(yaml.safe_dump(result.record(), sort_keys=True), end="")
    else:
        print(result.summary())
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
