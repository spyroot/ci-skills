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
import subprocess
import tarfile
from collections.abc import Callable, Mapping, MutableMapping, MutableSequence, Sequence
from datetime import UTC, datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, Optional, TypeAlias, TypedDict

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

SKILL_ROOT: Final = Path(__file__).resolve().parents[1] / "ci-skills"
runpy.run_path(str(SKILL_ROOT / "bin" / "_bootstrap.py"))

from lib.python.core.cli_api import response_decoder
from lib.python.core.gitlab.mirror.constants import KIND as MIRROR_KIND
from lib.python.core.gitlab.mirror.constants import OPERATION as MIRROR_OPERATION
from lib.python.core.gitlab.mirror.evidence import (
    check_mirror_pair,
    check_mirror_receipt,
)
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    json_integer,
    json_string,
    json_strings,
)
from lib.python.core.kubernetes.artifact_storage_runtime import normalized_digest
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.podman_evidence import (
    MARKER_PREFIX,
    parse_probe_markers,
)
from lib.python.core.logging import add_log_arguments, log_event
from lib.python.core.schema import SpecificationError, validate_specification
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
    :param mode: Aggregate or targeted coverage selected for this result.
    :param tested_revision: Single installed skill revision shared by the receipts.
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

    .. attribute :: mode
        :type: str

        Aggregate or targeted coverage selected for this result.

    .. attribute :: tested_revision
        :type: Optional[str]

        Single installed skill revision shared by the receipts, or None if unavailable.

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
    mode: str
    tested_revision: Optional[str]
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
        "gitlab_repository",
        "gitlab_merge_request",
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
GATE_SURFACES: Final = ("gitlab", "kubernetes")
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
IMAGE_BUILD_READINESS_KEY: Final[OperationKey] = (
    "image_build_readiness",
    "probe",
    None,
)
MIRROR_OPERATIONS: Final[frozenset[OperationKey]] = frozenset(
    (MIRROR_KIND, MIRROR_OPERATION, action) for action in ("APPLIED", "NO_OP")
)
REQUIRED_CAPABILITY_OPERATIONS: Final = frozenset(
    {
        IMAGE_BUILD_READINESS_KEY,
        *MIRROR_OPERATIONS,
        ("image_build_readiness", "chain", None),
        ("gitlab_repository", "commit", "APPLIED"),
        ("gitlab_merge_request", "create", "APPLIED"),
        ("gitlab_merge_request", "update", "APPLIED"),
    }
)
SUPPORTED_REQUIRED_OPERATIONS: Final[frozenset[OperationKey]] = (
    REQUIRED_GITLAB_OPERATIONS
    | REQUIRED_CAPABILITY_OPERATIONS
    | {(RECEIPT_KIND, None, None)}
)
IMAGE_BUILD_READINESS_SCHEMA: Final = "results/image-build-readiness.schema.json"


def _operation_key(receipt: JSONObject) -> Optional[OperationKey]:
    """Normalize the existing envelope and mirror record action conventions.

    :param receipt: Selected receipt whose shape will be independently validated.
    :returns: Comparable operation key, or None for malformed key fields.
    """
    kind = receipt.get("kind")
    operation = receipt.get("operation")
    action = receipt.get("result_action")
    if kind == MIRROR_KIND:
        records = receipt.get("records")
        if (
            not isinstance(records, list)
            or len(records) != 1
            or not isinstance(records[0], dict)
        ):
            return None
        action = records[0].get("action")
    if (
        not isinstance(kind, str)
        or not (operation is None or isinstance(operation, str))
        or not (action is None or isinstance(action, str))
    ):
        return None
    return kind, operation, action


class AcceptanceError(RuntimeError):
    """Acceptance inputs could not be read."""


