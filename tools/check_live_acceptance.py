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
one comparison function.
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
GITLAB_RECEIPT_KINDS = {
    "gitlab_access",
    "gitlab_milestone",
    "gitlab_issue",
    "gitlab_wiki",
    "gitlab_runner",
}
REQUIRED_EXPECTATIONS = (
    "targets",
    "required_live_checks",
    "required_checks",
    "max_receipt_age_days",
    "gitlab_receipts",
)
REPEATABLE_GITLAB_OPERATIONS = (
    ("gitlab_milestone", "create"),
    ("gitlab_milestone", "update"),
    ("gitlab_milestone", "adjust-time"),
    ("gitlab_issue", "open-bug"),
    ("gitlab_wiki", "create"),
    ("gitlab_wiki", "update"),
    ("gitlab_runner", "assign"),
)
REQUIRED_GITLAB_OPERATIONS = {
    ("gitlab_access", None, None),
    ("gitlab_runner", "create", "APPLIED"),
} | {
    (kind, operation, action)
    for kind, operation in REPEATABLE_GITLAB_OPERATIONS
    for action in ("APPLIED", "NO_OP")
}


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
    window = data["max_receipt_age_days"]
    if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
        raise AcceptanceError("max_receipt_age_days_invalid")
    operations = data.get("gitlab_receipts", [])
    if not isinstance(operations, list) or any(
        not isinstance(item, dict)
        or item.get("kind") not in GITLAB_RECEIPT_KINDS
        or any(
            not item.get(field)
            for field in (
                "execution_host",
                "origin",
                "username",
                "target_kind",
                "target_id",
                "target_path",
            )
        )
        or (
            item.get("kind") != "gitlab_access"
            and (
                not item.get("operation")
                or item.get("result_action") not in {"APPLIED", "NO_OP"}
            )
        )
        for item in operations
    ):
        raise AcceptanceError("gitlab_receipt_expectation_invalid")
    declared = {
        (item["kind"], item.get("operation"), item.get("result_action"))
        for item in operations
    }
    for kind, operation, action in sorted(
        REQUIRED_GITLAB_OPERATIONS - declared,
        key=lambda entry: tuple(str(value) for value in entry),
    ):
        raise AcceptanceError(
            f"gitlab_receipt_expectation_missing:{kind}:{operation or 'check'}:{action or 'check'}"
        )
    return data


def _load_receipts(directory: Path) -> dict[str, Any]:
    if not directory.is_dir():
        raise AcceptanceError(f"receipt_directory_missing:{directory}")
    receipts: dict[str, Any] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AcceptanceError(f"receipt_unreadable:{path.name}") from exc
        if not isinstance(receipt, dict):
            raise AcceptanceError(f"receipt_invalid_shape:{path.name}")
        receipts[path.name] = receipt
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


def _check_common(
    name: str,
    receipt: dict[str, Any],
    digest: str,
    now: datetime,
    max_age_days: int,
) -> list[str]:
    """Verify fields shared by diagnostics and GitLab operation receipts."""
    problems: list[str] = []
    if receipt.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"{name}:schema_version_unexpected")
    if receipt.get("status") != "PASS":
        problems.append(f"{name}:status_not_pass")
    reported = (receipt.get("skill") or {}).get("digest")
    if not reported:
        problems.append(f"{name}:skill_digest_absent")
    elif reported != digest:
        problems.append(f"{name}:skill_digest_mismatch")
    captured = _parse_time(receipt.get("captured_at"))
    if captured is None:
        problems.append(f"{name}:captured_at_unreadable")
    elif captured > now:
        problems.append(f"{name}:captured_in_the_future")
    elif (now - captured).days > max_age_days:
        problems.append(f"{name}:receipt_stale")
    redactor = _redactor()
    if redactor is None:
        problems.append(f"{name}:sanitization_check_unavailable")
    elif redactor(receipt) != receipt:
        problems.append(f"{name}:receipt_not_sanitized")
    return problems


