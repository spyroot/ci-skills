#!/usr/bin/env python3
"""Compare two exact event-trace implementations on one Kubernetes runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from event_trace_evidence import (
    EvidenceError,
    correctness,
    performance,
    same_preflight_identity,
    sample_matches_preflight,
    validate_limits,
    validate_worker_payload,
    variant_summary,
)
from source_identity import SourceIdentityError, source_identity

HARNESS_ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).with_name("event_trace_worker.py")


def _environment(executor_label: str) -> dict[str, Any]:
    namespace_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    namespace = (
        namespace_path.read_text(encoding="utf-8").strip()
        if namespace_path.is_file()
        else None
    )
    return {
        "executor_label": executor_label,
        "hostname_sha256": hashlib.sha256(socket.getfqdn().encode()).hexdigest(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "kubernetes_namespace": namespace,
        "ci_provider": (
            "gitlab"
            if os.environ.get("GITLAB_CI")
            else "github"
            if os.environ.get("GITHUB_ACTIONS")
            else "kubernetes-job"
            if os.environ.get("KUBERNETES_SERVICE_HOST")
            else "none"
        ),
    }


def _blocked(arguments: argparse.Namespace, reason: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": "event_trace_ab_benchmark",
        "status": "BLOCKED",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "revisions": {
            "harness": arguments.harness_sha.lower(),
            "baseline": arguments.baseline_sha.lower(),
            "candidate": arguments.candidate_sha.lower(),
        },
        "environment": _environment(arguments.executor_label),
        "workload": {"from": arguments.from_time, "to": arguments.to_time},
    }


def _worker(
    operation: str,
    *,
    root: Path,
    revision: str,
    harness_sha: str,
    target: Path,
    from_time: str | None,
    to_time: str | None,
    timeout: float,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(WORKER),
        operation,
        "--source-root",
        str(root),
        "--revision",
        revision,
        "--harness-sha",
        harness_sha,
        "--target",
        str(target),
    ]
    if from_time:
        command += ["--from", from_time]
    if to_time:
        command += ["--to", to_time]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"status": "BLOCKED", "reason": "benchmark_worker_timeout"}
    try:
        decoded = json.loads(result.stdout)
        payload = validate_worker_payload(operation, decoded)
    except (json.JSONDecodeError, EvidenceError) as exc:
        reason = (
            str(exc)
            if isinstance(exc, EvidenceError)
            else "benchmark_worker_invalid_json"
        )
        return {
            "status": "BLOCKED",
            "reason": reason,
            "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
        }
    if result.returncode and payload.get("status") != "BLOCKED":
        return {"status": "BLOCKED", "reason": "benchmark_worker_failed"}
    return payload


def _remaining_timeout(arguments: argparse.Namespace, deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 0
    return min(float(arguments.sample_timeout_seconds), remaining)


def _call_worker(
    arguments: argparse.Namespace,
    deadline: float,
    operation: str,
    root: Path,
    revision: str,
    target: Path,
) -> dict[str, Any]:
    timeout = _remaining_timeout(arguments, deadline)
    if timeout <= 0:
        return {"status": "BLOCKED", "reason": "benchmark_total_timeout"}
    return _worker(
        operation,
        root=root,
        revision=revision,
        harness_sha=arguments.harness_sha.lower(),
        target=target,
        from_time=arguments.from_time if operation == "sample" else None,
        to_time=arguments.to_time if operation == "sample" else None,
        timeout=timeout,
    )


def _blocked_with(
    arguments: argparse.Namespace, reason: str, **evidence: Any
) -> dict[str, Any]:
    return {**_blocked(arguments, reason), **evidence}


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    if platform.system() != "Linux" or not os.environ.get("KUBERNETES_SERVICE_HOST"):
        return _blocked(arguments, "authorized_kubernetes_runner_required")
    baseline_root = Path(arguments.baseline_root).resolve()
    candidate_root = Path(arguments.candidate_root).resolve()
    target = Path(arguments.target).resolve()
    try:
        harness = source_identity(
            HARNESS_ROOT,
            arguments.harness_sha,
            HARNESS_ROOT / "benchmarks",
        )
        variant_sources = {
            "baseline": source_identity(
                baseline_root,
                arguments.baseline_sha,
                baseline_root / "skills" / "k8s-admin-diagnostics",
            ),
            "candidate": source_identity(
                candidate_root,
                arguments.candidate_sha,
                candidate_root / "skills" / "k8s-admin-diagnostics",
            ),
        }
    except SourceIdentityError as exc:
        return _blocked(arguments, str(exc))
    variants = {
        "baseline": (baseline_root, arguments.baseline_sha.lower()),
        "candidate": (candidate_root, arguments.candidate_sha.lower()),
    }
    deadline = time.monotonic() + arguments.total_timeout_seconds
    preflights = {
        name: _call_worker(arguments, deadline, "preflight", root, revision, target)
        for name, (root, revision) in variants.items()
    }
    if any(item.get("status") != "PASS" for item in preflights.values()):
        return _blocked_with(
            arguments,
            "live_access_preflight_failed",
            harness=harness,
            variant_sources=variant_sources,
            preflights=preflights,
        )
    if any(item.get("harness") != harness for item in preflights.values()):
        return _blocked_with(
            arguments,
            "benchmark_harness_identity_mismatch",
            harness=harness,
            preflights=preflights,
        )
    if not same_preflight_identity(preflights):
        return _blocked_with(
            arguments,
            "target_or_credential_source_mismatch",
            harness=harness,
            preflights=preflights,
        )

    def measured(variant: str) -> dict[str, Any]:
        root, revision = variants[variant]
        payload = _call_worker(arguments, deadline, "sample", root, revision, target)
        payload["variant"] = variant
        return payload

    warmups: list[dict[str, Any]] = []
    for index in range(arguments.warmups):
        order = (
            ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
        )
        for variant in order:
            warmups.append(measured(variant))
    if any(
        item.get("status") != "PASS"
        or not sample_matches_preflight(preflights[item["variant"]], item)
        for item in warmups
    ):
        return _blocked_with(
            arguments,
            "warmup_failed_or_identity_drifted",
            harness=harness,
            preflights=preflights,
            warmups=warmups,
        )

    pairs: list[dict[str, dict[str, Any]]] = []
    raw_samples: list[dict[str, Any]] = []
    for index in range(arguments.samples):
        order = (
            ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
        )
        pair: dict[str, dict[str, Any]] = {}
        for variant in order:
            sample = measured(variant)
            sample["pair"] = index + 1
            pair[variant] = sample
            raw_samples.append(sample)
        pairs.append(pair)
    if any(item.get("status") not in {"PASS", "PARTIAL"} for item in raw_samples):
        return _blocked_with(
            arguments,
            "measurement_failed",
            harness=harness,
            preflights=preflights,
            samples=raw_samples,
        )
    if any(
        not sample_matches_preflight(preflights[item["variant"]], item)
        for item in raw_samples
    ):
        return _blocked_with(
            arguments,
            "measurement_identity_drifted",
            harness=harness,
            preflights=preflights,
            samples=raw_samples,
        )
    grouped = {
        name: [pair[name] for pair in pairs] for name in ("baseline", "candidate")
    }
    summaries = {name: variant_summary(items) for name, items in grouped.items()}
    correctness_result = correctness(summaries)
    performance_result = performance(
        pairs,
        summaries,
        minimum_improvement=arguments.minimum_improvement_percent,
        minimum_faster_pairs=arguments.minimum_faster_pairs,
        maximum_p95_regression=arguments.maximum_p95_regression_percent,
    )
    passed = (
        correctness_result["status"] == "PASS"
        and performance_result["acceptance"] == "PASS"
    )
    return {
        "schema_version": "1.0",
        "kind": "event_trace_ab_benchmark",
        "status": "PASS" if passed else "BLOCKED",
        "reason": None if passed else "acceptance_failed",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "revisions": {
            "harness": arguments.harness_sha.lower(),
            "baseline": arguments.baseline_sha.lower(),
            "candidate": arguments.candidate_sha.lower(),
        },
        "harness": harness,
        "variant_sources": variant_sources,
        "environment": _environment(arguments.executor_label),
        "workload": {
            "from": arguments.from_time,
            "to": arguments.to_time,
            "warmups": arguments.warmups,
            "samples_per_variant": arguments.samples,
            "maximum_live_operations": 2 + 2 * (arguments.warmups + arguments.samples),
            "sample_timeout_seconds": arguments.sample_timeout_seconds,
            "total_timeout_seconds": arguments.total_timeout_seconds,
            "order": "alternating paired A/B then B/A",
            "boundary": "collect_events; access preflight excluded",
        },
        "preflights": preflights,
        "correctness": correctness_result,
        "performance": performance_result,
        "summary": summaries,
        "samples": raw_samples,
    }


def _format_seconds(value: float) -> str:
    return f"{value:.3f} s"


def _change(baseline: float, candidate: float) -> str:
    percent = (candidate - baseline) / baseline * 100 if baseline else 0.0
    return f"{-percent:+.1f}% improvement"


def _markdown(evidence: dict[str, Any]) -> str:
    base = evidence.get("summary", {}).get("baseline", {})
    candidate = evidence.get("summary", {}).get("candidate", {})
    correctness_result = evidence.get("correctness", {})
    performance_result = evidence.get("performance", {})
    revisions = evidence["revisions"]
    harness_digest = evidence.get("harness", {}).get("digest", "not-run")
    variant_sources = evidence.get("variant_sources", {})
    baseline_digest = variant_sources.get("baseline", {}).get("digest", "not-run")
    candidate_digest = variant_sources.get("candidate", {}).get("digest", "not-run")
    lines = [
        "## Event trace A/B evidence",
        "",
        "| Field | Baseline | Candidate | Result |",
        "| --- | --- | --- | --- |",
        f"| Harness exact SHA | `{revisions['harness']}` | `{revisions['harness']}` | {'planned' if evidence.get('status') == 'DRY_RUN' else 'pinned'} |",
        f"| Harness tree SHA-256 | `{harness_digest}` | `{harness_digest}` | content identity |",
        f"| Source exact SHA | `{revisions['baseline']}` | `{revisions['candidate']}` | {'planned' if evidence.get('status') == 'DRY_RUN' else 'pinned'} |",
        f"| Source tree SHA-256 | `{baseline_digest}` | `{candidate_digest}` | content identity |",
        f"| Samples | {base.get('sample_count', 0)} | {candidate.get('sample_count', 0)} | warmups excluded |",
    ]
    if base and candidate:
        base_wall = base["wall_seconds"]
        candidate_wall = candidate["wall_seconds"]
        lines += [
            f"| Wall p50 | {_format_seconds(base_wall['p50'])} | {_format_seconds(candidate_wall['p50'])} | {_change(base_wall['p50'], candidate_wall['p50'])} |",
            f"| Wall p95 | {_format_seconds(base_wall['p95'])} | {_format_seconds(candidate_wall['p95'])} | {_change(base_wall['p95'], candidate_wall['p95'])} |",
            f"| Wall CV | {base_wall['cv_percent']:.1f}% | {candidate_wall['cv_percent']:.1f}% | variance read-back |",
            f"| Record counts | `{base['record_counts']}` | `{candidate['record_counts']}` | {'PASS' if correctness_result.get('record_counts_equal') else 'BLOCKED'} |",
            f"| Semantic SHA-256 | `{base['semantic_sha256'][0] if len(base['semantic_sha256']) == 1 else 'unstable'}` | `{candidate['semantic_sha256'][0] if len(candidate['semantic_sha256']) == 1 else 'unstable'}` | {'PASS' if correctness_result.get('semantic_equal') else 'BLOCKED'} |",
            f"| Collector errors | `{base['error_counts']}` | `{candidate['error_counts']}` | {'PASS' if correctness_result.get('no_errors') else 'BLOCKED'} |",
            f"| Kubernetes calls | `{base['kubectl_call_counts']}` | `{candidate['kubectl_call_counts']}` | {'PASS' if performance_result.get('stable_kubectl_call_reduction') else 'BLOCKED'} |",
            f"| Faster sample pairs | n/a | {performance_result.get('candidate_faster_pairs', 0)}/{base.get('sample_count', 0)} | observed |",
        ]
    lines += [
        f"| Correctness gate | n/a | n/a | **{correctness_result.get('status', 'NOT_RUN')}** |",
        f"| Performance acceptance | n/a | n/a | **{performance_result.get('acceptance', 'NOT_RUN')}** |",
        f"| Overall | n/a | n/a | **{evidence.get('status', 'BLOCKED')}** |",
        "",
        "Environment and limits:",
        "",
        f"- Executor: `{evidence.get('environment', {}).get('executor_label', 'unknown')}` in Kubernetes namespace `{evidence.get('environment', {}).get('kubernetes_namespace') or 'unknown'}`.",
        f"- Window: `{evidence.get('workload', {}).get('from')}` through `{evidence.get('workload', {}).get('to')}`.",
        (
            "- No measurements were run; this artifact records the planned boundary."
            if evidence.get("status") == "DRY_RUN"
            else "- Measurements cover `collect_events()` with the pinned Kubernetes target and credential identity. Preflight time is excluded."
        ),
        "- A PASS applies only to this executor, target, window, harness SHA, and source SHA pair.",
        "",
    ]
    return "\n".join(lines)


def _write(path: Path, body: str) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_text(body, encoding="utf-8")
    os.replace(temporary, path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark exact baseline and candidate event collectors live.",
        epilog=(
            "Run only in an authorized Linux Kubernetes Job or Kubernetes-backed "
            "CI runner with the same Kubernetes target and effective credential mounted."
        ),
    )
    parser.add_argument("--harness-sha", required=True, metavar="SHA")
    parser.add_argument("--baseline-root", required=True, metavar="PATH")
    parser.add_argument("--baseline-sha", required=True, metavar="SHA")
    parser.add_argument("--candidate-root", required=True, metavar="PATH")
    parser.add_argument("--candidate-sha", required=True, metavar="SHA")
    parser.add_argument("--target", required=True, metavar="PATH")
    parser.add_argument("--executor-label", required=True, metavar="LABEL")
    parser.add_argument("--from", dest="from_time", required=True, metavar="RFC3339")
    parser.add_argument("--to", dest="to_time", required=True, metavar="RFC3339")
    parser.add_argument("--warmups", type=int, default=1, metavar="N")
    parser.add_argument("--samples", type=int, default=7, metavar="N")
    parser.add_argument(
        "--sample-timeout-seconds", type=int, default=300, metavar="SECONDS"
    )
    parser.add_argument(
        "--total-timeout-seconds", type=int, default=900, metavar="SECONDS"
    )
    parser.add_argument("--minimum-improvement-percent", type=float, metavar="PERCENT")
    parser.add_argument("--minimum-faster-pairs", type=int, metavar="N")
    parser.add_argument(
        "--maximum-p95-regression-percent", type=float, metavar="PERCENT"
    )
    parser.add_argument("--json-out", required=True, metavar="PATH")
    parser.add_argument("--markdown-out", required=True, metavar="PATH")
    parser.add_argument(
        "--dry-run", action="store_true", help="write the benchmark plan only"
    )
    return parser


def _plan(arguments: argparse.Namespace) -> dict[str, Any]:
    return {
        **_blocked(arguments, "benchmark_not_executed"),
        "status": "DRY_RUN",
        "plan": {
            "warmups": arguments.warmups,
            "samples_per_variant": arguments.samples,
            "maximum_live_operations": 2 + 2 * (arguments.warmups + arguments.samples),
            "sample_timeout_seconds": arguments.sample_timeout_seconds,
            "total_timeout_seconds": arguments.total_timeout_seconds,
            "thresholds": {
                "minimum_median_improvement_percent": arguments.minimum_improvement_percent,
                "minimum_faster_pairs": arguments.minimum_faster_pairs,
                "maximum_p95_regression_percent": arguments.maximum_p95_regression_percent,
            },
        },
    }


def main() -> int:
    arguments = _parser().parse_args()
    invalid = validate_limits(arguments)
    if invalid:
        evidence = _blocked(arguments, invalid)
    elif arguments.dry_run:
        evidence = _plan(arguments)
    else:
        evidence = run(arguments)
    _write(
        Path(arguments.json_out), json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    )
    _write(Path(arguments.markdown_out), _markdown(evidence))
    sys.stdout.write(
        json.dumps(
            {
                "status": evidence["status"],
                "json": str(Path(arguments.json_out).resolve()),
                "markdown": str(Path(arguments.markdown_out).resolve()),
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 0 if evidence["status"] in {"PASS", "DRY_RUN"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