class AcceptancePolicy(TypedDict):
    """Declared receipt locations and freshness window for live acceptance.

    :param max_receipt_age_days: Whole days after which a receipt is stale.
    :param receipt_directories: Repository-relative directories holding receipts.
    :param required_operations: Validated operation keys required in every mode.
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

    .. attribute :: required_operations
        :type: Sequence[OperationKey]

        Validated operation keys required in both targeted and aggregate mode.

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
    required_operations: Sequence[OperationKey]
    historical_directory: str
    scheduled_workflow: str
    scheduled_cron: str
    rerun_procedure: str


def load_policy(path: Path, schema_path: Path) -> AcceptancePolicy:
    """Read the tracked live-acceptance policy and validate it against its schema.

    The JSON Schema owns every field rule; this loader adds only the relation the
    schema cannot express: historical receipts are excluded and required operations
    must have an implemented validator.

    :param path: Policy file selected by the caller or the repository default.
    :param schema_path: Schema file that defines the policy document.
    :returns: Validated freshness window, receipt directories, and schedule.
    :raises AcceptanceError: If the policy or schema is missing or unreadable (each
        failure names its own file), the schema is invalid, the policy violates the
        schema, the historical directory is also a receipt directory, or a required
        operation has no implemented validator.
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
    required_operations: tuple[OperationKey, ...] = tuple(
        (
            str(entry["kind"]),
            str(entry["operation"]) if entry["operation"] is not None else None,
            str(entry["action"]) if entry["action"] is not None else None,
        )
        for entry in document["requiredOperations"]
    )
    if any(key not in SUPPORTED_REQUIRED_OPERATIONS for key in required_operations):
        raise AcceptanceError("policy_invalid:requiredOperations")
    scheduled = document["scheduledCheck"]
    return {
        "max_receipt_age_days": int(document["maxReceiptAgeDays"]),
        "receipt_directories": receipt_directories,
        "required_operations": required_operations,
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


@lru_cache(maxsize=32)
def _source_functional_digest(skill_root: Path, revision: str) -> Optional[str]:
    """Measure a retained source snapshot without altering the checkout.

    :param skill_root: Current repository's installable skill directory.
    :param revision: Verified forty-character source commit from the receipt.
    :returns: Original functional digest, or None when source is unavailable.
    """
    from lib.python.core.provenance import functional_digest

    try:
        archive = subprocess.run(
            [
                "git",
                "-C",
                str(skill_root.parent),
                "archive",
                "--format=tar",
                f"{revision}:{skill_root.name}",
            ],
            capture_output=True,
            check=False,
            timeout=30,
        )
        if archive.returncode:
            return None
        with TemporaryDirectory(prefix="ci-skills-receipt-source-") as directory:
            with tarfile.open(fileobj=BytesIO(archive.stdout)) as source:
                source.extractall(directory, filter="data")
            return functional_digest(Path(directory))
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError):
        return None


def check_recorded_identity(receipt: JSONObject, skill_root: Path) -> Sequence[str]:
    """Verify historical receipt bytes against their captured source revision.

    Equal candidate bytes remain sufficient when a squash source is unavailable.
    This verifies historical identity, not execution on a changed candidate.

    :param receipt: Captured receipt whose identity must remain unchanged.
    :param skill_root: Candidate repository's skill directory.
    :returns: Source-provenance or original functional-checksum failures.
    """
    problems = _check_source_revision(receipt)
    if problems:
        return problems
    if not check_candidate_identity(receipt, skill_root):
        return []
    skill = _mapping(receipt.get("skill"))
    revision = _mapping(skill.get("revision"))["value"]
    original = _source_functional_digest(skill_root, json_string(revision))
    if original is None:
        return ["recorded_source_unavailable"]
    if skill.get("functional_digest") != original:
        return ["recorded_functional_digest_mismatch"]
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


def _callback_record(
    records: Sequence[JSONValue], callback: str
) -> Optional[JSONObject]:
    """Select one exact callback result from a workflow report.

    :param records: Schema-validated callback result objects.
    :param callback: Concrete callback class name to select.
    :returns: The sole matching result, or None when absent or ambiguous.
    """
    matching = [
        item
        for item in records
        if isinstance(item, dict) and item.get("callback") == callback
    ]
    return matching[0] if len(matching) == 1 else None


def _probe_runtime_verified(probe: Optional[JSONObject]) -> bool:
    """Check the authoritative Pod termination retained by the probe callback.

    :param probe: Sole RunPodmanProbe result, or None when missing or ambiguous.
    :returns: Whether the callback proves a successful nested Podman execution.
    """
    if probe is None:
        return False
    pod = _mapping(probe.get("pod"))
    return (
        probe.get("verified") is True
        and probe.get("action") == "APPLIED"
        and probe.get("claim") == "nested_podman_runtime_readiness"
        and probe.get("outcome") == "ready"
        and probe.get("nested_runtime_ready") is True
        and pod.get("exit_code") == 0
        and bool(pod.get("image_id"))
    )