def _check_receipt(
    name: str,
    receipt: dict[str, Any],
    executor: dict[str, Any],
    expected: dict[str, Any],
    digest: str,
    now: datetime,
) -> list[str]:
    """Return every reason this receipt does not accept the current skill."""
    problems = _check_common(
        name, receipt, digest, now, expected["max_receipt_age_days"]
    )
    if receipt.get("kind") != RECEIPT_KIND:
        problems.append(f"{name}:kind_unexpected")
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

    return problems


def _check_gitlab_receipt(
    name: str,
    receipt: dict[str, Any],
    required: dict[str, Any],
    expected: dict[str, Any],
    digest: str,
    now: datetime,
) -> list[str]:
    """Accept one exact-host, exact-target GitLab access or action read-back."""
    problems = _check_common(
        name, receipt, digest, now, expected["max_receipt_age_days"]
    )
    kind = required["kind"]
    if kind not in GITLAB_RECEIPT_KINDS or receipt.get("kind") != kind:
        problems.append(f"{name}:kind_unexpected")
    for field in ("execution_host", "origin"):
        if receipt.get(field) != required.get(field):
            problems.append(f"{name}:{field}_mismatch")
    identity = receipt.get("identity") or {}
    if identity.get("username") != required.get("username"):
        problems.append(f"{name}:identity_mismatch")
    if not receipt.get("credential_source") or not receipt.get("credential_digest"):
        problems.append(f"{name}:credential_source_unresolved")
    if not receipt.get("target_source"):
        problems.append(f"{name}:target_source_unresolved")
    target = (
        receipt.get("target")
        if kind == "gitlab_access"
        else receipt.get("verified_target")
    ) or {}
    if (
        target.get("kind") != required.get("target_kind")
        or target.get("id") != required.get("target_id")
        or target.get("full_path") != required.get("target_path")
    ):
        problems.append(f"{name}:target_mismatch")
    if kind == "gitlab_access":
        if not {"identity_read", "target_read"} <= set(
            receipt.get("observed_capability") or []
        ):
            problems.append(f"{name}:access_readback_missing")
    elif (
        receipt.get("phase") != "APPLY"
        or receipt.get("mutated") is not (required.get("result_action") == "APPLIED")
        or receipt.get("operation") != required.get("operation")
        or receipt.get("result_action") != required.get("result_action")
        or not isinstance(receipt.get("readback"), dict)
        or receipt["readback"].get("verified") is not True
        or receipt["readback"].get("action") != required.get("result_action")
        or receipt.get("errors")
    ):
        problems.append(f"{name}:operation_readback_missing")
    return problems


def _redactor():
    """Return the skill's own redactor, so one rule covers capture and review."""
    root = Path(__file__).resolve().parents[1]
    scripts = root / "skills" / "k8s-admin-diagnostics" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        from core.runtime import redact_tree
    except ImportError:
        return None
    return redact_tree


