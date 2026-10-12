#!/usr/bin/env python3
"""Run one isolated live event-collector measurement for the A/B harness.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import runpy
import shlex
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping, MutableSequence, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from event_trace_evidence import WorkerPayload

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue
    from lib.python.core.target import Target

from source_identity import SourceIdentity, source_identity

HARNESS_ROOT = Path(__file__).resolve().parents[1]


def _digest(value: JSONValue) -> str:
    """Hash one JSON-serializable measurement value deterministically.

    :param value: Semantic or strict collector result selected for comparison.
    :returns: SHA-256 hex digest of sorted compact UTF-8 JSON.
    :raises TypeError: If the selected value cannot be serialized as JSON.
    :raises ValueError: If the selected containers form a cycle.
    :raises UnicodeError: If a selected string cannot be encoded as UTF-8.
    """  # noqa: DOC502 - JSON serialization and UTF-8 encoding can fail.
    body = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _source(root: Path, revision: str) -> Path:
    """Verify and load one exact benchmark variant's Python library.

    :param root: Variant repository checkout.
    :param revision: Full commit SHA expected at the checkout HEAD.
    :returns: Verified library directory added to the worker import path.
    :raises RuntimeError: If the expected packaged library directory is missing.
    :raises OSError: If source files or the bootstrap cannot be read, or Git cannot start.
    :raises subprocess.TimeoutExpired: If a source Git query exceeds its bound.
    :raises SourceIdentityError: Its tracked source identity is not clean and exact.
    """  # noqa: DOC503 - Source, credential, and read helpers propagate failures.
    root = root.resolve()
    library = root / "ci-skills" / "lib" / "python"
    if not library.is_dir():
        raise RuntimeError("skill_library_missing")
    source_identity(root, revision, library.parents[1])
    runpy.run_path(str(root / "ci-skills" / "bin" / "_bootstrap.py"))
    return library


def _harness(arguments: argparse.Namespace) -> SourceIdentity:
    """Verify the worker's own benchmark harness source identity.

    :param arguments: Parsed harness commit SHA and benchmark operation options.
    :returns: Clean harness subtree digest and exact revision record.
    :raises SourceIdentityError: If the harness checkout or subtree is not clean and exact.
    :raises OSError: If source files cannot be read or Git cannot start.
    :raises subprocess.TimeoutExpired: If a source Git query exceeds its bound.
    """  # noqa: DOC502 - Exact source identity performs bounded Git/file reads.
    return source_identity(
        HARNESS_ROOT,
        arguments.harness_sha,
        HARNESS_ROOT / "benchmarks",
    )


def _source_kind(reference: str) -> Optional[str]:
    """Read the scheme of a bound credential-source reference.

    :param reference: Bound source reference such as file: or env:.
    :returns: Prefix before the first colon, or ``None`` for an empty reference.
    """
    if not reference:
        return None
    return reference.split(":", 1)[0]


def _bound_target(arguments: argparse.Namespace) -> Target:
    """Bind selected credentials and require a verified variant revision.

    :param arguments: Variant root, exact revision, and selected target path.
    :returns: Target with frozen credential sources and verified skill identity.
    :raises RuntimeError: If bound revision provenance is malformed, differs from
        the selected revision, or is not verified.
    :raises SourceIdentityError: Source checkout does not match the exact revision.
    :raises ImportError: If the verified variant library or one of its dependencies is unavailable.
    :raises TargetError: If the selected target or credential source is invalid.
    :raises OSError: If a source/credential file is unreadable or native binding cannot start.
    :raises subprocess.TimeoutExpired: If a source Git query exceeds its bound.
    """  # noqa: DOC503 - Source, credential, and read helpers propagate failures.
    _source(Path(arguments.source_root), arguments.revision)
    from lib.python.access.credentials import bind_sources
    from lib.python.core.target import load_target

    target = bind_sources(load_target(arguments.target), revision=arguments.revision)
    skill = target.skill or {}
    revision = skill.get("revision") or {}
    if not isinstance(revision, Mapping):
        # Malformed provenance is a revision verification failure.
        raise RuntimeError("bound_revision_mismatch")  # noqa: TRY004
    if revision.get("value") != arguments.revision.lower():
        raise RuntimeError("bound_revision_mismatch")
    if revision.get("verified") is not True:
        raise RuntimeError("bound_revision_unverified")
    return target


def _identity(target: Target, target_file: Path) -> JSONObject:
    """Record target and credential-source identity without token values.

    :param target: Bound target with all three authority and source records.
    :param target_file: Selected TOML target whose bytes are hashed.
    :returns: Target digest, provider identities, source-kind and reference
        digests, and ordered kubeconfig digests.
    :raises OSError: If the selected target file cannot be read.
    :raises RuntimeError: If any required authority or bound credential source is absent.
    """  # noqa: DOC503 - Hashing reads the selected target file.
    sources = target.sources
    if target.github is None or target.gitlab is None or target.kubernetes is None:
        raise RuntimeError("benchmark_authorities_missing")
    if (
        sources is None
        or sources.github is None
        or sources.gitlab is None
        or sources.kubernetes is None
    ):
        raise RuntimeError("credential_sources_unbound")
    references = {
        "github": sources.github.reference,
        "gitlab": sources.gitlab.reference,
        "kubernetes": sources.kubernetes.reference,
    }
    return {
        "target_sha256": hashlib.sha256(target_file.read_bytes()).hexdigest(),
        "targets": {
            "github": f"{target.github.host}/{target.github.repository}",
            "gitlab": target.gitlab.url,
            "kubernetes": {
                "context": target.kubernetes.context,
                "server": target.kubernetes.server,
            },
        },
        "credential_sources": {
            name: {
                "kind": _source_kind(reference),
                "reference_sha256": hashlib.sha256(reference.encode()).hexdigest(),
            }
            for name, reference in references.items()
        },
        "kubeconfig_sha256": list(sources.kubeconfig_digests),
    }


def _skill(target: Target) -> JSONObject:
    """Select measured skill provenance from a bound target.

    :param target: Bound target carrying optional skill identity.
    :returns: Digest algorithm, digest, file count, and revision fields.
    """
    skill = target.skill or {}
    return {
        "algorithm": skill.get("algorithm"),
        "digest": skill.get("digest"),
        "file_count": skill.get("file_count"),
        "revision": skill.get("revision"),
    }


def _preflight(arguments: argparse.Namespace) -> WorkerPayload:
    """Check live access before benchmarking one exact source variant.

    :param arguments: Variant source, target, and harness identity options.
    :returns: Preflight status with harness/source identities, target and
        credential-source identity, and per-surface access evidence.
    :raises SourceIdentityError: Source or harness checkout is not exact and clean.
    :raises ImportError: If the verified variant library or one of its dependencies is unavailable.
    :raises TargetError: If the selected target or credential source cannot be bound.
    :raises RuntimeError: If required bound provenance, authorities, or access fields are absent.
    :raises OSError: If source/credential files are unreadable or native reads cannot start.
    :raises subprocess.TimeoutExpired: If a source Git query exceeds its bound.
    """  # noqa: DOC503 - Source, credential, and read helpers propagate failures.
    target = _bound_target(arguments)
    from lib.python.access.access import check_access

    gate = check_access(target)
    surfaces = gate.get("surfaces") or {}
    if not isinstance(surfaces, Mapping) or any(
        not isinstance(surface, Mapping) for surface in surfaces.values()
    ):
        raise RuntimeError("access_surface_invalid")
    return {
        "schema_version": "1.0",
        "kind": "event_trace_benchmark_preflight",
        "status": gate.get("status"),
        "harness": _harness(arguments),
        "source": _skill(target),
        "identity": _identity(target, Path(arguments.target)),
        "access": {
            "profile": "base",
            "identities": {
                name: surface.get("identity")
                for name, surface in surfaces.items()
                if isinstance(surface, Mapping)
            },
            "surface_status": {
                name: surface.get("status")
                for name, surface in surfaces.items()
                if isinstance(surface, Mapping)
            },
        },
    }


def _semantic(result: JSONObject) -> JSONObject:
    """Select collector fields whose values must match across variants.

    :param result: Complete event collector report.
    :returns: Kind, target, filters, records without source labels, errors,
        status, and summary for semantic hashing.
    :raises TypeError: If records is not an array of JSON objects.
    """
    observations = result.get("records", [])
    if not isinstance(observations, Sequence) or isinstance(observations, str):
        raise TypeError("collector_records_invalid")
    records: MutableSequence[JSONObject] = []
    for original in observations:
        if not isinstance(original, Mapping):
            raise TypeError("collector_record_invalid")
        record = dict(original)
        record.pop("source", None)
        records.append(record)
    return {
        "kind": result.get("kind"),
        "target": result.get("target"),
        "filters": result.get("filters"),
        "records": records,
        "errors": result.get("errors", []),
        "status": result.get("status"),
        "summary": result.get("summary"),
    }


def _usage() -> tuple[resource.struct_rusage, resource.struct_rusage]:
    """Read resource usage for worker and child processes.

    :returns: Current self and children ``struct_rusage`` snapshots.
    """
    return (
        resource.getrusage(resource.RUSAGE_SELF),
        resource.getrusage(resource.RUSAGE_CHILDREN),
    )


def _cpu_seconds(
    before: tuple[resource.struct_rusage, resource.struct_rusage],
    after: tuple[resource.struct_rusage, resource.struct_rusage],
) -> float:
    """Calculate combined user and system CPU time between two snapshots.

    :param before: Self and child usage before collector execution.
    :param after: Self and child usage after collector execution.
    :returns: Total CPU seconds consumed between snapshots.
    """
    return sum(
        later - earlier
        for earlier, later in (
            (before[0].ru_utime, after[0].ru_utime),
            (before[0].ru_stime, after[0].ru_stime),
            (before[1].ru_utime, after[1].ru_utime),
            (before[1].ru_stime, after[1].ru_stime),
        )
    )


@contextmanager
def kubectl_counter() -> Iterator[Path]:
    """Count native kubectl calls while restoring the worker environment on exit.

    :ytype: Path
    :yields: Private log path containing one line per wrapped kubectl invocation.
    :raises RuntimeError: If kubectl is unavailable on the original executable path.
    :raises OSError: If temporary wrapper creation, permissions, or cleanup fails.
    """  # noqa: DOC503 - Private wrapper creation and cleanup may fail.
    real_kubectl = shutil.which("kubectl")
    if not real_kubectl:
        raise RuntimeError("kubectl_missing")
    original_path = os.environ.get("PATH")
    original_log = os.environ.get("CI_SKILLS_BENCH_KUBECTL_LOG")
    with tempfile.TemporaryDirectory(prefix="ci-skills-benchmark-") as temporary:
        temporary_path = Path(temporary)
        log_path = temporary_path / "kubectl-count"
        wrapper = temporary_path / "kubectl"
        wrapper.write_text(
            "#!/bin/sh\n"
            "printf '1\\n' >> \"$CI_SKILLS_BENCH_KUBECTL_LOG\"\n"
            f'exec {shlex.quote(real_kubectl)} "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
        os.environ["CI_SKILLS_BENCH_KUBECTL_LOG"] = str(log_path)
        os.environ["PATH"] = f"{temporary}{os.pathsep}{original_path or ''}"
        try:
            yield log_path
        finally:
            if original_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = original_path
            if original_log is None:
                os.environ.pop("CI_SKILLS_BENCH_KUBECTL_LOG", None)
            else:
                os.environ["CI_SKILLS_BENCH_KUBECTL_LOG"] = original_log


def _sample(arguments: argparse.Namespace) -> WorkerPayload:
    """Measure one live event collection with exact bound source identity.

    :param arguments: Variant source, target, fixed time window, and harness SHA.
    :returns: Worker sample with wall/CPU time, report size/counts, counted
        ``kubectl`` calls, semantic digest, and strict digest.
    :raises RuntimeError: If kubectl, required authorities, or verified provenance is absent.
    :raises OSError: If source/target files, instrumentation, or native reads fail.
    :raises UnicodeError: If a captured counter log is not valid UTF-8.
    :raises TypeError: If the collector returns malformed report records or errors.
    :raises ValueError: If the requested event time window is invalid.
    :raises OverflowError: If requested event time arithmetic exceeds datetime bounds.
    :raises subprocess.TimeoutExpired: If a source identity Git query exceeds its bound.
    :raises SourceIdentityError: Variant or harness source is not exact and clean.
    :raises ImportError: If the verified variant library or one of its dependencies is unavailable.
    :raises TargetError: If target or credential binding fails.
    """  # noqa: DOC503 - Source, credential, and read helpers propagate failures.
    target = _bound_target(arguments)
    from lib.python.core.collect import collect_events

    options = argparse.Namespace(
        from_time=arguments.from_time,
        to_time=arguments.to_time,
        namespace="all",
        kind=None,
        object=None,
        reason=None,
        search=None,
    )
    with kubectl_counter() as log_path:
        before = _usage()
        started = time.monotonic_ns()
        result = collect_events(target, options)
        wall_seconds = (time.monotonic_ns() - started) / 1_000_000_000
        after = _usage()
        kubectl_calls = (
            len(log_path.read_text(encoding="utf-8").splitlines())
            if log_path.is_file()
            else 0
        )
    records = result.get("records", [])
    errors = result.get("errors", [])
    if not isinstance(records, Sequence) or isinstance(records, str):
        raise TypeError("collector_records_invalid")
    if not isinstance(errors, Sequence) or isinstance(errors, str):
        raise TypeError("collector_errors_invalid")
    strict = dict(result)
    strict.pop("captured_at", None)
    compact = json.dumps(
        result,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "schema_version": "1.0",
        "kind": "event_trace_benchmark_sample",
        "status": result.get("status"),
        "harness": _harness(arguments),
        "source": _skill(target),
        "identity": _identity(target, Path(arguments.target)),
        "measurement": {
            "wall_seconds": wall_seconds,
            "cpu_seconds": _cpu_seconds(before, after),
            "collector_json_bytes": len(compact),
            "record_count": len(records),
            "error_count": len(errors),
            "kubectl_calls": kubectl_calls,
            "semantic_sha256": _digest(_semantic(result)),
            "strict_sha256": _digest(strict),
        },
    }


def _parser() -> argparse.ArgumentParser:
    """Declare isolated worker CLI inputs for preflight and sample operations.

    :returns: Parser accepting exact source/harness identities, target, optional
        time window, and plan-only mode.
    """
    parser = argparse.ArgumentParser(
        description="Run one exact-source live event-trace benchmark operation.",
        epilog=(
            "This worker is called by event_trace_ab.py inside an authorized "
            "Kubernetes runner. It never emits event records or credential references."
        ),
    )
    parser.add_argument("operation", choices=("preflight", "sample"))
    parser.add_argument("--source-root", required=True, metavar="PATH")
    parser.add_argument("--revision", required=True, metavar="SHA")
    parser.add_argument("--harness-sha", required=True, metavar="SHA")
    parser.add_argument("--target", required=True, metavar="PATH")
    parser.add_argument("--from", dest="from_time", metavar="RFC3339")
    parser.add_argument("--to", dest="to_time", metavar="RFC3339")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the planned worker operation"
    )
    return parser


def main() -> int:
    """Run one benchmark worker operation and emit bounded JSON.

    :returns: Zero for PASS, DRY_RUN, or PARTIAL sample; two for blocked results.
    :raises SystemExit: If arguments are invalid or help is requested.
    :raises OSError: If final evidence cannot be written to stdout.
    """  # noqa: DOC503 - Worker failures are caught; argparse/stdout may still fail.
    arguments = _parser().parse_args()
    payload: WorkerPayload
    try:
        if arguments.dry_run:
            payload = {
                "schema_version": "1.0",
                "kind": "event_trace_benchmark_worker_plan",
                "status": "DRY_RUN",
                "operation": arguments.operation,
                "revision": arguments.revision.lower(),
                "harness_revision": arguments.harness_sha.lower(),
                "fixed_time_bounds": bool(arguments.from_time and arguments.to_time),
            }
        elif platform.system() != "Linux" or not os.environ.get(
            "KUBERNETES_SERVICE_HOST"
        ):
            raise RuntimeError("authorized_kubernetes_runner_required")
        elif arguments.operation == "sample" and (
            not arguments.from_time or not arguments.to_time
        ):
            raise RuntimeError("fixed_time_bounds_required")
        else:
            payload = (
                _preflight(arguments)
                if arguments.operation == "preflight"
                else _sample(arguments)
            )
    except Exception as exc:  # noqa: BLE001 - emit a bounded benchmark failure
        raw_reason = str(exc)
        reason = (
            raw_reason if raw_reason.replace("_", "").isalnum() else "worker_failure"
        )
        payload = {
            "schema_version": "1.0",
            "kind": "event_trace_benchmark_worker_failure",
            "status": "BLOCKED",
            "reason": reason[:80],
            "detail_sha256": hashlib.sha256(raw_reason.encode()).hexdigest(),
        }
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    successful = payload.get("status") in {"PASS", "DRY_RUN"} or (
        arguments.operation == "sample" and payload.get("status") == "PARTIAL"
    )
    return 0 if successful else 2


if __name__ == "__main__":
    raise SystemExit(main())
