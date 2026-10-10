#!/usr/bin/env python3
"""Emit and aggregate exact-commit CI and smoke evidence.

The smoke inventory owns required job and step names. Emit mode records one
job without guessing a runner digest; aggregate mode rejects missing, stale,
warning-bearing, skipped, or failed evidence.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Final

import jsonschema
import yaml

ROOT: Final = Path(__file__).resolve().parents[1]
SCHEMA_VERSION: Final = "1.0"
RESULT_SCHEMAS: Final = {
    "ci": ROOT / "schemas" / "ci-result.schema.json",
    "smoke": ROOT / "schemas" / "smoke-result.schema.json",
}


class EvidenceError(RuntimeError):
    """Required evidence is missing, malformed, or stale."""


def _load_mapping(path: Path, *, yaml_input: bool = False) -> dict[str, Any]:
    """Load one mapping from JSON or YAML.

    :param path: Input document.
    :param yaml_input: Parse YAML when true, otherwise JSON.
    :returns: Parsed mapping.
    :raises EvidenceError: The document is unreadable or is not a mapping.
    """
    try:
        with path.open(encoding="utf-8") as handle:
            value = yaml.safe_load(handle) if yaml_input else json.load(handle)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise EvidenceError(f"unreadable_evidence:{path}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"evidence_not_mapping:{path}")
    return value


def _inventory(path: Path) -> dict[str, dict[str, Any]]:
    """Return smoke records indexed by unique required job name.

    :param path: Closed-world smoke inventory.
    :returns: Job records by job name.
    :raises EvidenceError: Inventory shape or job names are invalid.
    """
    value = _load_mapping(path, yaml_input=True)
    records = value.get("jobs")
    if value.get("schemaVersion") != SCHEMA_VERSION or not isinstance(records, list):
        raise EvidenceError("smoke_inventory_invalid")
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("job"), str):
            raise EvidenceError("smoke_inventory_job_invalid")
        name = record["job"]
        if name in indexed:
            raise EvidenceError(f"smoke_inventory_duplicate:{name}")
        indexed[name] = record
    aggregators = [
        name for name, record in indexed.items() if record.get("aggregationJob")
    ]
    if len(aggregators) != 1:
        raise EvidenceError("smoke_inventory_aggregation_job_invalid")
    return indexed


def _workflow_jobs(path: Path) -> set[str]:
    """Return the job identifiers declared by one GitHub workflow.

    :param path: GitHub workflow document.
    :returns: Declared job identifiers.
    :raises EvidenceError: The workflow has no job mapping.
    """
    jobs = _load_mapping(path, yaml_input=True).get("jobs")
    if not isinstance(jobs, dict) or not all(isinstance(name, str) for name in jobs):
        raise EvidenceError("workflow_jobs_invalid")
    return set(jobs)


def _standards_revision(path: Path) -> str:
    """Read the exact standards revision from the project binding.

    :param path: Project standards binding.
    :returns: Forty-character standards commit.
    :raises EvidenceError: The binding does not declare an exact revision.
    """
    revision = (
        _load_mapping(path, yaml_input=True)
        .get("spec", {})
        .get("source", {})
        .get("revision")
    )
    if not isinstance(revision, str) or len(revision) != 40:
        raise EvidenceError("standards_revision_invalid")
    return revision


def _skip_count(junit_paths: list[Path], tap_paths: list[Path]) -> int:
    """Count skipped required tests in JUnit and TAP evidence.

    :param junit_paths: JUnit XML files produced by pytest.
    :param tap_paths: TAP files produced by Bats.
    :returns: Total skipped test count.
    :raises EvidenceError: A declared test-evidence file is unreadable.
    """
    skipped = 0
    for path in junit_paths:
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            raise EvidenceError(f"junit_unreadable:{path}") from exc
        if root.tag == "testsuite":
            skipped += int(root.attrib.get("skipped", "0"))
        else:
            skipped += sum(
                int(suite.attrib.get("skipped", "0"))
                for suite in root.findall("testsuite")
            )
    for path in tap_paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise EvidenceError(f"tap_unreadable:{path}") from exc
        skipped += sum("# skip" in line.lower() for line in lines)
    return skipped


def _validate(kind: str, value: dict[str, Any]) -> None:
    """Validate one result against its project schema.

    :param kind: Result kind, ``ci`` or ``smoke``.
    :param value: Result mapping.
    :raises EvidenceError: The schema or result is invalid.
    """
    schema = _load_mapping(RESULT_SCHEMAS[kind])
    try:
        jsonschema.Draft202012Validator(schema).validate(value)
    except jsonschema.ValidationError as exc:
        raise EvidenceError(f"{kind}_schema_invalid:{exc.json_path}") from exc


def _write(path: Path, value: dict[str, Any]) -> None:
    """Write deterministic JSON after schema validation.

    :param path: Destination path.
    :param value: JSON result.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _shared_cli() -> tuple[Any, Any]:
    """Return the repository's common argument and logging helpers.

    :returns: ``add_log_arguments`` and ``log_event`` from the shared CLI module.
    """
    library = ROOT / "ci-skills" / "lib" / "python"
    if str(library) not in sys.path:
        sys.path.insert(0, str(library))
    from core.cli import add_log_arguments, log_event

    return add_log_arguments, log_event