def _resource_identity(receipt: dict[str, Any]) -> Any:
    readback = receipt.get("readback") or {}
    if not isinstance(readback, dict):
        return None
    for key in ("id", "iid", "slug"):
        if readback.get(key):
            return (key, readback[key])
    return None


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
    by_host: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for name, receipt in receipts.items():
        by_host.setdefault(receipt.get("execution_host") or "", []).append(
            (name, receipt)
        )
    problems: list[str] = []
    accepted: list[str] = []
    consumed: set[str] = set()
    matched_operations: dict[
        tuple[Any, ...], dict[str, tuple[str, dict[str, Any]]]
    ] = {}
    for executor in expected["executors"]:
        host = executor.get("host")
        host_receipts = by_host.get(host, [])
        candidates = [
            item for item in host_receipts if item[1].get("kind") == RECEIPT_KIND
        ]
        if not candidates and len(host_receipts) == 1:
            # Preserve a precise kind error for a lone malformed diagnostic.
            candidates = host_receipts
        if not candidates:
            problems.append(f"{executor.get('label', host)}:receipt_missing")
            continue
        if len(candidates) != 1:
            problems.append(f"{executor.get('label', host)}:receipt_ambiguous")
            consumed.update(name for name, _receipt in candidates)
            continue
        name, receipt = candidates[0]
        consumed.add(name)
        try:
            found = _check_receipt(name, receipt, executor, expected, digest, now)
        except (AttributeError, KeyError, TypeError, ValueError):
            found = [f"{name}:receipt_invalid_shape"]
        problems.extend(found)
        if not found:
            accepted.append(name)

    for required in expected.get("gitlab_receipts", []):
        kind = required["kind"]
        operation = required.get("operation") if kind != "gitlab_access" else None
        label = required.get("label") or f"{kind}:{operation or 'check'}"
        candidates = [
            (name, receipt)
            for name, receipt in by_host.get(required["execution_host"], [])
            if receipt.get("kind") == kind
            and receipt.get("operation") == operation
            and (
                kind == "gitlab_access"
                or receipt.get("result_action") == required.get("result_action")
            )
        ]
        if len(candidates) > 1:
            # The same operation may be required on several targets. Match the
            # exact selected target before calling the remaining evidence
            # ambiguous. A lone wrong-target receipt stays a precise mismatch.
            candidates = [
                (name, receipt)
                for name, receipt in candidates
                if (receipt.get("origin") == required.get("origin"))
                and (
                    (
                        receipt.get("target")
                        if kind == "gitlab_access"
                        else receipt.get("verified_target")
                    )
                    or {}
                )
                == {
                    "kind": required.get("target_kind"),
                    "id": required.get("target_id"),
                    "full_path": required.get("target_path"),
                }
            ]
        if not candidates:
            problems.append(f"{label}:receipt_missing")
            continue
        if len(candidates) != 1:
            problems.append(f"{label}:receipt_ambiguous")
            consumed.update(name for name, _receipt in candidates)
            continue
        name, receipt = candidates[0]
        consumed.add(name)
        try:
            found = _check_gitlab_receipt(
                name, receipt, required, expected, digest, now
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            found = [f"{name}:receipt_invalid_shape"]
        problems.extend(found)
        if not found:
            accepted.append(name)
            if (kind, operation) in REPEATABLE_GITLAB_OPERATIONS:
                pair = (
                    required["execution_host"],
                    required["origin"],
                    kind,
                    operation,
                    required["target_kind"],
                    required["target_id"],
                    required["target_path"],
                )
                matched_operations.setdefault(pair, {})[required["result_action"]] = (
                    name,
                    receipt,
                )

    for pair, actions in matched_operations.items():
        if set(actions) != {"APPLIED", "NO_OP"}:
            problems.append(f"{pair[2]}:{pair[3]}:repeat_readback_missing")
            continue
        applied_name, applied = actions["APPLIED"]
        noop_name, noop = actions["NO_OP"]
        if (
            not applied.get("plan_digest")
            or applied.get("plan_digest") != noop.get("plan_digest")
            or not _resource_identity(applied)
            or _resource_identity(applied) != _resource_identity(noop)
            or applied.get("credential_source") != noop.get("credential_source")
            or applied.get("credential_digest") != noop.get("credential_digest")
        ):
            problems.append(f"{applied_name}:{noop_name}:repeat_readback_mismatch")

    declared_hosts = {executor.get("host") for executor in expected["executors"]}
    for name, receipt in receipts.items():
        if name in consumed:
            continue
        if (receipt.get("execution_host") or "") not in declared_hosts:
            problems.append(f"{name}:executor_not_declared")
        else:
            problems.append(f"{name}:receipt_not_declared")

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
    except (AcceptanceError, OSError, ValueError, TypeError) as exc:
        data = {
            "schema_version": SCHEMA_VERSION,
            "kind": "live_acceptance",
            "status": "BLOCKED",
            "accepted": [],
            "problems": [str(exc)],
        }

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
