#!/usr/bin/env python3
"""Verify that committed live-access receipts actually accept this revision.

Mocked CI proves code behavior. It cannot prove access, because the fake `gh`,
`glab` and `kubectl` answer whatever the fixtures say. So acceptance consumes
the receipt a real host produced and refuses it when it is missing, stale, from
a different authenticated setup, or produced by different code.

The candidate check compares the recorded functional checksum with ``bin/``,
``lib/``, and ``schemas/`` using the installed skill's shared implementation.
The receipt directories and the freshness window are declared once in the
tracked ``inventory/ci/live-acceptance.json`` policy; nothing here hardcodes them.
The whole-package checksum remains evidence of the original installed package;
documentation-only changes may alter that checksum without changing runtime
behavior. The receipt keeps its original source revision and package identity.

The initial access receipts hold the selected targets and API-read identities.
Later operation receipts are compared with that setup; no checked-in environment
or identity values are a second authority.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import re
import runpy
from collections.abc import Callable, Mapping, MutableMapping, MutableSequence, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Optional, TypeAlias, TypedDict

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

SKILL_ROOT: Final = Path(__file__).resolve().parents[1] / "ci-skills"
runpy.run_path(str(SKILL_ROOT / "bin" / "_bootstrap.py"))

from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    json_integer,
    json_string,
    json_strings,
)
from lib.python.core.logging import add_log_arguments, log_event
from lib.python.sys.status import BLOCKED, DRY_RUN, PASS, exit_code

OperationKey: TypeAlias = tuple[str, Optional[str], Optional[str]]
ReceiptPairKey: TypeAlias = tuple[str, str, str, str, str, int, str]


class AcceptanceResult(TypedDict, total=False):
    """Acceptance envelope retaining only accepted names and classified failures.

    :param schema_version: Stable live-acceptance evidence version.
    :param kind: Existing live_acceptance report identifier.
    :param status: PASS when every required receipt passes, otherwise BLOCKED.
    :param skill_digest: Measured whole-package digest of the candidate.
    :param functional_digest: Candidate runtime and schema digest used for acceptance.
    :param accepted: Sorted names of accepted setup and operation receipts.
    :param problems: Sorted classified acceptance failures, or a loading diagnostic.
    :param policy: Declared policy values, present only in a dry run.
    :param receipts: Sorted receipt names that would be evaluated, dry run only.

    .. attribute :: schema_version
        :type: str

        Stable live-acceptance evidence version.

    .. attribute :: kind
        :type: str

        Existing live_acceptance report identifier.

    .. attribute :: status
        :type: str

        PASS when every required receipt passes, otherwise BLOCKED.

    .. attribute :: skill_digest
        :type: str

        Measured whole-package digest of the candidate.

    .. attribute :: functional_digest
        :type: str

        Candidate runtime and schema digest used for acceptance.

    .. attribute :: accepted
        :type: Sequence[str]

        Sorted names of accepted setup and operation receipts.

    .. attribute :: problems
        :type: Sequence[str]

        Sorted classified acceptance failures, or a loading diagnostic.

    .. attribute :: policy
        :type: AcceptancePolicy

        Declared policy values, present only in a dry run.

    .. attribute :: receipts
        :type: Sequence[str]

        Sorted receipt names that would be evaluated, dry run only.
    """

    schema_version: str
    kind: str
    status: str
    skill_digest: str
    functional_digest: str
    accepted: Sequence[str]
    problems: Sequence[str]
    policy: AcceptancePolicy
    receipts: Sequence[str]


SCHEMA_VERSION: Final = "1.0"
RECEIPT_KIND: Final = "access_check"
GITLAB_RECEIPT_KINDS: Final = frozenset(
    {
        "gitlab_access",
        "gitlab_milestone",
        "gitlab_issue",
        "gitlab_wiki",
        "gitlab_runner",
    }
)
# The acceptance window and receipt locations come from the tracked policy file.
POLICY_FILE: Final = Path("inventory") / "ci" / "live-acceptance.json"
POLICY_SCHEMA_FILE: Final = Path("schemas") / "live-acceptance-policy.schema.json"
REQUIRED_LIVE_CHECKS: Final = (
    "storage_report",
    "event_trace",
    "cilium_status",
    "ceph_cluster",
    "gitlab_job",
)
SETUP_RECEIPT_KINDS: Final = frozenset({RECEIPT_KIND, "gitlab_access"})
REPEATABLE_GITLAB_OPERATIONS: Final = (
    ("gitlab_milestone", "create"),
    ("gitlab_milestone", "update"),
    ("gitlab_milestone", "adjust-time"),
    ("gitlab_issue", "open-bug"),
    ("gitlab_wiki", "create"),
    ("gitlab_wiki", "update"),
    ("gitlab_runner", "assign"),
    ("gitlab_runner", "tag"),
)
READ_ONLY_GITLAB_OPERATIONS: Final = (
    ("gitlab_runner", "get"),
    ("gitlab_runner", "list"),
)
RUNNER_DELETE_OPERATIONS: Final = (
    ("gitlab_runner", "delete", "APPLIED"),
    ("gitlab_runner", "delete", "NO_OP"),
)
REQUIRED_GITLAB_OPERATIONS: Final = frozenset(
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


class AcceptanceError(RuntimeError):
    """Acceptance inputs could not be read."""


class AcceptancePolicy(TypedDict):
    """Declared receipt locations and freshness window for live acceptance.

    :param max_receipt_age_days: Whole days after which a receipt is stale.
    :param receipt_directories: Repository-relative directories holding receipts.
    :param historical_directory: Repository-relative directory never evaluated.
    :param scheduled_workflow: Workflow that re-evaluates receipts on a schedule.
    :param scheduled_cron: Cron expression declared for that workflow.
    :param rerun_procedure: Document anchor describing how receipts are refreshed.

    .. attribute :: max_receipt_age_days
        :type: int

        Whole days after which a receipt is stale.

    .. attribute :: receipt_directories
        :type: Sequence[str]

        Repository-relative directories holding receipts.

    .. attribute :: historical_directory
        :type: str

        Repository-relative directory never evaluated.

    .. attribute :: scheduled_workflow
        :type: str

        Workflow that re-evaluates receipts on a schedule.

    .. attribute :: scheduled_cron
        :type: str

        Cron expression declared for that workflow.

    .. attribute :: rerun_procedure
        :type: str

        Document anchor describing how receipts are refreshed.
    """

    max_receipt_age_days: int
    receipt_directories: Sequence[str]
    historical_directory: str
    scheduled_workflow: str
    scheduled_cron: str
    rerun_procedure: str


def load_policy(path: Path, schema_path: Path) -> AcceptancePolicy:
    """Read the tracked live-acceptance policy and validate it against its schema.

    The JSON Schema owns every field rule; this loader adds only the relation the
    schema cannot express, that the historical directory is never evaluated.

    :param path: Policy file selected by the caller or the repository default.
    :param schema_path: Schema file that defines the policy document.
    :returns: Validated freshness window, receipt directories, and schedule.
    :raises AcceptanceError: If the policy or schema is missing or unreadable (each
        failure names its own file), the schema is invalid, the policy violates the
        schema, or the historical directory is also a receipt directory.
    """
    if not path.is_file():
        raise AcceptanceError(f"policy_missing:{path}")
    if not schema_path.is_file():
        raise AcceptanceError(f"policy_schema_missing:{schema_path}")
    try:
        document = response_decoder().decode(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise AcceptanceError(f"policy_unreadable:{path}") from exc
    try:
        schema = response_decoder().decode(schema_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise AcceptanceError(f"policy_schema_unreadable:{schema_path}") from exc
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(document)
    except SchemaError as exc:
        raise AcceptanceError(f"policy_schema_invalid:{schema_path}") from exc
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "keys"
        raise AcceptanceError(f"policy_invalid:{location}") from exc
    receipt_directories = [str(item) for item in document["receiptDirectories"]]
    historical = str(document["historicalDirectory"])
    if historical in receipt_directories:
        raise AcceptanceError("policy_invalid:historicalDirectory")
    scheduled = document["scheduledCheck"]
    return {
        "max_receipt_age_days": int(document["maxReceiptAgeDays"]),
        "receipt_directories": receipt_directories,
        "historical_directory": historical,
        "scheduled_workflow": str(scheduled["workflow"]),
        "scheduled_cron": str(scheduled["cron"]),
        "rerun_procedure": str(document["rerunProcedure"]),
    }


def _mapping(value: JSONValue) -> JSONObject:
    """Guard a receipt field before reading child keys.

    :param value: Untrusted decoded receipt field.
    :returns: Read-only object view, or an empty object for a missing/nonobject field.
    """
    return value if isinstance(value, Mapping) else {}


def _load_receipts(directory: Path) -> Mapping[str, JSONObject]:
    """Load and validate JSON receipt objects from one directory.

    :param directory: Existing directory containing committed JSON receipts.
    :returns: Receipt mappings keyed by their file names.
    :raises AcceptanceError: If the directory is absent, a file cannot be decoded,
        a receipt root is not an object, or no JSON receipts exist.
    """
    if not directory.is_dir():
        raise AcceptanceError(f"receipt_directory_missing:{directory}")
    receipts: MutableMapping[str, JSONObject] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            receipt = response_decoder().decode(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError) as exc:
            raise AcceptanceError(f"receipt_unreadable:{path.name}") from exc
        if not isinstance(receipt, dict):
            raise AcceptanceError(f"receipt_invalid_shape:{path.name}")
        receipts[path.name] = receipt
    if not receipts:
        raise AcceptanceError("receipts_missing")
    return receipts


def _load_receipt_dirs(paths: Sequence[Path]) -> Mapping[str, JSONObject]:
    """Load committed acceptance receipts from one or more directories.

    :param paths: Declared receipt directories in priority order.
    :returns: Mapping of unique file names to parsed receipt objects.
    :raises AcceptanceError: If a declared directory is absent or unreadable, a
        receipt is malformed, or two directories contain the same receipt name.
    """
    receipts: MutableMapping[str, JSONObject] = {}
    for directory in paths:
        if not directory.is_dir():
            raise AcceptanceError(f"receipt_directory_missing:{directory}")
        for name, receipt in _load_receipts(directory).items():
            if name in receipts:
                raise AcceptanceError(f"receipt_duplicate:{name}")
            receipts[name] = receipt
    if not receipts:
        raise AcceptanceError("receipts_missing")
    return receipts


def _check_source_revision(receipt: JSONObject) -> Sequence[str]:
    """Validate recorded source provenance independently of local Git history.

    :param receipt: Live receipt retaining its original tested source revision.
    :returns: Stable reasons when the revision is unverified or inconsistent.
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
    return []


