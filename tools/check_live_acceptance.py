#!/usr/bin/env python3
"""Verify that committed live-access receipts actually accept this revision.

Mocked CI proves code behavior. It cannot prove access, because the fake `gh`,
`glab` and `kubectl` answer whatever the fixtures say. So acceptance consumes
the receipt a real host produced and refuses it when it is missing, stale, from
a different authenticated setup, or produced by different code.

The candidate check pairs the exact source commit with the skill tree digest.
The receipt commit cannot contain its own SHA, so acceptance verifies that the
tested source commit's skill tree equals the checked-out candidate before
comparing the recomputed digest. Squash merges may change commit ancestry
without changing the tested code.

The initial access receipts hold the selected targets and API-read identities.
Later operation receipts are compared with that setup; no checked-in environment
or identity values are a second authority.

Mustafa Byarmov mbayramo@ciso.com / spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION = "1.0"
RECEIPT_KIND = "access_check"
GITLAB_RECEIPT_KINDS = {
    "gitlab_access",
    "gitlab_milestone",
    "gitlab_issue",
    "gitlab_wiki",
    "gitlab_runner",
}
# Preserve the existing acceptance window and operation coverage in one place.
MAX_RECEIPT_AGE_DAYS: Final = 30
REQUIRED_LIVE_CHECKS: Final = (
    "storage_report",
    "event_trace",
    "cilium_status",
    "ceph_cluster",
    "gitlab_job",
)
SETUP_RECEIPT_KINDS: Final = {RECEIPT_KIND, "gitlab_access"}
REPEATABLE_GITLAB_OPERATIONS = (
    ("gitlab_milestone", "create"),
    ("gitlab_milestone", "update"),
    ("gitlab_milestone", "adjust-time"),
    ("gitlab_issue", "open-bug"),
    ("gitlab_wiki", "create"),
    ("gitlab_wiki", "update"),
    ("gitlab_runner", "assign"),
    ("gitlab_runner", "tag"),
)
READ_ONLY_GITLAB_OPERATIONS = (
    ("gitlab_runner", "get"),
    ("gitlab_runner", "list"),
)
RUNNER_DELETE_OPERATIONS = (
    ("gitlab_runner", "delete", "APPLIED"),
    ("gitlab_runner", "delete", "NO_OP"),
)
REQUIRED_GITLAB_OPERATIONS = (
    {
        ("gitlab_access", None, None),
        ("gitlab_runner", "create", "APPLIED"),
        *RUNNER_DELETE_OPERATIONS,
    }
    | {
        (kind, operation, action)
        for kind, operation in REPEATABLE_GITLAB_OPERATIONS
        for action in ("APPLIED", "NO_OP")
    }
    | {(kind, operation, None) for kind, operation in READ_ONLY_GITLAB_OPERATIONS}
)
DEFAULT_RECEIPT_DIRECTORIES = (
    "receipts",
    "runner-tag",
    "runner-lifecycle",
)


class AcceptanceError(RuntimeError):
    """Acceptance inputs could not be read."""


def _mapping(value: Any) -> dict[str, Any]:
    """Return a mapping for guarded receipt reads, without accepting its shape."""
    return value if isinstance(value, dict) else {}


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


def _load_receipt_dirs(paths: Sequence[Path]) -> dict[str, Any]:
    """Load committed acceptance receipts from one or more directories.

    :param paths: Candidate receipt directories in priority order.
    :returns: Mapping of unique file names to parsed receipt objects.
    :raises AcceptanceError: If a required directory is unreadable, a receipt
        is malformed, or two directories contain the same receipt name.
    """
    receipts: dict[str, Any] = {}
    for index, directory in enumerate(paths):
        if not directory.exists():
            if index == 0:
                raise AcceptanceError(f"receipt_directory_missing:{directory}")
            continue
        for name, receipt in _load_receipts(directory).items():
            if name in receipts:
                raise AcceptanceError(f"receipt_duplicate:{name}")
            receipts[name] = receipt
    if not receipts:
        raise AcceptanceError("receipts_missing")
    return receipts


def check_candidate_identity(
    receipt: dict[str, Any], repository_root: Path, skill_root: Path
) -> list[str]:
    """Verify a committed receipt against the exact candidate skill tree.

    :param receipt: Live receipt with a verified tested source revision.
    :param repository_root: Git checkout containing the candidate HEAD.
    :param skill_root: Candidate's ``ci-skills`` directory.
    :returns: Stable reasons when source, tree identity, or digest differs.
    """
    skill = _mapping(receipt.get("skill"))
    source = _mapping(skill.get("revision"))
    revision = receipt.get("tested_revision")
    if receipt.get("kind") == "gitlab_access" and revision is None:
        # Access receipts carry the same verified source under skill.revision.
        revision = source.get("value")
    if (
        not isinstance(revision, str)
        or re.fullmatch(r"[0-9a-f]{40}", revision) is None
        or source.get("verified") is not True
        or source.get("value") != revision
    ):
        return ["candidate_revision_unverified"]

    def git(*args: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", "-C", str(repository_root), *args],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None

    source_commit = git("cat-file", "-e", f"{revision}^{{commit}}")
    if source_commit is None or source_commit.returncode != 0:
        return ["candidate_revision_unavailable"]
    tested_tree = git("rev-parse", f"{revision}:ci-skills")
    current_tree = git("rev-parse", "HEAD:ci-skills")
    if (
        tested_tree is None
        or current_tree is None
        or tested_tree.returncode != 0
        or current_tree.returncode != 0
        or tested_tree.stdout.strip() != current_tree.stdout.strip()
    ):
        return ["candidate_skill_tree_mismatch"]
    if _redactor() is None:
        return ["candidate_digest_unavailable"]
    try:
        from core.provenance import tree_digest

        current = tree_digest(skill_root)
    except (ImportError, OSError, ValueError):
        return ["candidate_digest_unavailable"]
    if any(
        skill.get(key) != current[key] for key in ("algorithm", "digest", "file_count")
    ):
        return ["candidate_skill_digest_mismatch"]
    return []


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
    reported = _mapping(receipt.get("skill")).get("digest")
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
    digest: str,
    now: datetime,
) -> list[str]:
    """Validate the publication setup's own live access and read-back evidence."""
    from core.catalog import AUTHORITIES

    problems = _check_common(name, receipt, digest, now, MAX_RECEIPT_AGE_DAYS)
    if receipt.get("kind") != RECEIPT_KIND:
        problems.append(f"{name}:kind_unexpected")
    if receipt.get("publication") is not True:
        problems.append(f"{name}:not_a_publication_receipt")
    revision = _mapping(_mapping(receipt.get("skill")).get("revision"))
    tested_revision = receipt.get("tested_revision")
    if (
        not isinstance(revision, dict)
        or revision.get("verified") is not True
        or not isinstance(tested_revision, str)
        or re.fullmatch(r"[0-9a-f]{40}", tested_revision) is None
        or revision.get("value") != tested_revision
    ):
        problems.append(f"{name}:tested_revision_unverified")
    if not receipt.get("execution_host"):
        problems.append(f"{name}:execution_host_missing")
    surfaces = _mapping(receipt.get("surfaces"))
    sources = _mapping(receipt.get("credential_sources"))
    targets = _mapping(receipt.get("targets"))
    for surface in AUTHORITIES:
        observed = _mapping(surfaces.get(surface))
        if not observed.get("identity"):
            problems.append(f"{name}:identity_missing:{surface}")
        if observed.get("status") != "PASS":
            problems.append(f"{name}:surface_not_pass:{surface}")
        source = sources.get(surface)
        if not source or observed.get("credential_source") != source:
            problems.append(f"{name}:credential_source_mismatch:{surface}")
        target = targets.get(surface)
        if surface == "kubernetes":
            cluster = _mapping(target)
            if not cluster.get("context") or not cluster.get("server"):
                problems.append(f"{name}:surface_target_missing:{surface}")
            target = f"{cluster.get('context')} -> {cluster.get('server')}"
        if not target or observed.get("target") != target:
            problems.append(f"{name}:surface_target_mismatch:{surface}")

    live = _mapping(receipt.get("live_checks"))
    for required in REQUIRED_LIVE_CHECKS:
        check = live.get(required)
        if check is None:
            problems.append(f"{name}:live_check_missing:{required}")
        elif not isinstance(check, dict):
            problems.append(f"{name}:live_check_invalid:{required}")
        elif check.get("access_proven") is not True:
            problems.append(f"{name}:live_check_unproven:{required}")
        if isinstance(check, dict) and (
            check.get("status") not in {"PASS", "PARTIAL"}
            or not isinstance(check.get("readback_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", check["readback_sha256"]) is None
        ):
            problems.append(f"{name}:live_readback_missing:{required}")
    job = _mapping(live.get("gitlab_job"))
    readback = job.get("job_readback")
    if not job.get("job_url"):
        problems.append(f"{name}:job_target_missing")
    if (
        not isinstance(readback, dict)
        or any(
            type(readback.get(field)) is not int or readback[field] <= 0
            for field in ("job_id", "pipeline_id", "runner_id")
        )
        or (
            type(_mapping(readback).get("trace_line_count")) is not int
            or readback["trace_line_count"] < 0
        )
    ):
        problems.append(f"{name}:job_readback_missing")
    if not _mapping(live.get("ceph_cluster")).get("namespace"):
        problems.append(f"{name}:ceph_namespace_missing")
    if receipt.get("blocking_live_checks"):
        problems.append(f"{name}:live_checks_blocking")
    github = _mapping(surfaces.get("github"))
    if "required_checks_read" not in (
        github.get("observed_capability") or []
    ) or not _mapping(github.get("details")).get("required_checks"):
        problems.append(f"{name}:required_checks_readback_missing")
    return problems


def _gitlab_context(receipt: dict[str, Any]) -> dict[str, Any]:
    """Extract runtime expectations from an initial GitLab access receipt."""
    target = _mapping(receipt.get("target"))
    identity = _mapping(receipt.get("identity"))
    return {
        "execution_host": receipt.get("execution_host"),
        "origin": receipt.get("origin"),
        "username": identity.get("username"),
        "identity_id": identity.get("id"),
        "target_kind": target.get("kind"),
        "target_id": target.get("id"),
        "target_path": target.get("full_path"),
        "credential_source": receipt.get("credential_source"),
        "credential_digest": receipt.get("credential_digest"),
    }


def _matches_context(receipt: dict[str, Any], context: dict[str, Any]) -> bool:
    """Locate the initial access proof for this exact host, origin and target."""
    target = _mapping(receipt.get("verified_target"))
    return (
        receipt.get("execution_host") == context.get("execution_host")
        and receipt.get("origin") == context.get("origin")
        and target.get("kind") == context.get("target_kind")
        and target.get("id") == context.get("target_id")
        and target.get("full_path") == context.get("target_path")
    )


def _check_gitlab_receipt(
    name: str,
    receipt: dict[str, Any],
    required: dict[str, Any],
    digest: str,
    now: datetime,
) -> list[str]:
    """Accept one exact-host, exact-target GitLab access or action read-back."""
    problems = _check_common(name, receipt, digest, now, MAX_RECEIPT_AGE_DAYS)
    kind = required["kind"]
    if kind not in GITLAB_RECEIPT_KINDS or receipt.get("kind") != kind:
        problems.append(f"{name}:kind_unexpected")
    for field in ("execution_host", "origin"):
        if receipt.get(field) != required.get(field):
            problems.append(f"{name}:{field}_mismatch")
    identity = receipt.get("identity") or {}
    if identity.get("username") != required.get("username") or identity.get(
        "id"
    ) != required.get("identity_id"):
        problems.append(f"{name}:identity_mismatch")
    if any(
        receipt.get(field) != required.get(field)
        for field in ("credential_source", "credential_digest")
    ):
        problems.append(f"{name}:credential_context_mismatch")
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
    operation = required.get("operation") if kind != "gitlab_access" else None
    if kind == "gitlab_access":
        if not {"identity_read", "target_read"} <= set(
            receipt.get("observed_capability") or []
        ):
            problems.append(f"{name}:access_readback_missing")
    elif (kind, operation) in READ_ONLY_GITLAB_OPERATIONS:
        problems.extend(_check_gitlab_read_receipt(name, receipt, required))
    elif (
        receipt.get("phase") != "APPLY"
        or receipt.get("mutated") is not (required.get("result_action") == "APPLIED")
        or receipt.get("operation") != operation
        or receipt.get("result_action") != required.get("result_action")
        or not isinstance(receipt.get("readback"), dict)
        or receipt["readback"].get("verified") is not True
        or receipt["readback"].get("action") != required.get("result_action")
        or receipt.get("errors")
    ):
        problems.append(f"{name}:operation_readback_missing")
    if (
        kind == "gitlab_runner"
        and required.get("operation") == "create"
        and required.get("result_action") == "APPLIED"
        and not _runner_smoke_cleanup_verified(receipt, required, digest, now)
    ):
        problems.append(f"{name}:runner_smoke_cleanup_unproven")
    if (
        kind == "gitlab_runner"
        and operation == "delete"
        and not _runner_delete_readback_verified(receipt, required)
    ):
        problems.append(f"{name}:runner_delete_readback_unproven")
    return problems


def _check_gitlab_read_receipt(
    name: str, receipt: dict[str, Any], required: dict[str, Any]
) -> list[str]:
    """Validate a live read receipt that must not mutate GitLab.

    :param name: Receipt file name used in diagnostics.
    :param receipt: Parsed receipt object.
    :param required: Expected operation and target entry.
    :returns: Acceptance problems for this read receipt.
    """
    problems: list[str] = []
    records = receipt.get("records")
    readback = receipt.get("readback")
    if (
        receipt.get("status") != "PASS"
        or receipt.get("phase") != "READ"
        or receipt.get("mutated") is not False
        or receipt.get("operation") != required.get("operation")
        or receipt.get("result_action") is not None
        or receipt.get("errors")
        or not isinstance(records, list)
        or not records
        or not isinstance(readback, dict)
        or readback.get("verified") is not True
        or readback.get("record_count") != len(records)
    ):
        problems.append(f"{name}:read_operation_readback_missing")
    if required.get("operation") == "get" and (
        not isinstance(records, list) or len(records) != 1
    ):
        problems.append(f"{name}:runner_get_cardinality_unexpected")
    if isinstance(records, list) and any(
        not isinstance(record, dict)
        or type(record.get("id")) is not int
        or record["id"] <= 0
        for record in records
    ):
        problems.append(f"{name}:runner_record_identity_missing")
    return problems


def _runner_delete_readback_verified(
    receipt: dict[str, Any], required: dict[str, Any]
) -> bool:
    """Check delete-specific before/action/absence evidence.

    :param receipt: Parsed runner delete receipt.
    :param required: Expected delete operation and result action.
    :returns: ``True`` when delete read-back proves the declared result.
    """
    record = receipt.get("readback")
    records = receipt.get("records")
    plan = receipt.get("plan")
    if (
        not isinstance(record, dict)
        or not isinstance(records, list)
        or records != [record]
        or not isinstance(plan, dict)
        or record.get("action") != required.get("result_action")
        or record.get("verified") is not True
        or record.get("id") != plan.get("resource_id")
        or type(record.get("id")) is not int
        or record["id"] <= 0
    ):
        return False
    after = record.get("after")
    if (
        not isinstance(after, dict)
        or after.get("global_get_status") != 404
        or after.get("scoped_absent") is not True
        or after.get("write_error") not in (None, "")
    ):
        return False
    before = record.get("before")
    snapshot = plan.get("runner_snapshot")
    if required.get("result_action") == "APPLIED":
        return (
            record.get("mutated") is True
            and isinstance(before, dict)
            and before.get("id") == record["id"]
            and isinstance(snapshot, dict)
            and snapshot.get("id") == record["id"]
            and snapshot.get("present") is True
        )
    return (
        record.get("mutated") is False
        and before is None
        and isinstance(snapshot, dict)
        and snapshot.get("id") == record["id"]
        and snapshot.get("present") is False
    )


def _runner_smoke_cleanup_verified(
    receipt: dict[str, Any],
    required: dict[str, Any],
    digest: str,
    now: datetime,
) -> bool:
    """Require independent absence read-back for a disposable created runner."""
    cleanup = receipt.get("smoke_cleanup")
    if not isinstance(cleanup, dict):
        return False
    captured = _parse_time(cleanup.get("captured_at"))
    created = _parse_time(receipt.get("captured_at"))
    runner_id = (receipt.get("readback") or {}).get("id")
    before = cleanup.get("before")
    deletion = cleanup.get("delete")
    after = cleanup.get("after")
    revision = (receipt.get("skill") or {}).get("revision") or {}
    if (
        cleanup.get("schema_version") != SCHEMA_VERSION
        or cleanup.get("kind") != "gitlab_runner_smoke_cleanup"
        or cleanup.get("status") != "PASS"
        or cleanup.get("execution_host") != receipt.get("execution_host")
        or cleanup.get("origin") != receipt.get("origin")
        or cleanup.get("credential_source") != receipt.get("credential_source")
        or cleanup.get("skill_digest") != digest
        or revision.get("verified") is not True
        or not isinstance(revision.get("value"), str)
        or not revision["value"]
        or cleanup.get("tested_revision") != revision["value"]
        or type(runner_id) is not int
        or runner_id <= 0
        or cleanup.get("runner_id") != runner_id
        or captured is None
        or created is None
        or not created <= captured <= now
        or (now - captured).days > MAX_RECEIPT_AGE_DAYS
        or not isinstance(before, dict)
        or before.get("id") != runner_id
        or not isinstance(deletion, dict)
        or deletion.get("method") != "DELETE"
        or deletion.get("endpoint") != f"runners/{runner_id}"
        or deletion.get("exit_code") != 0
        or not isinstance(after, dict)
        or after.get("global_get_http_status") != 404
        or type(after.get("global_get_exit_code")) is not int
        or after["global_get_exit_code"] == 0
    ):
        return False
    project_ids = before.get("project_ids")
    if (
        not isinstance(project_ids, list)
        or not project_ids
        or any(
            type(project_id) is not int or project_id <= 0 for project_id in project_ids
        )
        or required.get("target_id") not in project_ids
    ):
        return False
    return all(
        after.get(f"project_{project_id}_absent") is True for project_id in project_ids
    )


def _redactor():
    """Return the skill's own redactor, so one rule covers capture and review."""
    root = Path(__file__).resolve().parents[1]
    library = root / "ci-skills" / "lib" / "python"
    if str(library) not in sys.path:
        sys.path.insert(0, str(library))
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
    setup: dict[str, Any],
    receipts: dict[str, Any],
    skill_root: Path,
    *,
    now: datetime | None = None,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    """Compare action receipts with the initial authenticated runtime setup.

    :param setup: Existing access receipts keyed by name, captured before writes.
    :param receipts: Parsed operation and setup receipts keyed by file name.
    :param skill_root: Candidate skill tree used for digest comparison.
    :param now: Optional reference time for receipt freshness.
    :param repository_root: Git checkout for exact source commit verification.
    :returns: Acceptance status, accepted receipts, and stable problem reasons.
    """
    from core.provenance import tree_digest

    now = now or datetime.now(UTC)
    digest = tree_digest(skill_root)["digest"]
    problems: list[str] = []
    accepted: list[str] = []
    contexts: list[dict[str, Any]] = []
    publication_hosts: set[str] = set()
    observed_operations: set[tuple[str, str | None, str | None]] = set()
    matched_operations: dict[
        tuple[Any, ...], dict[str, tuple[str, dict[str, Any]]]
    ] = {}
    for name, receipt in setup.items():
        kind = receipt.get("kind")
        try:
            if kind == RECEIPT_KIND:
                found = _check_receipt(name, receipt, digest, now)
                host = receipt.get("execution_host")
                if host in publication_hosts:
                    found.append(f"{name}:receipt_ambiguous")
                publication_hosts.add(host)
            elif kind == "gitlab_access":
                context = _gitlab_context(receipt)
                found = _check_gitlab_receipt(
                    name, receipt, {**context, "kind": kind}, digest, now
                )
                if (
                    any(not value for value in context.values())
                    or type(context["identity_id"]) is not int
                    or context["identity_id"] <= 0
                    or type(context["target_id"]) is not int
                    or context["target_id"] <= 0
                    or receipt.get("errors")
                ):
                    found.append(f"{name}:setup_context_incomplete")
            else:
                found = [f"{name}:setup_kind_unexpected"]
            if repository_root is not None:
                found.extend(
                    f"{name}:{reason}"
                    for reason in check_candidate_identity(
                        receipt, repository_root, skill_root
                    )
                )
        except (AttributeError, KeyError, TypeError, ValueError):
            found = [f"{name}:receipt_invalid_shape"]
        problems.extend(found)
        if not found:
            accepted.append(name)
            if kind == "gitlab_access":
                contexts.append(context)
                observed_operations.add((kind, None, None))
    if not any(setup[name].get("kind") == RECEIPT_KIND for name in accepted):
        problems.append("access_check:setup_missing")

    for name, receipt in receipts.items():
        if name in setup:
            if receipt != setup[name]:
                problems.append(f"{name}:setup_receipt_mismatch")
            continue
        kind = receipt.get("kind")
        operation = receipt.get("operation")
        result_action = receipt.get("result_action")
        operation_key = (kind, operation, result_action)
        if operation_key not in REQUIRED_GITLAB_OPERATIONS:
            problems.append(f"{name}:receipt_not_declared")
            continue
        candidates = [
            context for context in contexts if _matches_context(receipt, context)
        ]
        if len(candidates) != 1:
            problems.append(f"{name}:setup_context_missing_or_ambiguous")
            continue
        required = {
            **candidates[0],
            "kind": kind,
            "operation": operation,
            "result_action": result_action,
        }
        try:
            found = _check_gitlab_receipt(name, receipt, required, digest, now)
            if repository_root is not None:
                found.extend(
                    f"{name}:{reason}"
                    for reason in check_candidate_identity(
                        receipt, repository_root, skill_root
                    )
                )
        except (AttributeError, KeyError, TypeError, ValueError):
            found = [f"{name}:receipt_invalid_shape"]
        problems.extend(found)
        if not found:
            accepted.append(name)
            observed_operations.add(operation_key)
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
                actions = matched_operations.setdefault(pair, {})
                if result_action in actions:
                    problems.append(f"{name}:receipt_ambiguous")
                actions[result_action] = (name, receipt)

    for kind, operation, action in sorted(
        REQUIRED_GITLAB_OPERATIONS - observed_operations,
        key=lambda entry: tuple(str(value) for value in entry),
    ):
        problems.append(
            f"{kind}:{operation or 'check'}:{action or 'check'}:receipt_missing"
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

    return {
        "schema_version": SCHEMA_VERSION,
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
        "--receipts",
        metavar="PATH",
        help="one receipt directory (default: all configured acceptance directories)",
    )
    cli.add_argument(
        "--skill",
        metavar="PATH",
        help="skill root (default: <root>/ci-skills)",
    )
    modes = cli.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", help="print JSON")
    modes.add_argument("--yaml", action="store_true", help="print YAML")
    args = cli.parse_args()

    root = Path(args.root).resolve()
    receipts_paths = (
        [Path(args.receipts)]
        if args.receipts
        else [
            root / "tests" / "acceptance" / directory
            for directory in DEFAULT_RECEIPT_DIRECTORIES
        ]
    )
    skill_path = Path(args.skill) if args.skill else root / "ci-skills"
    _redactor()
    try:
        receipts = _load_receipt_dirs(receipts_paths)
        setup = {
            name: receipt
            for name, receipt in receipts.items()
            if receipt.get("kind") in SETUP_RECEIPT_KINDS
        }
        data = evaluate(
            setup,
            receipts,
            skill_path,
            repository_root=root,
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
