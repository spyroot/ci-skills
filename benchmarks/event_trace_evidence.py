"""Validate and summarize bounded event-trace A/B evidence."""

from __future__ import annotations

import math
import statistics
from typing import Any

MIN_WARMUPS = 1
MAX_WARMUPS = 3
MIN_SAMPLES = 5
MAX_SAMPLES = 20
MAX_SAMPLE_TIMEOUT_SECONDS = 300
MAX_TOTAL_TIMEOUT_SECONDS = 1800

MEASUREMENT_FIELDS = {
    "wall_seconds",
    "cpu_seconds",
    "collector_json_bytes",
    "record_count",
    "error_count",
    "kubectl_calls",
    "semantic_sha256",
    "strict_sha256",
}


class EvidenceError(ValueError):
    """Worker output or benchmark evidence violated its stable contract."""


def validate_limits(arguments: Any) -> str | None:
    """Return a stable reason when the live-read budget is invalid."""
    if not MIN_WARMUPS <= arguments.warmups <= MAX_WARMUPS:
        return "warmups_out_of_range"
    if not MIN_SAMPLES <= arguments.samples <= MAX_SAMPLES:
        return "samples_out_of_range"
    if not 1 <= arguments.sample_timeout_seconds <= MAX_SAMPLE_TIMEOUT_SECONDS:
        return "sample_timeout_out_of_range"
    if not 1 <= arguments.total_timeout_seconds <= MAX_TOTAL_TIMEOUT_SECONDS:
        return "total_timeout_out_of_range"
    if arguments.minimum_faster_pairs is not None and not (
        0 <= arguments.minimum_faster_pairs <= arguments.samples
    ):
        return "minimum_faster_pairs_out_of_range"
    return None


def validate_worker_payload(operation: str, payload: Any) -> dict[str, Any]:
    """Validate the complete worker envelope before orchestration consumes it."""
    if not isinstance(payload, dict):
        raise EvidenceError("benchmark_worker_non_object_json")
    if payload.get("schema_version") != "1.0":
        raise EvidenceError("benchmark_worker_schema_invalid")
    expected_kind = (
        "event_trace_benchmark_preflight"
        if operation == "preflight"
        else "event_trace_benchmark_sample"
    )
    status = payload.get("status")
    if status == "BLOCKED":
        if not isinstance(payload.get("reason"), str):
            raise EvidenceError("benchmark_worker_blocked_reason_missing")
        return payload
    if payload.get("kind") != expected_kind:
        raise EvidenceError("benchmark_worker_kind_invalid")
    if operation == "preflight":
        if status != "PASS":
            raise EvidenceError("benchmark_worker_preflight_status_invalid")
        required = ("source", "identity", "access", "harness")
        if any(not isinstance(payload.get(key), dict) for key in required):
            raise EvidenceError("benchmark_worker_preflight_fields_invalid")
        return payload
    if operation != "sample" or status not in {"PASS", "PARTIAL"}:
        raise EvidenceError("benchmark_worker_sample_status_invalid")
    required = ("source", "identity", "measurement", "harness")
    if any(not isinstance(payload.get(key), dict) for key in required):
        raise EvidenceError("benchmark_worker_sample_fields_invalid")
    measurement = payload["measurement"]
    if not MEASUREMENT_FIELDS <= measurement.keys():
        raise EvidenceError("benchmark_worker_measurement_fields_missing")
    numeric = (
        "wall_seconds",
        "cpu_seconds",
        "collector_json_bytes",
        "record_count",
        "error_count",
        "kubectl_calls",
    )
    if any(
        isinstance(measurement[key], bool)
        or not isinstance(measurement[key], (int, float))
        or not math.isfinite(float(measurement[key]))
        or float(measurement[key]) < 0
        for key in numeric
    ):
        raise EvidenceError("benchmark_worker_measurement_values_invalid")
    if measurement["wall_seconds"] <= 0:
        raise EvidenceError("benchmark_worker_wall_time_invalid")
    for key in ("semantic_sha256", "strict_sha256"):
        value = measurement[key]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value.lower())
        ):
            raise EvidenceError("benchmark_worker_digest_invalid")
    return payload


def same_preflight_identity(preflights: dict[str, dict[str, Any]]) -> bool:
    baseline = preflights["baseline"]
    candidate = preflights["candidate"]
    return (
        baseline.get("identity") == candidate.get("identity")
        and baseline.get("identity") is not None
        and baseline.get("access") == candidate.get("access")
    )


