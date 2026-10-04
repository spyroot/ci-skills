#!/usr/bin/env python3
"""Check a private receipt from the actual host against this exact checkout."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / "k8s-admin-diagnostics"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from core.acceptance import validate_live_receipt


def checkout_matches(revision: str) -> bool:
    """Bind the private verifier to an unmodified exact source checkout."""
    head = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if head.returncode or head.stdout.strip().lower() != revision.lower():
        return False
    changes = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            str(SKILL_ROOT.relative_to(REPO_ROOT)),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    return changes.returncode == 0 and not changes.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify one sanitized actual-host GAL-19 receipt.",
        epilog="Example: python tools/verify_live_receipt.py --revision SHA --repository owner/repo",
    )
    parser.add_argument("--revision", required=True, help="exact skill checkout commit")
    parser.add_argument(
        "--repository", required=True, help="exact selected GitHub repository"
    )
    parser.add_argument(
        "--json", action="store_true", help="print a machine-readable result"
    )
    parser.add_argument(
        "--yaml", action="store_true", help="print a machine-readable result"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="show required receipt inputs"
    )
    args = parser.parse_args()
    if args.json and args.yaml:
        parser.error("--json and --yaml are mutually exclusive")
    if args.dry_run:
        result = {
            "status": "DRY_RUN",
            "required_environment": [
                "GAL19_LIVE_RECEIPT_JSON",
                "GAL19_EXPECTED_TARGET_JSON",
            ],
        }
    else:
        try:
            if not checkout_matches(args.revision):
                raise ValueError("verifier_checkout_mismatch")
            receipt = json.loads(os.environ["GAL19_LIVE_RECEIPT_JSON"])
            expected = json.loads(os.environ["GAL19_EXPECTED_TARGET_JSON"])
            failures = validate_live_receipt(
                receipt,
                expected,
                revision=args.revision,
                github_repository=args.repository,
                skill_root=SKILL_ROOT,
            )
        except (KeyError, ValueError, TypeError, OSError, subprocess.TimeoutExpired):
            failures = ["receipt_or_target_unavailable"]
        result = {"status": "BLOCKED" if failures else "PASS", "failures": failures}
    if args.yaml:
        import yaml

        print(yaml.safe_dump(result, sort_keys=True), end="")
    elif args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            result["status"]
            + (
                ": " + ", ".join(result.get("failures", []))
                if result.get("failures")
                else ""
            )
        )
    return 0 if result["status"] in ("PASS", "DRY_RUN") else 2


if __name__ == "__main__":
    raise SystemExit(main())
