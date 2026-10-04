#!/usr/bin/env python3
"""Render the skill's `tools.json` from its single declaration.

`skills/ci-skills/tools.json` is the machine-readable contract an
agent reads instead of five help texts, and it is generated from
`scripts/core/catalog.py`. `tests/test_catalog.py` fails when the committed copy
drifts from that module, so this is the other half: the command that makes the
committed copy current again.

    tools/render_manifest.py            # write it
    tools/render_manifest.py --check    # exit 2 if it would change

Without this, regenerating the manifest means hand-reproducing the serialization
the test compares against, and the next person edits the generated file instead
of the declaration.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
SKILL_RELATIVE = Path("skills") / "ci-skills"


# Summary: import this checkout's catalog; Arguments: repository root
# Environment inputs: source tree; Stdout: none; Stderr: none
# Exit classes: module or missing-file error; Side effects: updates sys.path
# Idempotency: stable source; Cleanup: none
def load_catalog(root: Path) -> Any:
    """Import the catalog module from the skill tree, uninstalled."""
    script_root = root / SKILL_RELATIVE / "scripts"
    if not (script_root / "core" / "catalog.py").is_file():
        raise FileNotFoundError(f"no catalog module under {script_root}")
    sys.path.insert(0, str(script_root))
    # Imported after sys.path is set up, which is the point of this function.
    from core import catalog

    return catalog


# Summary: serialize catalog manifest; Arguments: repository root
# Environment inputs: source catalog; Stdout: none; Stderr: none
# Exit classes: JSON text or import error; Side effects: imports catalog
# Idempotency: stable catalog; Cleanup: none
def render(root: Path) -> str:
    """Return the manifest exactly as it is written, newline included."""
    manifest = load_catalog(root).manifest()
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


# Summary: check or regenerate tools.json; Arguments: optional CLI argv
# Environment inputs: catalog and manifest; Stdout: status; Stderr: parser errors
# Exit classes: 0 current/written, 2 stale; Side effects: write unless --check
# Idempotency: repeated render converges; Cleanup: file handles close
def main(argv: list[str] | None = None) -> int:
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
        result = {
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