def sample_matches_preflight(preflight: dict[str, Any], sample: dict[str, Any]) -> bool:
    """Require target, credentials, exact source, and harness to remain pinned."""
    return all(
        sample.get(key) == preflight.get(key)
        for key in ("identity", "source", "harness")
    )


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summary(values: list[float]) -> dict[str, float]:
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


def variant_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [sample["measurement"] for sample in samples]
    return {
        "sample_count": len(samples),
        "wall_seconds": summary([item["wall_seconds"] for item in metrics]),
        "cpu_seconds": summary([item["cpu_seconds"] for item in metrics]),
        "collector_json_bytes": summary(
            [float(item["collector_json_bytes"]) for item in metrics]
        ),
        "record_counts": sorted({item["record_count"] for item in metrics}),
        "error_counts": sorted({item["error_count"] for item in metrics}),
        "kubectl_call_counts": sorted({item["kubectl_calls"] for item in metrics}),
        "semantic_sha256": sorted({item["semantic_sha256"] for item in metrics}),
        "strict_sha256": sorted({item["strict_sha256"] for item in metrics}),
        "statuses": sorted({sample.get("status") for sample in samples}),
    }


def correctness(summaries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    baseline = summaries["baseline"]
    candidate = summaries["candidate"]
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
    passed = all(
        (
            stable_baseline,
            stable_candidate,
            semantic_equal,
            record_counts_equal,
            no_errors,
            statuses_pass,
        )
    )
    return {
        "status": "PASS" if passed else "BLOCKED",
        "baseline_stable": stable_baseline,
        "candidate_stable": stable_candidate,
        "semantic_equal": semantic_equal,
        "record_counts_equal": record_counts_equal,
        "no_errors": no_errors,
        "statuses_pass": statuses_pass,
        "strict_equal": baseline["strict_sha256"] == candidate["strict_sha256"],
    }


def performance(
    pairs: list[dict[str, dict[str, Any]]],
    summaries: dict[str, dict[str, Any]],
    *,
    minimum_improvement: float | None,
    minimum_faster_pairs: int | None,
    maximum_p95_regression: float | None,
) -> dict[str, Any]:
    paired = [
        (
            pair["baseline"]["measurement"]["wall_seconds"]
            - pair["candidate"]["measurement"]["wall_seconds"]
        )
        / pair["baseline"]["measurement"]["wall_seconds"]
        * 100
        for pair in pairs
    ]
    median_improvement = statistics.median(paired)
    wins = sum(value > 0 for value in paired)
    baseline_p95 = summaries["baseline"]["wall_seconds"]["p95"]
    candidate_p95 = summaries["candidate"]["wall_seconds"]["p95"]
    p95_change = (candidate_p95 - baseline_p95) / baseline_p95 * 100
    baseline_calls = summaries["baseline"]["kubectl_call_counts"]
    candidate_calls = summaries["candidate"]["kubectl_call_counts"]
    call_reduction = (
        len(baseline_calls) == len(candidate_calls) == 1
        and candidate_calls[0] < baseline_calls[0]
    )
    thresholds: dict[str, bool] = {}
    if minimum_improvement is not None:
        thresholds["minimum_median_improvement"] = (
            median_improvement >= minimum_improvement
        )
    if minimum_faster_pairs is not None:
        thresholds["minimum_faster_pairs"] = wins >= minimum_faster_pairs
    if maximum_p95_regression is not None:
        thresholds["maximum_p95_regression"] = p95_change <= maximum_p95_regression
    required = {
        "candidate_median_faster": median_improvement > 0,
        "stable_kubectl_call_reduction": call_reduction,
        "thresholds_configured": bool(thresholds),
    }
    accepted = all(required.values()) and all(thresholds.values())
    return {
        "acceptance": "PASS" if accepted else "BLOCKED",
        "measured_direction": (
            "CANDIDATE_FASTER"
            if median_improvement > 0
            else "BASELINE_FASTER"
            if median_improvement < 0
            else "NO_DIFFERENCE"
        ),
        "paired_improvement_percent": summary(paired),
        "candidate_faster_pairs": wins,
        "p95_change_percent": p95_change,
        "stable_kubectl_call_reduction": call_reduction,
        "thresholds": {
            "minimum_median_improvement_percent": minimum_improvement,
            "minimum_faster_pairs": minimum_faster_pairs,
            "maximum_p95_regression_percent": maximum_p95_regression,
        },
        "required_results": required,
        "threshold_results": thresholds,
    }
