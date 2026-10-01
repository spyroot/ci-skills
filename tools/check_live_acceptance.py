#!/usr/bin/env python3
"""Verify that committed live-access receipts actually accept this revision.

Mocked CI proves code behavior. It cannot prove access, because the fake `gh`,
`glab` and `kubectl` answer whatever the fixtures say. So acceptance consumes
the receipt a real host produced and refuses it when it is missing, stale, from
an undeclared executor, aimed at different targets, or produced by different
code.

The load-bearing comparison is the skill DIGEST, not a commit SHA. A receipt can
never carry the SHA of the commit that adds it, and a SHA is unverifiable where
it is claimed anyway. A digest recomputed from the checked-out skill tree is
verifiable here, and it fails exactly when the code that ran differs from the
code under review -- which is what "wrong revision" has to mean.

This is plain data: an operator-owned expectations file, receipts as files, and
one comparison function. No pairing protocol, no registry, no new service.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tomllib

SCHEMA_VERSION = "1.0"
RECEIPT_KIND = "access_check"
REQUIRED_EXPECTATIONS = (
    "targets",
    "required_live_checks",
    "required_checks",
    "max_receipt_age_days",
)


class AcceptanceError(RuntimeError):
    """Acceptance inputs could not be read."""


def _load_expected(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AcceptanceError(f"expectations_unreadable:{path}") from exc
    executors = data.get("executors") or []
    if not isinstance(executors, list) or not executors:
        raise AcceptanceError("no_executors_declared")
    # Every guarantee is mandatory. Left optional, the gate stays green while
    # dropping one key from this file silently stops it checking that any live
    # check ran -- the gate would exist and prove nothing.
    for key in REQUIRED_EXPECTATIONS:
        if not data.get(key):
            raise AcceptanceError(f"expectation_not_declared:{key}")
    return data


def _load_receipts(directory: Path) -> dict[str, Any]:
    if not directory.is_dir():
        raise AcceptanceError(f"receipt_directory_missing:{directory}")
    receipts: dict[str, Any] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            receipts[path.name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AcceptanceError(f"receipt_unreadable:{path.name}") from exc
    if not receipts:
        raise AcceptanceError("receipts_missing")
    return receipts


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _check_receipt(
    name: str,
    receipt: dict[str, Any],
    executor: dict[str, Any],
    expected: dict[str, Any],
    digest: str,
    now: datetime,
) -> list[str]:
    """Return every reason this receipt does not accept the current skill."""
    problems: list[str] = []

    if receipt.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"{name}:schema_version_unexpected")
    if receipt.get("kind") != RECEIPT_KIND:
        problems.append(f"{name}:kind_unexpected")
    if receipt.get("status") != "PASS":
        problems.append(f"{name}:status_not_pass")
    if receipt.get("publication") is not True:
        problems.append(f"{name}:not_a_publication_receipt")

    # Wrong executor: the host and every identity are declared, and compared by
    # exact string equality.
    if receipt.get("execution_host") != executor.get("host"):
        problems.append(f"{name}:execution_host_not_declared")
    surfaces = receipt.get("surfaces") or {}
    for surface, identity in (executor.get("identities") or {}).items():
        observed = (surfaces.get(surface) or {}).get("identity")
        if observed != identity:
            problems.append(f"{name}:identity_mismatch:{surface}")

    # Wrong target: deep equality, so an extra or missing authority is caught.
    if "targets" in expected and receipt.get("targets") != expected["targets"]:
        problems.append(f"{name}:targets_mismatch")

    # Wrong code: the digest of the tree under review must be the digest of the
    # tree that produced the receipt.
    reported = (receipt.get("skill") or {}).get("digest")
    if not reported:
        problems.append(f"{name}:skill_digest_absent")
    elif reported != digest:
        problems.append(f"{name}:skill_digest_mismatch")

    # Required results: every declared live check present, proven, none blocking.
    live = receipt.get("live_checks") or {}
    for required in expected.get("required_live_checks") or []:
        check = live.get(required)
        if check is None:
            problems.append(f"{name}:live_check_missing:{required}")
        elif check.get("access_proven") is not True:
            problems.append(f"{name}:live_check_unproven:{required}")
    if receipt.get("blocking_live_checks"):
        problems.append(f"{name}:live_checks_blocking")

    # The required GitHub checks the publication gate read back.
    declared = set(expected.get("required_checks") or [])
    observed_checks = set(
        ((surfaces.get("github") or {}).get("details") or {}).get("required_checks")
        or []
    )
    for missing in sorted(declared - observed_checks):
        problems.append(f"{name}:required_check_absent:{missing}")

    # Stale: measured against the receipt's own capture time.
    captured = _parse_time(receipt.get("captured_at"))
    window = expected.get("max_receipt_age_days")
    if captured is None:
        problems.append(f"{name}:captured_at_unreadable")
    elif captured > now:
        problems.append(f"{name}:captured_in_the_future")
    elif isinstance(window, int) and (now - captured).days > window:
        problems.append(f"{name}:receipt_stale")

    # A receipt that still carries a credential value must never be accepted.
    from_runtime = _redactor()
    body = json.dumps(receipt, sort_keys=True)
    if from_runtime is None:
        problems.append(f"{name}:sanitization_check_unavailable")
    elif from_runtime(body) != body:
        problems.append(f"{name}:receipt_not_sanitized")

    return problems


def _redactor():
    """Return the skill's own redactor, so one rule covers capture and review."""
    root = Path(__file__).resolve().parents[1]
    scripts = root / "skills" / "k8s-admin-diagnostics" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        from core.runtime import redact
    except ImportError:
        return None
    return redact


