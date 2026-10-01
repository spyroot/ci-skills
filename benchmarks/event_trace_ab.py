#!/usr/bin/env python3
"""Compare two exact event-trace implementations on one Kubernetes runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import socket
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKER = Path(__file__).with_name("event_trace_worker.py")


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _summary(values: list[float]) -> dict[str, float]:
    mean = statistics.fmean(values)
    deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "min": min(values),
        "p50": statistics.median(values),
        "mean": mean,
        "p95": _percentile(values, 0.95),
        "max": max(values),
        "stdev": deviation,
        "cv_percent": (deviation / mean * 100) if mean else 0.0,
    }


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode:
        raise RuntimeError("source_git_read_failed")
    return result.stdout.strip()


def _check_source(root: Path, revision: str) -> None:
    if _git(root, "rev-parse", "HEAD").lower() != revision.lower():
        raise RuntimeError("source_revision_mismatch")
    skill = root / "skills" / "k8s-admin-diagnostics"
    if _git(root, "status", "--porcelain", "--", str(skill)):
        raise RuntimeError("skill_source_dirty")


def _worker(
    operation: str,
    *,
    root: Path,
    revision: str,
    target: Path,
    from_time: str | None,
    to_time: str | None,
    timeout: int,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(WORKER),
        operation,
        "--source-root",
        str(root),
        "--revision",
        revision,
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
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "status": "BLOCKED",
            "reason": "benchmark_worker_invalid_json",
            "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
        }
    if result.returncode and payload.get("status") != "BLOCKED":
        payload["status"] = "BLOCKED"
        payload["reason"] = "benchmark_worker_failed"
    return payload


def _same_identity(preflights: dict[str, dict[str, Any]]) -> bool:
    baseline = preflights["baseline"]
    candidate = preflights["candidate"]
    return (
        baseline.get("identity") == candidate.get("identity")
        and baseline.get("identity") is not None
        and baseline.get("access") == candidate.get("access")
    )


def _variant_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [sample["measurement"] for sample in samples]
    return {
        "sample_count": len(samples),
        "wall_seconds": _summary([item["wall_seconds"] for item in metrics]),
        "cpu_seconds": _summary([item["cpu_seconds"] for item in metrics]),
        "max_process_rss_kib": _summary(
            [float(item["max_process_rss_kib"]) for item in metrics]
        ),
        "collector_json_bytes": _summary(
            [float(item["collector_json_bytes"]) for item in metrics]
        ),
        "record_counts": sorted({item["record_count"] for item in metrics}),
        "error_counts": sorted({item["error_count"] for item in metrics}),
        "kubectl_call_counts": sorted({item["kubectl_calls"] for item in metrics}),
        "semantic_sha256": sorted({item["semantic_sha256"] for item in metrics}),
        "strict_sha256": sorted({item["strict_sha256"] for item in metrics}),
        "statuses": sorted({sample.get("status") for sample in samples}),
    }


def _correctness(summary: dict[str, dict[str, Any]]) -> dict[str, Any]:
    baseline = summary["baseline"]
    candidate = summary["candidate"]
    stable_baseline = len(baseline["semantic_sha256"]) == 1
    stable_candidate = len(candidate["semantic_sha256"]) == 1
    semantic_equal = (
        stable_baseline
        and stable_candidate
        and baseline["semantic_sha256"] == candidate["semantic_sha256"]
    )
    record_counts_equal = (
        len(baseline["record_counts"]) == 1
        and baseline["record_counts"] == candidate["record_counts"]
    )
    no_errors = baseline["error_counts"] == [0] and candidate["error_counts"] == [0]
    statuses_pass = baseline["statuses"] == ["PASS"] and candidate["statuses"] == [
        "PASS"
    ]
    return {
        "status": (
            "PASS"
            if all(
                (
                    stable_baseline,
                    stable_candidate,
                    semantic_equal,
                    record_counts_equal,
                    no_errors,
                    statuses_pass,
                )
            )
            else "BLOCKED"
        ),
        "baseline_stable": stable_baseline,
        "candidate_stable": stable_candidate,
        "semantic_equal": semantic_equal,
        "record_counts_equal": record_counts_equal,
        "no_errors": no_errors,
        "statuses_pass": statuses_pass,
        "strict_equal": baseline["strict_sha256"] == candidate["strict_sha256"],
    }


def _performance(
    pairs: list[dict[str, dict[str, Any]]],
    summary: dict[str, dict[str, Any]],
    *,
    minimum_improvement: float | None,
    minimum_faster_pairs: int | None,
    maximum_p95_regression: float | None,
) -> dict[str, Any]:
    paired_improvements = [
        (
            pair["baseline"]["measurement"]["wall_seconds"]
            - pair["candidate"]["measurement"]["wall_seconds"]
        )
        / pair["baseline"]["measurement"]["wall_seconds"]
        * 100
        for pair in pairs
    ]
    median_improvement = statistics.median(paired_improvements)
    wins = sum(value > 0 for value in paired_improvements)
    baseline_p95 = summary["baseline"]["wall_seconds"]["p95"]
    candidate_p95 = summary["candidate"]["wall_seconds"]["p95"]
    p95_change = (candidate_p95 - baseline_p95) / baseline_p95 * 100
    baseline_calls = summary["baseline"]["kubectl_call_counts"]
    candidate_calls = summary["candidate"]["kubectl_call_counts"]
    stable_call_reduction = (
        len(baseline_calls) == len(candidate_calls) == 1
        and candidate_calls[0] < baseline_calls[0]
    )
    criteria: dict[str, bool] = {}
    if minimum_improvement is not None:
        criteria["minimum_median_improvement"] = (
            median_improvement >= minimum_improvement
        )
    if minimum_faster_pairs is not None:
        criteria["minimum_faster_pairs"] = wins >= minimum_faster_pairs
    if maximum_p95_regression is not None:
        criteria["maximum_p95_regression"] = p95_change <= maximum_p95_regression
    acceptance = (
        "NOT_CONFIGURED"
        if not criteria
        else "PASS"
        if all(criteria.values())
        else "BLOCKED"
    )
    return {
        "acceptance": acceptance,
        "measured_direction": (
            "CANDIDATE_FASTER"
            if median_improvement > 0
            else "BASELINE_FASTER"
            if median_improvement < 0
            else "NO_DIFFERENCE"
        ),
        "paired_improvement_percent": _summary(paired_improvements),
        "candidate_faster_pairs": wins,
        "p95_change_percent": p95_change,
        "stable_kubectl_call_reduction": stable_call_reduction,
        "thresholds": {
            "minimum_median_improvement_percent": minimum_improvement,
            "minimum_faster_pairs": minimum_faster_pairs,
            "maximum_p95_regression_percent": maximum_p95_regression,
        },
        "threshold_results": criteria,
    }


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


def _format_seconds(value: float) -> str:
    return f"{value:.3f} s"


def _change(baseline: float, candidate: float, *, lower_is_better: bool = True) -> str:
    percent = (candidate - baseline) / baseline * 100 if baseline else 0.0
    if lower_is_better:
        return f"{-percent:+.1f}% improvement"
    return f"{percent:+.1f}%"


def _markdown(evidence: dict[str, Any]) -> str:
    base = evidence.get("summary", {}).get("baseline", {})
    candidate = evidence.get("summary", {}).get("candidate", {})
    correctness = evidence.get("correctness", {})
    performance = evidence.get("performance", {})
    lines = [
        "## Event trace A/B evidence",
        "",
        "| Field | Baseline | Candidate | Result |",
        "| --- | --- | --- | --- |",
        f"| Exact SHA | `{evidence['revisions']['baseline']}` | `{evidence['revisions']['candidate']}` | {'planned' if evidence.get('status') == 'DRY_RUN' else 'pinned'} |",
        f"| Samples | {base.get('sample_count', 0)} | {candidate.get('sample_count', 0)} | warmups excluded |",
    ]
    if base and candidate:
        base_wall = base["wall_seconds"]
        candidate_wall = candidate["wall_seconds"]
        base_rss = base["max_process_rss_kib"]
        candidate_rss = candidate["max_process_rss_kib"]
        lines += [
            f"| Wall p50 | {_format_seconds(base_wall['p50'])} | {_format_seconds(candidate_wall['p50'])} | {_change(base_wall['p50'], candidate_wall['p50'])} |",
            f"| Wall p95 | {_format_seconds(base_wall['p95'])} | {_format_seconds(candidate_wall['p95'])} | {_change(base_wall['p95'], candidate_wall['p95'])} |",
            f"| Wall CV | {base_wall['cv_percent']:.1f}% | {candidate_wall['cv_percent']:.1f}% | variance read-back |",
            f"| Max process RSS p50 | {base_rss['p50']:.0f} KiB | {candidate_rss['p50']:.0f} KiB | {_change(base_rss['p50'], candidate_rss['p50'])} |",
            f"| Record counts | `{base['record_counts']}` | `{candidate['record_counts']}` | {'PASS' if correctness.get('record_counts_equal') else 'BLOCKED'} |",
            f"| Semantic SHA-256 | `{base['semantic_sha256'][0] if len(base['semantic_sha256']) == 1 else 'unstable'}` | `{candidate['semantic_sha256'][0] if len(candidate['semantic_sha256']) == 1 else 'unstable'}` | {'PASS' if correctness.get('semantic_equal') else 'BLOCKED'} |",
            f"| Collector errors | `{base['error_counts']}` | `{candidate['error_counts']}` | {'PASS' if correctness.get('no_errors') else 'BLOCKED'} |",
            f"| Kubernetes calls | `{base['kubectl_call_counts']}` | `{candidate['kubectl_call_counts']}` | {'PASS' if performance.get('stable_kubectl_call_reduction') else 'BLOCKED'} |",
            f"| Faster sample pairs | n/a | {performance.get('candidate_faster_pairs', 0)}/{base.get('sample_count', 0)} | observed |",
        ]
    lines += [
        f"| Correctness gate | n/a | n/a | **{correctness.get('status', 'NOT_RUN')}** |",
        f"| Performance acceptance | n/a | n/a | **{performance.get('acceptance', 'NOT_CONFIGURED')}** |",
        f"| Overall | n/a | n/a | **{evidence.get('status', 'BLOCKED')}** |",
        "",
        "Environment and limits:",
        "",
        f"- Executor: `{evidence.get('environment', {}).get('executor_label', 'unknown')}` in Kubernetes namespace `{evidence.get('environment', {}).get('kubernetes_namespace') or 'unknown'}`.",
        f"- Window: `{evidence.get('workload', {}).get('from')}` through `{evidence.get('workload', {}).get('to')}`.",
        (
            "- No measurements were run; this artifact records the planned boundary."
            if evidence.get("status") == "DRY_RUN"
            else "- Measurements cover `collect_events()` with the real selected target and credential sources. Preflight access time is excluded."
        ),
        "- A PASS is live evidence for this executor, target, data window, and exact SHA pair only.",
        "",
    ]
    return "\n".join(lines)


def _write(path: Path, body: str) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_text(body, encoding="utf-8")
    os.replace(temporary, path)


def _blocked(arguments: argparse.Namespace, reason: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": "event_trace_ab_benchmark",
        "status": "BLOCKED",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "revisions": {
            "baseline": arguments.baseline_sha.lower(),
            "candidate": arguments.candidate_sha.lower(),
        },
        "environment": _environment(arguments.executor_label),
        "workload": {"from": arguments.from_time, "to": arguments.to_time},
    }


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    if platform.system() != "Linux" or not os.environ.get("KUBERNETES_SERVICE_HOST"):
        return _blocked(arguments, "authorized_kubernetes_runner_required")
    baseline_root = Path(arguments.baseline_root).resolve()
    candidate_root = Path(arguments.candidate_root).resolve()
    target = Path(arguments.target).resolve()
    try:
        _check_source(baseline_root, arguments.baseline_sha)
        _check_source(candidate_root, arguments.candidate_sha)
    except RuntimeError as exc:
        return _blocked(arguments, str(exc))
    variants = {
        "baseline": (baseline_root, arguments.baseline_sha.lower()),
        "candidate": (candidate_root, arguments.candidate_sha.lower()),
    }
    preflights = {
        name: _worker(
            "preflight",
            root=root,
            revision=revision,
            target=target,
            from_time=None,
            to_time=None,
            timeout=arguments.sample_timeout_seconds,
        )
        for name, (root, revision) in variants.items()
    }
    if any(item.get("status") != "PASS" for item in preflights.values()):
        evidence = _blocked(arguments, "live_access_preflight_failed")
        evidence["preflights"] = preflights
        return evidence
    if not _same_identity(preflights):
        evidence = _blocked(arguments, "target_or_credential_source_mismatch")
        evidence["preflights"] = preflights
        return evidence

    def measured(variant: str) -> dict[str, Any]:
        root, revision = variants[variant]
        payload = _worker(
            "sample",
            root=root,
            revision=revision,
            target=target,
            from_time=arguments.from_time,
            to_time=arguments.to_time,
            timeout=arguments.sample_timeout_seconds,
        )
        payload["variant"] = variant
        return payload

    warmups: list[dict[str, Any]] = []
    for index in range(arguments.warmups):
        order = (
            ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
        )
        for variant in order:
            warmups.append(measured(variant))
    if any(item.get("status") != "PASS" for item in warmups):
        evidence = _blocked(arguments, "warmup_failed")
        evidence["preflights"] = preflights
        evidence["warmups"] = warmups
        return evidence

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
        evidence = _blocked(arguments, "measurement_failed")
        evidence["preflights"] = preflights
        evidence["samples"] = raw_samples
        return evidence
    grouped = {
        name: [pair[name] for pair in pairs] for name in ("baseline", "candidate")
    }
    summary = {name: _variant_summary(items) for name, items in grouped.items()}
    correctness = _correctness(summary)
    performance = _performance(
        pairs,
        summary,
        minimum_improvement=arguments.minimum_improvement_percent,
        minimum_faster_pairs=arguments.minimum_faster_pairs,
        maximum_p95_regression=arguments.maximum_p95_regression_percent,
    )
    acceptance = performance["acceptance"]
    status = "PASS" if correctness["status"] == "PASS" else "BLOCKED"
    if acceptance == "BLOCKED":
        status = "BLOCKED"
    return {
        "schema_version": "1.0",
        "kind": "event_trace_ab_benchmark",
        "status": status,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "revisions": {
            "baseline": arguments.baseline_sha.lower(),
            "candidate": arguments.candidate_sha.lower(),
        },
        "environment": _environment(arguments.executor_label),
        "workload": {
            "from": arguments.from_time,
            "to": arguments.to_time,
            "warmups": arguments.warmups,
            "samples_per_variant": arguments.samples,
            "order": "alternating paired A/B then B/A",
            "boundary": "collect_events; access preflight excluded",
        },
        "preflights": preflights,
        "correctness": correctness,
        "performance": performance,
        "summary": summary,
        "samples": raw_samples,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark exact baseline and candidate event collectors live.",
        epilog=(
            "Run only in an authorized Linux Kubernetes Job or Kubernetes-backed "
            "CI runner with the same target and effective credentials mounted."
        ),
    )
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
    parser.add_argument("--minimum-improvement-percent", type=float, metavar="PERCENT")
    parser.add_argument("--minimum-faster-pairs", type=int, metavar="N")
    parser.add_argument(
        "--maximum-p95-regression-percent",
        type=float,
        metavar="PERCENT",
    )
    parser.add_argument("--json-out", required=True, metavar="PATH")
    parser.add_argument("--markdown-out", required=True, metavar="PATH")
    parser.add_argument(
        "--dry-run", action="store_true", help="write the benchmark plan only"
    )
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.dry_run:
        evidence = {
            **_blocked(arguments, "benchmark_not_executed"),
            "status": "DRY_RUN",
            "plan": {
                "warmups": arguments.warmups,
                "samples_per_variant": arguments.samples,
                "sample_timeout_seconds": arguments.sample_timeout_seconds,
                "thresholds": {
                    "minimum_median_improvement_percent": arguments.minimum_improvement_percent,
                    "minimum_faster_pairs": arguments.minimum_faster_pairs,
                    "maximum_p95_regression_percent": arguments.maximum_p95_regression_percent,
                },
            },
        }
    elif arguments.warmups < 1 or arguments.samples < 5:
        evidence = _blocked(
            arguments, "warmups_must_be_at_least_1_and_samples_at_least_5"
        )
    elif arguments.minimum_faster_pairs is not None and not (
        0 <= arguments.minimum_faster_pairs <= arguments.samples
    ):
        evidence = _blocked(arguments, "minimum_faster_pairs_out_of_range")
    elif arguments.sample_timeout_seconds <= 0:
        evidence = _blocked(arguments, "sample_timeout_must_be_positive")
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