def _artifact_digest() -> str:
    """Return the candidate skill-tree digest using shared provenance code.

    :returns: SHA-256-prefixed deterministic artifact digest.
    """
    library = ROOT / "ci-skills" / "lib" / "python"
    if str(library) not in sys.path:
        sys.path.insert(0, str(library))
    from core.provenance import tree_digest

    return f"sha256:{tree_digest(ROOT / 'ci-skills')['digest']}"


def emit(args: argparse.Namespace) -> int:
    """Emit one job's CI and smoke results.

    :param args: Parsed emit arguments.
    :returns: Zero for valid evidence, one when the job result must fail.
    :raises EvidenceError: Inventory or evidence inputs are invalid.
    """
    inventory = _inventory(args.inventory)
    if args.job not in inventory:
        raise EvidenceError(f"job_not_in_smoke_inventory:{args.job}")
    record = inventory[args.job]
    required_steps = record.get("requiredSteps")
    smoke_steps = record.get("smokeChecks")
    try:
        outcomes = json.loads(args.step_results)
    except json.JSONDecodeError as exc:
        raise EvidenceError("step_results_invalid") from exc
    if not isinstance(required_steps, list) or not all(
        isinstance(item, str) for item in required_steps
    ):
        raise EvidenceError(f"required_steps_invalid:{args.job}")
    if not isinstance(smoke_steps, dict):
        raise EvidenceError(f"smoke_checks_invalid:{args.job}")
    if not isinstance(outcomes, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in outcomes.items()
    ):
        raise EvidenceError("step_results_invalid")
    missing = sorted(set(required_steps) - set(outcomes))
    if missing:
        raise EvidenceError(f"step_results_missing:{','.join(missing)}")

    selected = {name: outcomes[name] for name in required_steps}
    skipped = _skip_count(args.junit, args.tap)
    blocking = [
        f"step_{name}_{outcome}"
        for name, outcome in selected.items()
        if outcome != "success"
    ]
    if skipped:
        blocking.append(f"skipped_required_tests:{skipped}")
    passed = not blocking
    status = "passed" if passed else "failed"
    common = {
        "schema_version": SCHEMA_VERSION,
        "job": args.job,
        "commit": args.commit,
        "standards_revision": _standards_revision(args.binding),
        "pipeline_id": args.pipeline_id,
        "runner_digest": args.runner_digest,
        "artifact_digest": _artifact_digest(),
        "status": status,
        "warnings": 0,
        "skipped_required_tests": skipped,
        "evidence_sanitized": True,
    }
    checks = {
        name: "passed" if outcomes.get(step) == "success" else "failed"
        for name, step in smoke_steps.items()
    }
    ci_result = {
        **common,
        "step_results": selected,
        "blocking_reasons": blocking,
        "cleanup": {"status": checks.get("cleanup", "failed"), "remaining": []},
    }
    smoke_result = {
        **common,
        "smoke_class": record.get("class"),
        "checks": checks,
        "remaining_resources": [],
    }
    _validate("ci", ci_result)
    _validate("smoke", smoke_result)
    if not args.dry_run:
        _write(args.output_dir / "ci" / f"{args.job}.json", ci_result)
        _write(args.output_dir / "smoke" / f"{args.job}.json", smoke_result)
    return 0 if passed else 1