def _probe_markers_verified(probe: Optional[JSONObject]) -> bool:
    """Re-evaluate reported marker rows through the production marker parser.

    :param probe: Sole RunPodmanProbe result, or None when missing or ambiguous.
    :returns: Whether every row exactly matches the production PASS evidence.
    """
    rows = probe.get("rows") if probe is not None else None
    row_list = rows if isinstance(rows, list) else []
    try:
        marker_log = "\n".join(
            f"{MARKER_PREFIX}|{json_string(row['check'])}|"
            f"{json_string(row['observed'])}"
            for row in row_list
            if isinstance(row, Mapping)
        )
        marker_evidence = parse_probe_markers(marker_log)
    except (KeyError, TypeError, ValueError):
        return False
    return marker_evidence.verified is True and list(marker_evidence.rows) == row_list


def _object_identity(value: JSONValue) -> Optional[tuple[str, str, str]]:
    """Read one exact Kubernetes kind, name, and namespace.

    :param value: Schema-validated Kubernetes resource evidence.
    :returns: Exact resource identity, or None when incomplete.
    """
    if not isinstance(value, Mapping):
        return None
    kind, name, namespace = (
        value.get("kind"),
        value.get("name"),
        value.get("namespace"),
    )
    if (
        not isinstance(kind, str)
        or not kind
        or not isinstance(name, str)
        or not name
        or not isinstance(namespace, str)
    ):
        return None
    return kind, name, namespace


def _cleanup_identity(value: JSONValue) -> Optional[tuple[str, str, str]]:
    """Require one absent PASS resource and return its exact identity.

    :param value: Schema-validated cleanup resource object.
    :returns: Exact resource identity, or None when not proven absent.
    """
    identity = _object_identity(value)
    if (
        not isinstance(value, Mapping)
        or identity is None
        or value.get("status") != PASS
        or value.get("absent") is not True
    ):
        return None
    return identity


def _expected_probe_cleanup(
    records: Sequence[JSONValue],
) -> Optional[tuple[set[tuple[str, str, str]], str]]:
    """Derive cleanup identities only from resources callbacks say they own.

    :param records: Schema-validated callback results from the installed command.
    :returns: Expected exact identities and run namespace, or None if ambiguous.
    """
    scope = _callback_record(records, "OpenProbeNamespace")
    access = _callback_record(records, "EnsureSCCAccess")
    probe = _callback_record(records, "RunPodmanProbe")
    if scope is None or access is None or probe is None:
        return None
    namespace = scope.get("namespace")
    job = _mapping(probe.get("job"))
    if (
        not isinstance(namespace, str)
        or not namespace
        or probe.get("namespace") != namespace
        or not isinstance(job.get("name"), str)
        or not job.get("name")
    ):
        return None
    expected = {("Job", str(job["name"]), namespace)}
    owns_namespace = scope.get("owns_namespace")
    if owns_namespace is True:
        expected.add(("Namespace", namespace, ""))
    elif owns_namespace is not False:
        return None
    permission_source = access.get("permission_source")
    if permission_source == "temporary_role_binding":
        binding_record = _mapping(access.get("role_binding"))
        binding = _object_identity(binding_record)
        if (
            binding is None
            or binding[0] != "RoleBinding"
            or binding[2] != namespace
            or binding_record.get("action") != "APPLIED"
        ):
            return None
        expected.add(binding)
    elif permission_source not in {"existing", "not_applicable"}:
        return None
    return expected, namespace


def _probe_cleanup_verified(records: Sequence[JSONValue], cleanup: JSONObject) -> bool:
    """Match every owned probe resource to a successful absence read-back.

    :param records: Schema-validated callback results declaring resource ownership.
    :param cleanup: Final cleanup report emitted after reverse callback cleanup.
    :returns: Whether cleanup proves exactly the resources owned by this run absent.
    """
    resources = cleanup.get("resources")
    resource_list = resources if isinstance(resources, list) else []
    expected = _expected_probe_cleanup(records)
    identities = [_cleanup_identity(item) for item in resource_list]
    if (
        cleanup.get("status") != PASS
        or cleanup.get("failures") != []
        or expected is None
        or not resource_list
        or any(identity is None for identity in identities)
    ):
        return False
    expected_identities, namespace = expected
    observed = {identity for identity in identities if identity is not None}
    configmaps = {
        identity
        for identity in observed
        if identity[0] == "ConfigMap" and identity[2] == namespace
    }
    return (
        len(observed) == len(resource_list)
        and len(configmaps) == 1
        and observed == expected_identities | configmaps
    )