def check_candidate_identity(receipt: JSONObject, skill_root: Path) -> Sequence[str]:
    """Compare recorded functional bytes with the candidate runtime and schemas.

    :param receipt: Live receipt with a verified source and functional checksum.
    :param skill_root: Candidate's ``ci-skills`` directory.
    :returns: Stable reasons when the source or functional checksum cannot match.
    """
    problems = _check_source_revision(receipt)
    if problems:
        return problems
    try:
        from lib.python.core.provenance import functional_digest

        current = functional_digest(skill_root)
    except (ImportError, OSError, ValueError):
        return ["candidate_digest_unavailable"]
    if _mapping(receipt.get("skill")).get("functional_digest") != current:
        return ["candidate_functional_digest_mismatch"]
    return []


def _parse_time(value: JSONValue) -> Optional[datetime]:
    """Parse a receipt timestamp, treating a naive value as UTC.

    :param value: Untrusted captured-at field from a receipt.
    :returns: A timezone-aware timestamp, or None for invalid input.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _check_common(
    name: str,
    receipt: JSONObject,
    digest: str,
    now: datetime,
    max_age_days: int,
) -> MutableSequence[str]:
    """Check schema, status, functional identity, freshness, and sanitization.

    :param name: Receipt file name prefixed to each reported problem.
    :param receipt: Parsed diagnostic or GitLab operation receipt.
    :param digest: Expected functional digest of the candidate runtime and schemas.
    :param now: Reference time used for freshness and future-date checks.
    :param max_age_days: Maximum accepted age of the receipt in whole days.
    :returns: Mutable problem list owned by the caller for additional checks.
    :raises TypeError: If now is naive while a receipt timestamp is timezone-aware.
    :raises RecursionError: If caller-supplied cyclic data exceeds redaction nesting.
    """  # noqa: DOC502 - Datetime comparison and recursive redaction can fail.
    problems: MutableSequence[str] = []
    if receipt.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"{name}:schema_version_unexpected")
    if receipt.get("status") != "PASS":
        problems.append(f"{name}:status_not_pass")
    skill = _mapping(receipt.get("skill"))
    if not skill.get("digest"):
        problems.append(f"{name}:skill_digest_absent")
    reported = skill.get("functional_digest")
    if not reported:
        problems.append(f"{name}:functional_digest_absent")
    elif reported != digest:
        problems.append(f"{name}:functional_digest_mismatch")
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
    receipt: JSONObject,
    digest: str,
    now: datetime,
    max_age_days: int,
) -> MutableSequence[str]:
    """Validate the initial publication access receipt and live read-backs.

    :param name: Receipt file name prefixed to each reported problem.
    :param receipt: Parsed publication access receipt to inspect.
    :param digest: Expected functional digest of the candidate runtime and schemas.
    :param now: Reference time used for receipt freshness.
    :param max_age_days: Maximum accepted receipt age in whole days.
    :returns: Mutable problem list for missing identity, target, or live proof.
    :raises ImportError: If the shared command-authority catalogue cannot be loaded.
    :raises TypeError: If a nested readback has an incompatible type or now is naive
        while receipt timestamps are timezone-aware.
    :raises RecursionError: If caller-supplied cyclic data exceeds redaction nesting.
    """  # noqa: DOC502 - Shared validators and catalogue import propagate failures.
    from lib.python.core.catalog import DEFAULT_AUTHORITIES

    problems = _check_common(name, receipt, digest, now, max_age_days)
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
    for surface in DEFAULT_AUTHORITIES:
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
    if "required_checks_read" not in json_strings(
        github.get("observed_capability") or []
    ) or not _mapping(github.get("details")).get("required_checks"):
        problems.append(f"{name}:required_checks_readback_missing")
    return problems


def _gitlab_context(receipt: JSONObject) -> JSONObject:
    """Extract host, identity, credential, and target context from access proof.

    :param receipt: Initial GitLab access receipt with target and identity fields.
    :returns: Expected runtime context for matching later GitLab receipts.
    """
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


def _matches_context(receipt: JSONObject, context: JSONObject) -> bool:
    """Match an operation receipt to an exact access host and target.

    :param receipt: GitLab operation receipt carrying verified target details.
    :param context: Host, origin, and target identity from an access receipt.
    :returns: True when the operation's host, origin, and target all match.
    """
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
    receipt: JSONObject,
    required: JSONObject,
    digest: str,
    now: datetime,
    max_age_days: int,
) -> MutableSequence[str]:
    """Validate one GitLab receipt against selected access and operation proof.

    :param name: Receipt file name prefixed to each reported problem.
    :param receipt: Parsed GitLab access, read, or mutation receipt.
    :param required: Expected host, credential, target, kind, and operation fields.
    :param digest: Expected functional digest of the candidate runtime and schemas.
    :param now: Reference time used for receipt freshness.
    :param max_age_days: Maximum accepted receipt age in whole days.
    :returns: Mutable problem list for context or read-back mismatch.
    :raises KeyError: If the required context lacks its kind field.
    :raises TypeError: If nested evidence has an incompatible type or now is naive
        while receipt timestamps are timezone-aware.
    :raises RecursionError: If caller-supplied cyclic data exceeds redaction nesting.
    """  # noqa: DOC502 - Context indexing and shared validators propagate failures.
    problems = _check_common(name, receipt, digest, now, max_age_days)
    kind = required["kind"]
    if kind not in GITLAB_RECEIPT_KINDS or receipt.get("kind") != kind:
        problems.append(f"{name}:kind_unexpected")
    for field in ("execution_host", "origin"):
        if receipt.get(field) != required.get(field):
            problems.append(f"{name}:{field}_mismatch")
    identity = _mapping(receipt.get("identity"))
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
    target = _mapping(
        receipt.get("target")
        if kind == "gitlab_access"
        else receipt.get("verified_target")
    )
    if (
        target.get("kind") != required.get("target_kind")
        or target.get("id") != required.get("target_id")
        or target.get("full_path") != required.get("target_path")
    ):
        problems.append(f"{name}:target_mismatch")
    operation = required.get("operation") if kind != "gitlab_access" else None
    readback = receipt.get("readback")
    if kind == "gitlab_access":
        if not {"identity_read", "target_read"} <= set(
            json_strings(receipt.get("observed_capability") or [])
        ):
            problems.append(f"{name}:access_readback_missing")
    elif (kind, operation) in READ_ONLY_GITLAB_OPERATIONS:
        problems.extend(_check_gitlab_read_receipt(name, receipt, required))
    elif (
        receipt.get("phase") != "APPLY"
        or receipt.get("mutated") is not (required.get("result_action") == "APPLIED")
        or receipt.get("operation") != operation
        or receipt.get("result_action") != required.get("result_action")
        or not isinstance(readback, dict)
        or readback.get("verified") is not True
        or readback.get("action") != required.get("result_action")
        or receipt.get("errors")
    ):
        problems.append(f"{name}:operation_readback_missing")
    if (
        kind == "gitlab_runner"
        and required.get("operation") == "create"
        and required.get("result_action") == "APPLIED"
        and not _runner_smoke_cleanup_verified(receipt, required, now, max_age_days)
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
    name: str, receipt: JSONObject, required: JSONObject
) -> Sequence[str]:
    """Validate a live read receipt that must not mutate GitLab.

    :param name: Receipt file name used in diagnostics.
    :param receipt: Parsed receipt object.
    :param required: Expected operation and target entry.
    :returns: Acceptance problems for this read receipt.
    """
    problems: MutableSequence[str] = []
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


def _runner_delete_readback_verified(receipt: JSONObject, required: JSONObject) -> bool:
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
    receipt: JSONObject,
    required: JSONObject,
    now: datetime,
    max_age_days: int,
) -> bool:
    """Verify a disposable runner's deletion and independent absence read-back.

    :param receipt: Runner creation receipt containing smoke cleanup evidence.
    :param required: Expected project target identity from setup access.
    :param now: Reference time bounding cleanup receipt freshness.
    :param max_age_days: Maximum accepted cleanup receipt age in whole days.
    :returns: True only when the exact created runner is absent afterward.
    :raises TypeError: If now is naive while receipt timestamps are timezone-aware.
    """  # noqa: DOC502 - Datetime comparisons reject mixed timezone awareness.
    cleanup = receipt.get("smoke_cleanup")
    if not isinstance(cleanup, dict):
        return False
    captured = _parse_time(cleanup.get("captured_at"))
    created = _parse_time(receipt.get("captured_at"))
    runner_id = _mapping(receipt.get("readback")).get("id")
    before = cleanup.get("before")
    deletion = cleanup.get("delete")
    after = cleanup.get("after")
    revision = _mapping(_mapping(receipt.get("skill")).get("revision"))
    if (
        cleanup.get("schema_version") != SCHEMA_VERSION
        or cleanup.get("kind") != "gitlab_runner_smoke_cleanup"
        or cleanup.get("status") != "PASS"
        or cleanup.get("execution_host") != receipt.get("execution_host")
        or cleanup.get("origin") != receipt.get("origin")
        or cleanup.get("credential_source") != receipt.get("credential_source")
        or cleanup.get("skill_digest") != _mapping(receipt.get("skill")).get("digest")
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
        or (now - captured).days > max_age_days
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


def _redactor() -> Optional[Callable[[JSONObject], JSONObject]]:
    """Load the installed skill's receipt redactor for acceptance review.

    :returns: Shared object redaction function, or None when the skill library is absent.
    """
    try:
        from lib.python.core.runtime import redact_tree
    except ImportError:
        return None
    return redact_tree


def _resource_identity(receipt: JSONObject) -> Optional[tuple[str, JSONValue]]:
    """Select a stable resource identifier from an operation read-back.

    :param receipt: GitLab operation receipt with a readback object.
    :returns: First available id, iid, or slug pair, or None when absent.
    """
    readback = receipt.get("readback") or {}
    if not isinstance(readback, Mapping):
        return None
    for key in ("id", "iid", "slug"):
        if readback.get(key):
            return (key, readback[key])
    return None


class AcceptanceReview:
    """Evaluate setup, operation, and repeated-readback receipts in declared order."""

    def __init__(
        self,
        setup: Mapping[str, JSONObject],
        receipts: Mapping[str, JSONObject],
        skill_root: Path,
        now: datetime,
        max_age_days: int,
    ) -> None:
        """Retain receipt views and initialize candidate-bound evaluation state.

        :param setup: Existing access receipts captured before writes, keyed by name.
        :param receipts: Setup and operation receipt objects keyed by unique name.
        :param skill_root: Candidate skill tree whose current bytes are fingerprinted.
        :param now: Timezone-aware reference time for receipt freshness.
        :param max_age_days: Declared maximum receipt age in whole days.
        :raises ImportError: If shared provenance code cannot be loaded.
        :raises OSError: If a candidate skill source file cannot be read.
        :raises ValueError: If the candidate source tree cannot be fingerprinted.
        """  # noqa: DOC502 - The provenance import and tree_digest propagate failures.
        from lib.python.core.provenance import functional_digest, tree_digest

        self.setup = setup
        self.receipts = receipts
        self.skill_root = skill_root
        self.now = now
        self.max_age_days = max_age_days
        self.digest = tree_digest(skill_root)["digest"]
        self.functional_digest = functional_digest(skill_root)
        self.problems: MutableSequence[str] = []
        self.accepted: MutableSequence[str] = []
        self.contexts: MutableSequence[JSONObject] = []
        self.publication_hosts: set[str] = set()
        self.observed_operations: set[OperationKey] = set()
        self.matched_operations: MutableMapping[
            ReceiptPairKey, MutableMapping[str, tuple[str, JSONObject]]
        ] = {}

    def setup_receipts(self) -> None:
        """Require publication proof and retain each accepted GitLab access context.

        :raises ImportError: If the shared command-authority catalogue cannot be loaded.
        :raises RecursionError: If receipt containers exceed recursive redaction limits.
        """  # noqa: DOC502 - Receipt helpers propagate import and recursion failures.
        for name, receipt in self.setup.items():
            kind = receipt.get("kind")
            try:
                if kind == RECEIPT_KIND:
                    found = _check_receipt(
                        name,
                        receipt,
                        self.functional_digest,
                        self.now,
                        self.max_age_days,
                    )
                    host = receipt.get("execution_host")
                    if not isinstance(host, str):
                        found.append(f"{name}:receipt_invalid_shape")
                    elif host in self.publication_hosts:
                        found.append(f"{name}:receipt_ambiguous")
                    if isinstance(host, str):
                        self.publication_hosts.add(host)
                elif kind == "gitlab_access":
                    context = _gitlab_context(receipt)
                    found = _check_gitlab_receipt(
                        name,
                        receipt,
                        {**context, "kind": kind},
                        self.functional_digest,
                        self.now,
                        self.max_age_days,
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
                found.extend(
                    f"{name}:{reason}" for reason in _check_source_revision(receipt)
                )
            except (AttributeError, KeyError, TypeError, ValueError):
                found = [f"{name}:receipt_invalid_shape"]
            self.problems.extend(found)
            if not found:
                self.accepted.append(name)
                if kind == "gitlab_access":
                    self.contexts.append(context)
                    self.observed_operations.add((kind, None, None))
        if not any(
            self.setup[name].get("kind") == RECEIPT_KIND for name in self.accepted
        ):
            self.problems.append("access_check:setup_missing")

    def operation_receipts(self) -> None:
        """Match each operation to one setup context and retain repeatable pairs.

        Malformed fields contribute classified problems; accepted receipts retain
        their original bytes and must still match the candidate identity.
        """
        for name, receipt in self.receipts.items():
            if name in self.setup:
                if receipt != self.setup[name]:
                    self.problems.append(f"{name}:setup_receipt_mismatch")
                continue
            kind = receipt.get("kind")
            operation = receipt.get("operation")
            result_action = receipt.get("result_action")
            if (
                not isinstance(kind, str)
                or not (operation is None or isinstance(operation, str))
                or not (result_action is None or isinstance(result_action, str))
            ):
                self.problems.append(f"{name}:receipt_not_declared")
                continue
            operation_key = (kind, operation, result_action)
            if operation_key not in REQUIRED_GITLAB_OPERATIONS:
                self.problems.append(f"{name}:receipt_not_declared")
                continue
            candidates = [
                context
                for context in self.contexts
                if _matches_context(receipt, context)
            ]
            if len(candidates) != 1:
                self.problems.append(f"{name}:setup_context_missing_or_ambiguous")
                continue
            required = {
                **candidates[0],
                "kind": kind,
                "operation": operation,
                "result_action": result_action,
            }
            try:
                found = _check_gitlab_receipt(
                    name,
                    receipt,
                    required,
                    self.functional_digest,
                    self.now,
                    self.max_age_days,
                )
                found.extend(
                    f"{name}:{reason}" for reason in _check_source_revision(receipt)
                )
            except (AttributeError, KeyError, TypeError, ValueError):
                found = [f"{name}:receipt_invalid_shape"]
            self.problems.extend(found)
            if not found:
                if (kind, operation) in REPEATABLE_GITLAB_OPERATIONS:
                    try:
                        pair = (
                            json_string(required["execution_host"]),
                            json_string(required["origin"]),
                            kind,
                            json_string(operation),
                            json_string(required["target_kind"]),
                            json_integer(required["target_id"]),
                            json_string(required["target_path"]),
                        )
                    except (KeyError, TypeError):
                        self.problems.append(f"{name}:receipt_invalid_shape")
                        continue
                    actions = self.matched_operations.setdefault(pair, {})
                    if result_action in actions:
                        self.problems.append(f"{name}:receipt_ambiguous")
                    actions[json_string(result_action)] = (name, receipt)
                self.accepted.append(name)
                self.observed_operations.add(operation_key)

    def repeated_receipts(self) -> None:
        """Require operation coverage and matching APPLIED/NO_OP resource evidence."""
        for kind, operation, action in sorted(
            REQUIRED_GITLAB_OPERATIONS - self.observed_operations,
            key=lambda entry: tuple(str(value) for value in entry),
        ):
            self.problems.append(
                f"{kind}:{operation or 'check'}:{action or 'check'}:receipt_missing"
            )
        for pair, actions in self.matched_operations.items():
            if set(actions) != {"APPLIED", "NO_OP"}:
                self.problems.append(f"{pair[2]}:{pair[3]}:repeat_readback_missing")
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
                self.problems.append(
                    f"{applied_name}:{noop_name}:repeat_readback_mismatch"
                )

    def result(self) -> AcceptanceResult:
        """Serialize the accumulated acceptance outcome without changing receipts.

        :returns: Existing live-acceptance envelope with sorted names and problems.
        """
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "live_acceptance",
            "status": PASS if not self.problems else BLOCKED,
            "skill_digest": self.digest,
            "functional_digest": self.functional_digest,
            "accepted": sorted(self.accepted),
            "problems": sorted(self.problems),
        }


def evaluate(
    setup: Mapping[str, JSONObject],
    receipts: Mapping[str, JSONObject],
    skill_root: Path,
    now: Optional[datetime] = None,
    *,
    max_age_days: int,
) -> AcceptanceResult:
    """Compare action receipts with the initial authenticated runtime setup.

    :param setup: Existing access receipts keyed by name, captured before writes.
    :param receipts: Parsed operation and setup receipts keyed by file name.
    :param skill_root: Candidate skill tree used for digest comparison.
    :param now: Reference time for freshness, or None to read the current UTC time.
    :param max_age_days: Declared maximum receipt age from the acceptance policy.
    :returns: Acceptance status, accepted receipts, and stable problem reasons.
    :raises ImportError: If shared provenance or authority modules cannot be loaded.
    :raises OSError: If a candidate source file cannot be read for its digest.
    :raises ValueError: If the candidate skill tree cannot be fingerprinted.
    :raises RecursionError: If receipt containers exceed recursive redaction limits.
    """  # noqa: DOC502 - Review construction and receipt helpers propagate failures.
    review = AcceptanceReview(
        setup,
        receipts,
        skill_root,
        now if now is not None else datetime.now(UTC),
        max_age_days,
    )
    review.setup_receipts()
    review.operation_receipts()
    review.repeated_receipts()
    return review.result()


def main() -> int:
    """Evaluate committed live receipts and render the selected output format.

    :returns: Zero for accepted evidence or a dry run, or two for blocked acceptance.
    :raises SystemExit: If arguments are invalid or help is requested.
    :raises ImportError: If required shared modules or requested YAML support are absent.
    :raises OSError: If final output cannot be written.
    :raises UnicodeError: If final output cannot be encoded.
    :raises RecursionError: If receipt containers exceed recursive redaction limits.
    """  # noqa: DOC502 - Argument parsing, imports, and output helpers propagate failures.
    cli = argparse.ArgumentParser(
        description="Verify committed live-access receipts against this revision.",
        epilog="Example: check_live_acceptance.py --root . --json",
    )
    cli.add_argument("--root", required=True, metavar="PATH", help="repository root")
    cli.add_argument(
        "--policy",
        metavar="PATH",
        help=f"acceptance policy file (default: <root>/{POLICY_FILE.as_posix()})",
    )
    cli.add_argument(
        "--receipts",
        metavar="PATH",
        help="one receipt directory (default: the directories the policy declares)",
    )
    cli.add_argument(
        "--skill",
        metavar="PATH",
        help="skill root (default: <root>/ci-skills)",
    )
    cli.add_argument(
        "--dry-run",
        action="store_true",
        help="list the policy and the receipts that would be evaluated, then exit",
    )
    modes = cli.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", help="print JSON")
    modes.add_argument("--yaml", action="store_true", help="print YAML")
    add_log_arguments(cli)
    args = cli.parse_args()

    root = Path(args.root).resolve()
    skill_path = Path(args.skill) if args.skill else root / "ci-skills"
    _redactor()
    try:
        policy = load_policy(
            Path(args.policy) if args.policy else root / POLICY_FILE,
            root / POLICY_SCHEMA_FILE,
        )
        receipts_paths = (
            [Path(args.receipts)]
            if args.receipts
            else [root / directory for directory in policy["receipt_directories"]]
        )
        receipts = _load_receipt_dirs(receipts_paths)
        setup = {
            name: receipt
            for name, receipt in receipts.items()
            if receipt.get("kind") in SETUP_RECEIPT_KINDS
        }
        if args.dry_run:
            data = {
                "schema_version": SCHEMA_VERSION,
                "kind": "live_acceptance",
                "status": DRY_RUN,
                "policy": dict(policy),
                "receipts": sorted(receipts),
                "accepted": [],
                "problems": [],
            }
        else:
            data = evaluate(
                setup,
                receipts,
                skill_path,
                max_age_days=policy["max_receipt_age_days"],
            )
    except (AcceptanceError, OSError, ValueError, TypeError) as exc:
        data = {
            "schema_version": SCHEMA_VERSION,
            "kind": "live_acceptance",
            "status": BLOCKED,
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
        if data["status"] == DRY_RUN:
            policy = data["policy"]
            print(f"  window    {policy['max_receipt_age_days']} days")
            for directory in policy["receipt_directories"]:
                print(f"  directory {directory}")
            for name in data["receipts"]:
                print(f"  receipt   {name}")
        for name in data["accepted"]:
            print(f"  accepted {name}")
        for problem in data["problems"]:
            print(f"  problem  {problem}")
    status = json_string(data["status"])
    log_event(args, "live_acceptance", "result", status, persist=not args.dry_run)
    return exit_code(status)


if __name__ == "__main__":
    raise SystemExit(main())