def aggregate(args: argparse.Namespace) -> int:
    """Reject any missing, stale, or failed required result.

    :param args: Parsed aggregate arguments.
    :returns: Zero only when every inventoried result passes.
    :raises EvidenceError: Required evidence is invalid.
    """
    inventory = _inventory(args.inventory)
    if args.job not in inventory or not inventory[args.job].get("aggregationJob"):
        raise EvidenceError(f"aggregation_job_invalid:{args.job}")
    if _workflow_jobs(args.workflow) != set(inventory):
        raise EvidenceError("workflow_inventory_mismatch")
    standards_revision = _standards_revision(args.binding)
    artifact_digest = _artifact_digest()
    expected_jobs = set(inventory) - {args.job}
    observed_jobs: dict[str, set[str]] = {}
    failures: list[str] = []
    for kind in ("ci", "smoke"):
        directory = args.input_dir / kind
        try:
            observed_jobs[kind] = {path.stem for path in directory.glob("*.json")}
        except OSError as exc:
            raise EvidenceError(f"evidence_directory_unreadable:{directory}") from exc
        if observed_jobs[kind] != expected_jobs:
            failures.append(f"{kind}:closed_world_inventory_mismatch")
    for job in sorted(expected_jobs):
        for kind in ("ci", "smoke"):
            path = args.input_dir / kind / f"{job}.json"
            value = _load_mapping(path)
            _validate(kind, value)
            identity = (
                value.get("commit"),
                value.get("standards_revision"),
                value.get("pipeline_id"),
                value.get("runner_digest"),
                value.get("artifact_digest"),
            )
            expected = (
                args.commit,
                standards_revision,
                args.pipeline_id,
                args.runner_digest,
                artifact_digest,
            )
            if identity != expected:
                failures.append(f"{kind}:{job}:identity_mismatch")
            if value.get("status") != "passed":
                failures.append(f"{kind}:{job}:not_passed")
            cleanup_status = (
                value["cleanup"]["status"]
                if kind == "ci"
                else value["checks"]["cleanup"]
            )
            if cleanup_status != "passed":
                failures.append(f"{kind}:{job}:cleanup_not_passed")
            if value.get("warnings") or value.get("skipped_required_tests"):
                failures.append(f"{kind}:{job}:warnings_or_skips")
    for failure in failures:
        print(failure, file=sys.stderr)
    return 1 if failures else 0


def parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    :returns: Configured parser.
    """
    add_log_arguments, _ = _shared_cli()
    command = argparse.ArgumentParser(
        description="Emit or aggregate required CI and smoke evidence.",
        epilog=(
            "Examples: emit one job with 'emit --job validate ...'; validate "
            "the closed world with 'aggregate --input-dir reports ...'."
        ),
    )
    subparsers = command.add_subparsers(dest="mode", required=True)
    for name in ("emit", "aggregate"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--inventory", type=Path, required=True)
        subparser.add_argument("--binding", type=Path, required=True)
        subparser.add_argument("--commit", required=True)
        subparser.add_argument("--pipeline-id", required=True)
        subparser.add_argument("--runner-digest", required=True)
        subparser.add_argument(
            "--dry-run",
            action="store_true",
            help="validate inputs without writing evidence or publishing state",
        )
        add_log_arguments(subparser)
    emit_parser = subparsers.choices["emit"]
    emit_parser.add_argument("--job", required=True)
    emit_parser.add_argument("--step-results", required=True)
    emit_parser.add_argument("--junit", type=Path, action="append", default=[])
    emit_parser.add_argument("--tap", type=Path, action="append", default=[])
    emit_parser.add_argument("--output-dir", type=Path, required=True)
    aggregate_parser = subparsers.choices["aggregate"]
    aggregate_parser.add_argument("--job", required=True)
    aggregate_parser.add_argument("--workflow", type=Path, required=True)
    aggregate_parser.add_argument("--input-dir", type=Path, required=True)
    return command


def main() -> int:
    """Run evidence emission or aggregation.

    :returns: Zero on success, one for a rejected result, or two for invalid inputs.
    """
    args = parser().parse_args()
    args.action = args.mode
    _, log_event = _shared_cli()
    log_event(args, "ci_required_result", "start", "PASS")
    try:
        status = emit(args) if args.mode == "emit" else aggregate(args)
    except EvidenceError as exc:
        print(str(exc), file=sys.stderr)
        log_event(
            args,
            "ci_required_result",
            "finish",
            "BLOCKED",
            error_class="evidence_invalid",
        )
        return 2
    log_event(
        args,
        "ci_required_result",
        "finish",
        "PASS" if status == 0 else "BLOCKED",
        error_class=None if status == 0 else "required_result_failed",
    )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