def _check_image_build_readiness_receipt(
    name: str,
    receipt: JSONObject,
    digest: str,
    now: datetime,
    max_age_days: int,
) -> MutableSequence[str]:
    """Validate one installed Podman readiness result and its API read-backs.

    :param name: Receipt file name prefixed to each reported problem.
    :param receipt: Parsed image-build readiness result from the installed command.
    :param digest: Expected functional digest of the candidate runtime and schemas.
    :param now: Reference time used for receipt freshness.
    :param max_age_days: Maximum accepted receipt age in whole days.
    :returns: Problems proving a schema, runtime, read-back, or cleanup failure.
    :raises RecursionError: If receipt containers exceed recursive validation limits.
    """  # noqa: DOC502 - Shared validators propagate recursion failures.
    problems = _check_common(name, receipt, digest, now, max_age_days)
    try:
        validate_specification(receipt, IMAGE_BUILD_READINESS_SCHEMA)
    except (KeyError, OSError, SchemaError, SpecificationError, TypeError, ValueError):
        problems.append(f"{name}:result_schema_invalid")
        return problems

    problems.extend(f"{name}:{reason}" for reason in _check_source_revision(receipt))
    records = receipt.get("records")
    record_list = records if isinstance(records, list) else []
    summary = _mapping(receipt.get("summary"))
    if (
        receipt.get("operation") != "probe"
        or receipt.get("errors") != []
        or summary.get("error_count") != 0
        or summary.get("record_count") != len(record_list)
    ):
        problems.append(f"{name}:execution_readback_unproven")
    probe = _callback_record(record_list, "RunPodmanProbe")
    if not _probe_runtime_verified(probe):
        problems.append(f"{name}:runtime_readback_unproven")
    if not _probe_markers_verified(probe):
        problems.append(f"{name}:runtime_checks_incomplete")
    if not _probe_cleanup_verified(record_list, _mapping(receipt.get("cleanup"))):
        problems.append(f"{name}:cleanup_unproven")
    return problems


