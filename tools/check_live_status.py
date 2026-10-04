#!/usr/bin/env python3
"""Read the trusted verifier's exact-head GitHub commit status."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "skills" / "k8s-admin-diagnostics" / "scripts"))

from core.acceptance import validate_external_status


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a trusted live-receipt status for one commit.",
        epilog="Example: check_live_status.py --status-file status.json --revision SHA --repository owner/repo --actor trusted-bot --json",
    )
    parser.add_argument(
        "--status-file", required=True, help="combined GitHub status JSON"
    )
    parser.add_argument("--revision", required=True, help="exact pull request head SHA")
    parser.add_argument("--repository", required=True, help="owner/repository")
    parser.add_argument("--actor", required=True, help="trusted verifier account")
    parser.add_argument("--json", action="store_true", help="machine-readable JSON")
    parser.add_argument("--yaml", action="store_true", help="machine-readable YAML")
    parser.add_argument(
        "--dry-run", action="store_true", help="show planned validation"
    )
    args = parser.parse_args()
    if args.json and args.yaml:
        parser.error("--json and --yaml are mutually exclusive")
    if args.dry_run:
        result = {"status": "DRY_RUN", "context": "gal19/live-receipt"}
    else:
        try:
            payload = json.loads(Path(args.status_file).read_text(encoding="utf-8"))
            failures = validate_external_status(
                payload,
                revision=args.revision,
                repository=args.repository,
                trusted_actor=args.actor,
            )
        except (OSError, ValueError, TypeError):
            failures = ["status_response_unavailable"]
        result = {"status": "BLOCKED" if failures else "PASS", "failures": failures}
    if args.yaml:
        import yaml

        print(yaml.safe_dump(result, sort_keys=True), end="")
    elif args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(result["status"] + ": " + ", ".join(result.get("failures", [])))
    return 0 if result["status"] in ("PASS", "DRY_RUN") else 2


if __name__ == "__main__":
    raise SystemExit(main())