def evaluate(
    expected: dict[str, Any],
    receipts: dict[str, Any],
    skill_root: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compare committed receipts against the operator's expectations."""
    from core.provenance import tree_digest

    now = now or datetime.now(timezone.utc)
    digest = tree_digest(skill_root)["digest"]
    by_host = {
        (receipt.get("execution_host") or ""): (name, receipt)
        for name, receipt in receipts.items()
    }

    problems: list[str] = []
    accepted: list[str] = []
    for executor in expected["executors"]:
        host = executor.get("host")
        if host not in by_host:
            problems.append(f"{executor.get('label', host)}:receipt_missing")
            continue
        name, receipt = by_host[host]
        found = _check_receipt(name, receipt, executor, expected, digest, now)
        problems.extend(found)
        if not found:
            accepted.append(name)

    declared_hosts = {executor.get("host") for executor in expected["executors"]}
    for name, receipt in receipts.items():
        if (receipt.get("execution_host") or "") not in declared_hosts:
            problems.append(f"{name}:executor_not_declared")

    return {
        "schema_version": "1.0",
        "kind": "live_acceptance",
        "status": "PASS" if not problems else "BLOCKED",
        "skill_digest": digest,
        "accepted": sorted(accepted),
        "problems": sorted(problems),
    }


def main() -> int:
    cli = argparse.ArgumentParser(
        description="Verify committed live-access receipts against this revision.",
        epilog="Example: check_live_acceptance.py --root . --json",
    )
    cli.add_argument("--root", required=True, metavar="PATH", help="repository root")
    cli.add_argument(
        "--expected",
        metavar="PATH",
        help="expectations file (default: <root>/acceptance/expected.toml)",
    )
    cli.add_argument(
        "--receipts",
        metavar="PATH",
        help="receipt directory (default: <root>/acceptance/receipts)",
    )
    cli.add_argument(
        "--skill",
        metavar="PATH",
        help="skill root (default: <root>/skills/k8s-admin-diagnostics)",
    )
    modes = cli.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", help="print JSON")
    modes.add_argument("--yaml", action="store_true", help="print YAML")
    args = cli.parse_args()

    root = Path(args.root).resolve()
    expected_path = (
        Path(args.expected) if args.expected else root / "acceptance" / "expected.toml"
    )
    receipts_path = (
        Path(args.receipts) if args.receipts else root / "acceptance" / "receipts"
    )
    skill_path = (
        Path(args.skill) if args.skill else root / "skills" / "k8s-admin-diagnostics"
    )
    _redactor()
    try:
        data = evaluate(
            _load_expected(expected_path), _load_receipts(receipts_path), skill_path
        )
    except (AcceptanceError, OSError, ValueError) as exc:
        cli.exit(2, f"BLOCKED: {exc}\n")

    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif args.yaml:
        import yaml

        print(yaml.safe_dump(data, sort_keys=True), end="")
    else:
        print(f"Live acceptance: {data['status']}")
        for name in data["accepted"]:
            print(f"  accepted {name}")
        for problem in data["problems"]:
            print(f"  problem  {problem}")
    return 0 if data["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