def _check_receipt(
    name: str,
    receipt: JSONObject,
    digest: str,
    now: datetime,
    max_age_days: int,
) -> MutableSequence[str]:
    """Validate the initial full live access receipt and its live read-backs.

    The gate binds GitLab and Kubernetes proof to the candidate runtime. It does
    not read this repository's branch protection: the GitHub surface of the
    receipt is tolerated but never required, because repository merge policy
    lives in GitHub and the workflow, not in the skill's target file.

    :param name: Receipt file name prefixed to each reported problem.
    :param receipt: Parsed full live access receipt to inspect.
    :param digest: Expected functional digest of the candidate runtime and schemas.
    :param now: Reference time used for receipt freshness.
    :param max_age_days: Maximum accepted receipt age in whole days.
    :returns: Mutable problem list for missing identity, target, or live proof.
    :raises TypeError: If a nested readback has an incompatible type or now is naive
        while receipt timestamps are timezone-aware.
    :raises RecursionError: If caller-supplied cyclic data exceeds redaction nesting.
    """  # noqa: DOC502 - Shared validators propagate failures.
    problems = _check_common(name, receipt, digest, now, max_age_days)
    if receipt.get("kind") != RECEIPT_KIND:
        problems.append(f"{name}:kind_unexpected")
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
    for surface in GATE_SURFACES:
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
        self.access_hosts: set[str] = set()
        self.observed_operations: set[OperationKey] = set()
        self.matched_operations: MutableMapping[
            ReceiptPairKey, MutableMapping[str, tuple[str, JSONObject]]
        ] = {}

    def setup_receipts(self) -> None:
        """Require the full live access receipt and retain each GitLab access context.

        :raises RecursionError: If receipt containers exceed recursive redaction limits.
        """  # noqa: DOC502 - Receipt helpers propagate recursion failures.
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
                    elif host in self.access_hosts:
                        found.append(f"{name}:receipt_ambiguous")
                    if isinstance(host, str):
                        self.access_hosts.add(host)
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
        """Validate capability proof or match an operation to one setup context.

        Malformed fields contribute classified problems; accepted receipts retain
        their original bytes and must still match the candidate identity.
        """
        for name, receipt in self.receipts.items():
            if name in self.setup:
                if receipt != self.setup[name]:
                    self.problems.append(f"{name}:setup_receipt_mismatch")
                continue
            operation_key = _operation_key(receipt)
            if operation_key is None:
                self.problems.append(f"{name}:receipt_not_declared")
                continue
            kind, operation, result_action = operation_key
            if (
                kind in {"image_build_readiness", MIRROR_KIND}
                and operation_key in REQUIRED_CAPABILITY_OPERATIONS
            ):
                try:
                    found = _check_targeted_receipt(name, receipt, self)
                except (AttributeError, KeyError, TypeError, ValueError):
                    found = [f"{name}:receipt_invalid_shape"]
                self.problems.extend(found)
                if not found:
                    if operation_key in self.observed_operations:
                        self.problems.append(f"{name}:receipt_ambiguous")
                        continue
                    self.accepted.append(name)
                    self.observed_operations.add(operation_key)
                continue
            if operation_key not in REQUIRED_GITLAB_OPERATIONS and kind not in {
                "gitlab_repository",
                "gitlab_merge_request",
            }:
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
                found = _check_targeted_receipt(name, receipt, self, required=required)
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
            (REQUIRED_GITLAB_OPERATIONS | REQUIRED_CAPABILITY_OPERATIONS)
            - self.observed_operations,
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

    def mirror_receipts(self) -> None:
        """Require an ordered, compatible applied/no-op pair in either mode.

        Only receipts that passed their individual validators can satisfy this
        transition. Confirmation digests may differ as observed state changes.
        """
        selected = [
            self.receipts[name]
            for name in self.accepted
            if name in self.receipts and self.receipts[name].get("kind") == MIRROR_KIND
        ]
        if not selected:
            return
        prefix = f"{MIRROR_KIND}:{MIRROR_OPERATION}:"
        actions = {
            action: [
                receipt
                for receipt in selected
                if _operation_key(receipt) == (MIRROR_KIND, MIRROR_OPERATION, action)
            ]
            for action in ("APPLIED", "NO_OP")
        }
        if len(selected) != 2 or any(len(items) != 1 for items in actions.values()):
            self.problems.append(prefix + "repeat_readback_missing")
            return
        applied, noop = actions["APPLIED"][0], actions["NO_OP"][0]
        record = _mapping(applied["records"][0])
        if _mapping(record.get("created")).get("repository") is not True:
            self.problems.append(prefix + "create_if_absent_unproven")
        applied_time = _parse_time(applied.get("captured_at"))
        noop_time = _parse_time(noop.get("captured_at"))
        if applied_time is None or noop_time is None or applied_time >= noop_time:
            self.problems.append(prefix + "repeat_readback_order_invalid")
        try:
            self.problems.extend(
                prefix + reason for reason in check_mirror_pair(applied, noop)
            )
        except (
            KeyError,
            OSError,
            SchemaError,
            SpecificationError,
            TypeError,
            ValueError,
        ):
            self.problems.append(prefix + "mirror_pair_invalid_shape")

    def required_coverage(self, required_operations: Sequence[OperationKey]) -> None:
        """Require policy coverage from receipts that passed their full validators.

        :param required_operations: Nonempty validated operation keys from policy.
        """
        if not required_operations or any(
            key not in SUPPORTED_REQUIRED_OPERATIONS for key in required_operations
        ):
            self.problems.append("policy_invalid:requiredOperations")
            return
        accepted_operations: set[OperationKey] = set()
        for name in self.accepted:
            receipt = self.receipts.get(name, self.setup.get(name, {}))
            key = _operation_key(receipt)
            if key is not None:
                accepted_operations.add(key)
        for kind, operation, action in sorted(
            set(required_operations) - accepted_operations,
            key=lambda entry: tuple(str(value) for value in entry),
        ):
            self.problems.append(
                f"coverage_missing:{kind}:{operation or 'check'}:{action or 'check'}"
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


def _check_chain_runner_readback(
    name: str, receipt: JSONObject, review: AcceptanceReview
) -> Sequence[str]:
    """Match delivered runner identity to an independent installed-command read.

    :param name: Chain receipt filename used in diagnostics.
    :param receipt: Delivered toolbox and runner chain result.
    :param review: Selected receipts and candidate validation settings.
    :returns: A missing or mismatched read-back diagnostic, or no problems.
    """
    reads = [
        (read_name, item)
        for read_name, item in review.receipts.items()
        if item.get("kind") == "gitlab_runner" and item.get("operation") == "get"
    ]
    if not reads:
        return [f"{name}:runner_get_receipt_missing"]
    access = _mapping(_mapping(receipt.get("access")).get("gitlab_runner"))
    context = _gitlab_context(access)
    records = receipt.get("records")
    created = _callback_record(
        records if isinstance(records, list) else [], "CreateRunner"
    )
    runner_id = created.get("id") if created else None
    if (
        type(runner_id) is not int
        or runner_id <= 0
        or any(not value for value in context.values())
        or _check_source_revision(access)
        or _mapping(_mapping(access.get("skill")).get("revision")).get("value")
        != receipt.get("tested_revision")
    ):
        return [f"{name}:runner_get_readback_mismatch"]
    matching = 0
    for read_name, item in reads:
        found = _check_gitlab_receipt(
            read_name,
            item,
            {**context, "kind": "gitlab_runner", "operation": "get"},
            review.functional_digest,
            review.now,
            review.max_age_days,
        )
        found.extend(_check_source_revision(item))
        read_records = item.get("records")
        record = (
            _mapping(read_records[0])
            if isinstance(read_records, list) and len(read_records) == 1
            else {}
        )
        if (
            not found
            and record.get("id") == runner_id
            and _mapping(item.get("plan")).get("resource_id") == runner_id
            and record.get("online") is True
            and record.get("status") == "online"
            and item.get("tested_revision") == receipt.get("tested_revision")
        ):
            matching += 1
    return [] if matching == 1 else [f"{name}:runner_get_readback_mismatch"]


def _check_ocp_verify_readback(name: str, receipt: JSONObject) -> Sequence[str]:
    """Bind native ISO verification to its resolved record and reader cleanup.

    :param name: Receipt filename prefixed to each reported problem.
    :param receipt: Schema-validated installed node-image verification report.
    :returns: Execution, artifact, embedded-output, or cleanup evidence failures.
    """
    records = receipt.get("records")
    records = records if isinstance(records, list) else []
    forward = [
        item
        for item in records
        if isinstance(item, Mapping) and item.get("hook") != "cleanup"
    ]
    cleanup_records = [
        item
        for item in records
        if isinstance(item, Mapping) and item.get("hook") == "cleanup"
    ]
    resolved = _mapping(_callback_record(forward, "ResolveNodeArtifactRecord"))
    verified = _mapping(_callback_record(forward, "VerifyArtifactReference"))
    staged = _mapping(_callback_record(forward, "StageToolPod"))
    readback = _mapping(receipt.get("readback"))
    record = _mapping(resolved.get("record"))
    expected = _mapping(record.get("artifact"))
    observed = _mapping(verified.get("artifact"))
    inspection = _mapping(verified.get("inspection"))
    embedded = _mapping(inspection.get("embedded_ignition"))
    summary = _mapping(receipt.get("summary"))
    problems: MutableSequence[str] = []
    if (
        receipt.get("errors") != []
        or summary.get("error_count") != 0
        or summary.get("record_count") != len(records)
        or any(row.get("verified") is not True for row in (resolved, verified, staged))
        or resolved.get("present") is not True
    ):
        problems.append(f"{name}:execution_readback_unproven")
    try:
        content_matches = (
            normalized_digest(json_string(observed["sha256"]))
            == normalized_digest(json_string(expected["sha256"]))
            and observed.get("size") == expected.get("size")
            and observed.get("path") == expected.get("path")
        )
    except (KeyError, TypeError, KubernetesError):
        content_matches = False
    if (
        not content_matches
        or record != readback.get("artifact_record")
        or resolved.get("run_id") != record.get("run_id")
        or verified.get("run_id") != record.get("run_id")
        or receipt.get("run_id") != record.get("run_id")
        or verified.get("lifetime") != record.get("lifetime")
        or receipt.get("artifact_lifetime") != record.get("lifetime")
        or receipt.get("artifact") != expected
        or observed != readback.get("artifact_live_readback")
        or observed != inspection.get("artifact")
        or observed.get("pod") != staged.get("pod")
    ):
        problems.append(f"{name}:artifact_readback_unproven")
    if not embedded or embedded != readback.get("embedded_ignition"):
        problems.append(f"{name}:embedded_readback_unproven")
    cleanup = _mapping(receipt.get("cleanup"))
    resources = cleanup.get("resources")
    resources = resources if isinstance(resources, list) else []
    stage_cleanup = _mapping(_callback_record(cleanup_records, "StageToolPod"))
    removed = stage_cleanup.get("resources")
    removed = removed if isinstance(removed, list) else []
    pod = _mapping(staged.get("pod"))
    identity = _object_identity({**pod, "kind": "Pod"})
    if (
        cleanup.get("status") != PASS
        or cleanup.get("failures") != []
        or (
            record.get("lifetime") == "caller_selected_volume"
            and (
                staged.get("reused") is not False
                or identity is None
                or identity not in {_cleanup_identity(item) for item in resources}
                or identity not in {_cleanup_identity(item) for item in removed}
            )
        )
    ):
        problems.append(f"{name}:cleanup_unproven")
    return problems


def _check_targeted_receipt(
    name: str,
    receipt: JSONObject,
    review: AcceptanceReview,
    *,
    required: Optional[JSONObject] = None,
) -> Sequence[str]:
    """Validate selected candidate proof without demanding unrelated operations.

    :param name: Caller-selected receipt filename.
    :param receipt: Existing installed-command result envelope.
    :param review: Candidate identity and existing receipt freshness settings.
    :param required: Aggregate setup context, or None for a selected receipt.
    :returns: Provenance, schema, execution, read-back or cleanup failures.
    """
    kind = receipt.get("kind")
    operation = receipt.get("operation")
    arguments = (
        name,
        receipt,
        review.functional_digest,
        review.now,
        review.max_age_days,
    )
    if kind == RECEIPT_KIND:
        return [*_check_receipt(*arguments), *_check_source_revision(receipt)]
    if kind == "image_build_readiness" and operation == "probe":
        return _check_image_build_readiness_receipt(*arguments)
    problems = _check_common(*arguments)
    problems.extend(f"{name}:{item}" for item in _check_source_revision(receipt))
    try:
        if kind == "image_build_readiness" and operation == "chain":
            validate_specification(receipt, IMAGE_BUILD_READINESS_SCHEMA)
            records = receipt.get("records")
            records = records if isinstance(records, list) else []
            callbacks = [
                _callback_record(records, callback)
                for callback in (
                    "HarborBuild",
                    "HarborPush",
                    "CreateRunner",
                    "DeployManagerRunner",
                )
            ]
            if any(
                item is None or item.get("verified") is not True for item in callbacks
            ):
                problems.append(f"{name}:delivery_readback_missing")
            build, publish, _, deployed = (item or {} for item in callbacks)
            image = publish.get("image")
            if (
                not isinstance(image, str)
                or build.get("digest") != publish.get("digest")
                or deployed.get("image") != image
            ):
                problems.append(f"{name}:delivery_image_mismatch")
            cleanup = _mapping(receipt.get("cleanup"))
            if cleanup.get("status") != PASS or cleanup.get("failures") != []:
                problems.append(f"{name}:cleanup_unproven")
            problems.extend(_check_chain_runner_readback(name, receipt, review))
        elif kind == MIRROR_KIND and operation == MIRROR_OPERATION:
            problems.extend(
                f"{name}:{problem}" for problem in check_mirror_receipt(receipt)
            )
        elif kind == "ocp_node_onboard" and operation == "verify":
            validate_specification(receipt, "results/ocp-node-onboard.schema.json")
            problems.extend(_check_ocp_verify_readback(name, receipt))
        elif kind in GITLAB_RECEIPT_KINDS:
            context = _gitlab_context(
                receipt
                if kind == "gitlab_access"
                else {**receipt, "target": receipt.get("verified_target")}
            )
            problems = list(
                _check_gitlab_receipt(
                    name,
                    receipt,
                    required
                    or {
                        **context,
                        "kind": kind,
                        "operation": operation,
                        "result_action": receipt.get("result_action"),
                    },
                    review.functional_digest,
                    review.now,
                    review.max_age_days,
                )
            )
            problems.extend(
                f"{name}:{item}" for item in _check_source_revision(receipt)
            )
            if kind in {"gitlab_repository", "gitlab_merge_request"}:
                validate_specification(receipt, "results/gitlab-action.schema.json")
                supported = (
                    {"commit"} if kind == "gitlab_repository" else {"create", "update"}
                )
                if operation not in supported:
                    problems.append(f"{name}:operation_unexpected")
            elif (
                kind,
                operation,
                receipt.get("result_action"),
            ) not in REQUIRED_GITLAB_OPERATIONS:
                problems.append(f"{name}:receipt_not_declared")
            if any(not value for value in context.values()):
                problems.append(f"{name}:setup_context_incomplete")
        else:
            problems.append(f"{name}:receipt_not_declared")
    except (KeyError, OSError, SchemaError, SpecificationError, TypeError, ValueError):
        problems.append(f"{name}:result_schema_invalid")
    return problems


def evaluate(
    setup: Mapping[str, JSONObject],
    receipts: Mapping[str, JSONObject],
    skill_root: Path,
    now: Optional[datetime] = None,
    *,
    max_age_days: int,
    aggregate: bool = True,
    required_operations: Optional[Sequence[OperationKey]] = None,
) -> AcceptanceResult:
    """Compare action receipts with the initial authenticated runtime setup.

    :param setup: Existing access receipts keyed by name, captured before writes.
    :param receipts: Parsed operation and setup receipts keyed by file name.
    :param skill_root: Candidate skill tree used for digest comparison.
    :param now: Reference time for freshness, or None to read the current UTC time.
    :param max_age_days: Declared maximum receipt age from the acceptance policy.
    :param aggregate: Require the full operation set in addition to policy coverage.
    :param required_operations: Policy keys required in either mode; None preserves
        legacy programmatic evaluation without policy. The CLI always supplies them.
    :returns: Acceptance status, accepted receipts, and stable problem reasons.
    :raises ImportError: If the shared provenance module cannot be loaded.
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
    if aggregate:
        review.setup_receipts()
        review.operation_receipts()
        review.repeated_receipts()
    else:
        if not receipts:
            review.problems.append("receipts_missing")
        for name, receipt in receipts.items():
            problems = _check_targeted_receipt(name, receipt, review)
            review.problems.extend(problems)
            if not problems:
                review.accepted.append(name)
    review.mirror_receipts()
    if required_operations is not None:
        review.required_coverage(required_operations)
    result = review.result()
    result["mode"] = "aggregate" if aggregate else "targeted"
    revisions = {
        revision
        for receipt in receipts.values()
        if isinstance(
            revision := _mapping(_mapping(receipt.get("skill")).get("revision")).get(
                "value"
            ),
            str,
        )
    }
    result["tested_revision"] = next(iter(revisions)) if len(revisions) == 1 else None
    if len(revisions) > 1:
        result["problems"] = sorted([*result["problems"], "mixed_tested_revisions"])
        result["status"] = BLOCKED
    return result


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
        "--aggregate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="require all declared operation coverage (default); --no-aggregate "
        "checks the explicit --receipts directory; both require policy coverage",
    )
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
    if not args.aggregate and not args.receipts:
        cli.error("--no-aggregate requires --receipts PATH")

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
                "mode": "aggregate" if args.aggregate else "targeted",
                "tested_revision": None,
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
                aggregate=args.aggregate,
                required_operations=policy["required_operations"],
            )
    except (AcceptanceError, OSError, ValueError, TypeError) as exc:
        data = {
            "schema_version": SCHEMA_VERSION,
            "kind": "live_acceptance",
            "status": BLOCKED,
            "mode": "aggregate" if args.aggregate else "targeted",
            "tested_revision": None,
            "accepted": [],
            "problems": [str(exc)],
        }

    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif args.yaml:
        import yaml

        print(yaml.safe_dump(data, sort_keys=True), end="")
    else:
        print(f"Live acceptance: {data['status']} ({data['mode']})")
        print(f"  tested revision {data['tested_revision'] or 'unavailable'}")
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
