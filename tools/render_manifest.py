#!/usr/bin/env python3
"""Render the skill's `tools.json` from its single declaration.

`ci-skills/tools.json` is the machine-readable contract an
agent reads instead of five help texts, and it is generated from
`ci-skills/lib/python/core/catalog.py`. `tests/python/test_catalog.py` fails when the committed copy
drifts from that module, so this is the other half: the command that makes the
committed copy current again.

    tools/render_manifest.py            # write it
    tools/render_manifest.py --check    # exit 2 if it would change

Without this, regenerating the manifest means hand-reproducing the serialization
the test compares against, and the next person edits the generated file instead
of the declaration.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import runpy
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Optional, Union

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
SKILL_RELATIVE = Path("ci-skills")
LIBRARY_RELATIVE = SKILL_RELATIVE / "lib" / "python"


def load_catalog(root: Path) -> ModuleType:
    """Import the source catalog directly from a selected skill checkout.

    :param root: Repository root containing the ci-skills library.
    :returns: Imported catalog module exposing the manifest declaration.
    :raises FileNotFoundError: If the expected catalog source file is missing.
    """
    skill_root = root / SKILL_RELATIVE
    library = root / LIBRARY_RELATIVE
    if not (library / "core" / "catalog.py").is_file():
        raise FileNotFoundError(f"no catalog module under {library}")
    runpy.run_path(str(skill_root / "bin" / "_bootstrap.py"))
    # Import after the installed package bootstrap selects the skill root.
    from lib.python.core import catalog

    return catalog


def render(root: Path) -> str:
    """Serialize the catalog manifest with stable ordering and trailing newline.

    :param root: Repository root containing the catalog declaration.
    :returns: JSON manifest text exactly as written to tools.json.
    """
    manifest = load_catalog(root).manifest()
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Check or update the committed tools manifest from its catalog.

    :param argv: Optional command arguments; defaults to process arguments.
    :returns: Zero for current or rendered manifest, two for a stale check.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare only; exit 2 when the committed manifest is stale",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable result")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    path = root / SKILL_RELATIVE / "tools.json"
    rendered = render(root)
    current = path.read_text(encoding="utf-8") if path.is_file() else None

    if args.check:
        stale = current != rendered
        result: dict[str, Union[str, int]] = {
            "kind": "manifest_check",
            "manifest": str(path.relative_to(root)),
            "status": "STALE" if stale else "CURRENT",
        }
        print(json.dumps(result) if args.json else result["status"])
        return 2 if stale else 0

    if current != rendered:
        path.write_text(rendered, encoding="utf-8")
    result = {
        "kind": "manifest_render",
        "manifest": str(path.relative_to(root)),
        "status": "UNCHANGED" if current == rendered else "WRITTEN",
        "bytes": len(rendered.encode("utf-8")),
    }
    print(json.dumps(result) if args.json else f"{result['status']} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
