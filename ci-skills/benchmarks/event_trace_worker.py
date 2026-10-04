#!/usr/bin/env python3
"""Run one isolated live event-collector measurement for the A/B harness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from source_identity import source_identity

HARNESS_ROOT = Path(__file__).resolve().parents[1]


def _digest(value: Any) -> str:
    body = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _source(root: Path, revision: str) -> Path:
    root = root.resolve()
    scripts = root / "skills" / "ci-skills" / "scripts"
    if not scripts.is_dir():
        raise RuntimeError("skill_scripts_missing")
    source_identity(root, revision, scripts.parent)
    sys.path.insert(0, str(scripts))
    return scripts


def _harness(arguments: argparse.Namespace) -> dict[str, Any]:
    return source_identity(
        HARNESS_ROOT,
        arguments.harness_sha,
        HARNESS_ROOT / "benchmarks",
    )


def _source_kind(reference: str | None) -> str | None:
    if not reference:
        return None
    return reference.split(":", 1)[0]


def _bound_target(arguments: argparse.Namespace) -> Any:
    _source(Path(arguments.source_root), arguments.revision)
    from core.credentials import bind_sources
    from core.target import load_target

    target = bind_sources(load_target(arguments.target), revision=arguments.revision)
    skill = target.skill or {}
    revision = skill.get("revision") or {}
    if revision.get("value") != arguments.revision.lower():
        raise RuntimeError("bound_revision_mismatch")
    if revision.get("verified") is not True:
        raise RuntimeError("bound_revision_unverified")
    return target


def _identity(target: Any, target_file: Path) -> dict[str, Any]:
    sources = target.sources
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


def _skill(target: Any) -> dict[str, Any]:
    skill = target.skill or {}
    return {
        "algorithm": skill.get("algorithm"),
        "digest": skill.get("digest"),
        "file_count": skill.get("file_count"),
        "revision": skill.get("revision"),
    }


def _preflight(arguments: argparse.Namespace) -> dict[str, Any]:
    target = _bound_target(arguments)
    from core.access import check_access

    gate = check_access(target)
    surfaces = gate.get("surfaces") or {}
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
                name: surface.get("identity") for name, surface in surfaces.items()
            },
            "surface_status": {
                name: surface.get("status") for name, surface in surfaces.items()
            },
        },
    }


def _semantic(result: dict[str, Any]) -> dict[str, Any]:
    records = []
    for original in result.get("records", []):
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
    return (
        resource.getrusage(resource.RUSAGE_SELF),
        resource.getrusage(resource.RUSAGE_CHILDREN),
    )


def _cpu_seconds(
    before: tuple[resource.struct_rusage, resource.struct_rusage],
    after: tuple[resource.struct_rusage, resource.struct_rusage],
) -> float:
    return sum(
        later - earlier
        for earlier, later in (
            (before[0].ru_utime, after[0].ru_utime),
            (before[0].ru_stime, after[0].ru_stime),
            (before[1].ru_utime, after[1].ru_utime),
            (before[1].ru_stime, after[1].ru_stime),
        )
    )


def _sample(arguments: argparse.Namespace) -> dict[str, Any]:
    target = _bound_target(arguments)
    from core.collect import collect_events

    options = SimpleNamespace(
        from_time=arguments.from_time,
        to_time=arguments.to_time,
        namespace="all",
        kind=None,
        object=None,
        reason=None,
        search=None,
    )
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
            before = _usage()
            started = time.monotonic_ns()
            result = collect_events(target, options)
            wall_seconds = (time.monotonic_ns() - started) / 1_000_000_000
            after = _usage()
        finally:
            if original_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = original_path
            if original_log is None:
                os.environ.pop("CI_SKILLS_BENCH_KUBECTL_LOG", None)
            else:
                os.environ["CI_SKILLS_BENCH_KUBECTL_LOG"] = original_log
        kubectl_calls = (
            len(log_path.read_text(encoding="utf-8").splitlines())
            if log_path.is_file()
            else 0
        )
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
            "record_count": len(result.get("records", [])),
            "error_count": len(result.get("errors", [])),
            "kubectl_calls": kubectl_calls,
            "semantic_sha256": _digest(_semantic(result)),
            "strict_sha256": _digest(strict),
        },
    }


def _parser() -> argparse.ArgumentParser:
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
    arguments = _parser().parse_args()
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
