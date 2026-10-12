"""Validate and summarize bounded event-trace A/B evidence.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import math
import statistics
from argparse import Namespace
from collections.abc import Mapping, MutableMapping, Sequence
from typing import TYPE_CHECKING, Optional, TypeAlias, TypedDict, Union, cast

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue

Number: TypeAlias = Union[int, float]


class WorkerMeasurement(TypedDict):
    """Validated observations from one exact collector invocation.

    :param wall_seconds: Positive elapsed duration of the collector call.
    :param cpu_seconds: Nonnegative process and child CPU time.
    :param collector_json_bytes: Nonnegative byte count of compact collector JSON.
    :param record_count: Nonnegative number of reported event records.
    :param error_count: Nonnegative number of reported collection errors.
    :param kubectl_calls: Nonnegative counted native kubectl invocations.
    :param semantic_sha256: Digest of compared semantic report fields.
    :param strict_sha256: Digest of the full report without capture time.

    .. attribute :: wall_seconds
        :type: Number

        Positive elapsed duration of the collector call.

    .. attribute :: cpu_seconds
        :type: Number

        Nonnegative process and child CPU time.

    .. attribute :: collector_json_bytes
        :type: Number

        Nonnegative byte count of compact collector JSON.

    .. attribute :: record_count
        :type: Number

        Nonnegative number of reported event records.

    .. attribute :: error_count
        :type: Number

        Nonnegative number of reported collection errors.

    .. attribute :: kubectl_calls
        :type: Number

        Nonnegative counted native kubectl invocations.

    .. attribute :: semantic_sha256
        :type: str

        Digest of compared semantic report fields.

    .. attribute :: strict_sha256
        :type: str

        Digest of the full report without capture time.
    """

    wall_seconds: Number
    cpu_seconds: Number
    collector_json_bytes: Number
    record_count: Number
    error_count: Number
    kubectl_calls: Number
    semantic_sha256: str
    strict_sha256: str


class WorkerPayload(TypedDict, total=False):
    """Worker envelope whose populated fields depend on the declared operation.

    :param schema_version: Evidence contract version when emitted by a worker.
    :param kind: Preflight, sample, plan, or worker-failure kind.
    :param status: PASS, PARTIAL, DRY_RUN, or BLOCKED operation result.
    :param reason: Stable failure category for a blocked operation.
    :param stderr_sha256: Digest of malformed worker stderr captured by the harness.
    :param detail_sha256: Digest of private exception detail retained by the worker.
    :param source: Measured variant skill provenance.
    :param identity: Target and credential-source fingerprints.
    :param access: Per-surface identities and statuses from preflight.
    :param harness: Exact clean benchmark harness source identity.
    :param measurement: Validated collector timings, counts, and digests for samples.
    :param variant: Baseline or candidate label added by the harness.
    :param pair: One-based paired-sample index added by the harness.
    :param operation: Planned worker operation.
    :param revision: Planned variant revision.
    :param harness_revision: Planned harness revision.
    :param fixed_time_bounds: Whether both planned time-window endpoints are present.

    .. attribute :: schema_version
        :type: str

        Evidence contract version when emitted by a worker.

    .. attribute :: kind
        :type: str

        Preflight, sample, plan, or worker-failure kind.

    .. attribute :: status
        :type: str

        PASS, PARTIAL, DRY_RUN, or BLOCKED operation result.

    .. attribute :: reason
        :type: str

        Stable failure category for a blocked operation.

    .. attribute :: stderr_sha256
        :type: str

        Digest of malformed worker stderr captured by the harness.

    .. attribute :: detail_sha256
        :type: str

        Digest of private exception detail retained by the worker.

    .. attribute :: source
        :type: JSONObject

        Measured variant skill provenance.

    .. attribute :: identity
        :type: JSONObject

        Target and credential-source fingerprints.

    .. attribute :: access
        :type: JSONObject

        Per-surface identities and statuses from preflight.

    .. attribute :: harness
        :type: JSONObject

        Exact clean benchmark harness source identity.

    .. attribute :: measurement
        :type: WorkerMeasurement

        Validated collector timings, counts, and digests for samples.

    .. attribute :: variant
        :type: str

        Baseline or candidate label added by the harness.

    .. attribute :: pair
        :type: int

        One-based paired-sample index added by the harness.

    .. attribute :: operation
        :type: str

        Planned worker operation.

    .. attribute :: revision
        :type: str

        Planned variant revision.

    .. attribute :: harness_revision
        :type: str

        Planned harness revision.

    .. attribute :: fixed_time_bounds
        :type: bool

        Whether both planned time-window endpoints are present.
    """

    schema_version: str
    kind: str
    status: str
    reason: str
    stderr_sha256: str
    detail_sha256: str
    source: JSONObject
    identity: JSONObject
    access: JSONObject
    harness: JSONObject
    measurement: WorkerMeasurement
    variant: str
    pair: int
    operation: str
    revision: str
    harness_revision: str
    fixed_time_bounds: bool


class MetricSummary(TypedDict):
    """Distribution of one metric across a variant's measured samples.

    :param min: Smallest observation.
    :param p50: Median observation.
    :param mean: Arithmetic mean.
    :param p95: Linearly interpolated 95th percentile.
    :param max: Largest observation.
    :param stdev: Sample standard deviation, zero for one observation.
    :param cv_percent: Standard deviation divided by mean, in percent.

    .. attribute :: min
        :type: float

        Smallest observation.

    .. attribute :: p50
        :type: float

        Median observation.

    .. attribute :: mean
        :type: float

        Arithmetic mean.

    .. attribute :: p95
        :type: float

        Linearly interpolated 95th percentile.

    .. attribute :: max
        :type: float

        Largest observation.

    .. attribute :: stdev
        :type: float

        Sample standard deviation, zero for one observation.

    .. attribute :: cv_percent
        :type: float

        Standard deviation divided by mean, in percent.
    """

    min: float
    p50: float
    mean: float
    p95: float
    max: float
    stdev: float
    cv_percent: float


class VariantSummary(TypedDict):
    """Measured distributions and distinct correctness evidence for one variant.

    :param sample_count: Number of post-warmup samples summarized.
    :param wall_seconds: Distribution of collector elapsed durations.
    :param cpu_seconds: Distribution of combined worker and child CPU durations.
    :param collector_json_bytes: Distribution of compact report sizes.
    :param record_counts: Sorted distinct event counts.
    :param error_counts: Sorted distinct collection error counts.
    :param kubectl_call_counts: Sorted distinct native call counts.
    :param semantic_sha256: Sorted distinct semantic report digests.
    :param strict_sha256: Sorted distinct strict report digests.
    :param statuses: Sorted distinct collection statuses.

    .. attribute :: sample_count
        :type: int

        Number of post-warmup samples summarized.

    .. attribute :: wall_seconds
        :type: MetricSummary

        Distribution of collector elapsed durations.

    .. attribute :: cpu_seconds
        :type: MetricSummary

        Distribution of combined worker and child CPU durations.

    .. attribute :: collector_json_bytes
        :type: MetricSummary

        Distribution of compact report sizes.

    .. attribute :: record_counts
        :type: Sequence[Number]

        Sorted distinct event counts.

    .. attribute :: error_counts
        :type: Sequence[Number]

        Sorted distinct collection error counts.

    .. attribute :: kubectl_call_counts
        :type: Sequence[Number]

        Sorted distinct native call counts.

    .. attribute :: semantic_sha256
        :type: Sequence[str]

        Sorted distinct semantic report digests.

    .. attribute :: strict_sha256
        :type: Sequence[str]

        Sorted distinct strict report digests.

    .. attribute :: statuses
        :type: Sequence[str]

        Sorted distinct collection statuses.
    """

    sample_count: int
    wall_seconds: MetricSummary
    cpu_seconds: MetricSummary
    collector_json_bytes: MetricSummary
    record_counts: Sequence[Number]
    error_counts: Sequence[Number]
    kubectl_call_counts: Sequence[Number]
    semantic_sha256: Sequence[str]
    strict_sha256: Sequence[str]
    statuses: Sequence[str]


class CorrectnessEvidence(TypedDict):
    """Original semantic acceptance predicates evaluated for the sample pair set.

    :param status: PASS only when all required correctness predicates hold.
    :param baseline_stable: Whether baseline has one semantic digest.
    :param candidate_stable: Whether candidate has one semantic digest.
    :param semantic_equal: Whether both stable semantic digests match.
    :param record_counts_equal: Whether one baseline event count matches candidate.
    :param no_errors: Whether both variants report only zero errors.
    :param statuses_pass: Whether every sample has PASS collection status.
    :param strict_equal: Whether distinct strict-digest sets match.

    .. attribute :: status
        :type: str

        PASS only when all required correctness predicates hold.

    .. attribute :: baseline_stable
        :type: bool

        Whether baseline has one semantic digest.

    .. attribute :: candidate_stable
        :type: bool

        Whether candidate has one semantic digest.

    .. attribute :: semantic_equal
        :type: bool

        Whether both stable semantic digests match.

    .. attribute :: record_counts_equal
        :type: bool

        Whether one baseline event count matches candidate.

    .. attribute :: no_errors
        :type: bool

        Whether both variants report only zero errors.

    .. attribute :: statuses_pass
        :type: bool

        Whether every sample has PASS collection status.

    .. attribute :: strict_equal
        :type: bool

        Whether distinct strict-digest sets match.
    """

    status: str
    baseline_stable: bool
    candidate_stable: bool
    semantic_equal: bool
    record_counts_equal: bool
    no_errors: bool
    statuses_pass: bool
    strict_equal: bool


class PerformanceEvidence(TypedDict):
    """Original paired performance acceptance and its supporting measurements.

    :param acceptance: PASS only when mandatory predicates and thresholds hold.
    :param measured_direction: Sign of median paired improvement.
    :param paired_improvement_percent: Distribution of paired improvement percentages.
    :param candidate_faster_pairs: Count of positive paired improvements.
    :param p95_change_percent: Candidate p95 regression relative to baseline.
    :param stable_kubectl_call_reduction: Whether candidate uses fewer consistent calls.
    :param thresholds: Caller-selected optional acceptance thresholds.
    :param required_results: Mandatory performance predicate outcomes.
    :param threshold_results: Outcomes for configured optional thresholds.

    .. attribute :: acceptance
        :type: str

        PASS only when mandatory predicates and thresholds hold.

    .. attribute :: measured_direction
        :type: str

        Sign of median paired improvement.

    .. attribute :: paired_improvement_percent
        :type: MetricSummary

        Distribution of paired improvement percentages.

    .. attribute :: candidate_faster_pairs
        :type: int

        Count of positive paired improvements.

    .. attribute :: p95_change_percent
        :type: float

        Candidate p95 regression relative to baseline.

    .. attribute :: stable_kubectl_call_reduction
        :type: bool

        Whether candidate uses fewer consistent calls.

    .. attribute :: thresholds
        :type: Mapping[str, Optional[Number]]

        Caller-selected optional acceptance thresholds.

    .. attribute :: required_results
        :type: Mapping[str, bool]

        Mandatory performance predicate outcomes.

    .. attribute :: threshold_results
        :type: Mapping[str, bool]

        Outcomes for configured optional thresholds.
    """

    acceptance: str
    measured_direction: str
    paired_improvement_percent: MetricSummary
    candidate_faster_pairs: int
    p95_change_percent: float
    stable_kubectl_call_reduction: bool
    thresholds: Mapping[str, Optional[Number]]
    required_results: Mapping[str, bool]
    threshold_results: Mapping[str, bool]


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


def validate_limits(arguments: Namespace) -> Optional[str]:
    """Check the bounded live-read budget for an A/B benchmark.

    :param arguments: Warmup/sample counts, per-sample and total timeouts, and
        optional minimum faster-pair threshold.
    :returns: Stable invalid-limit reason, or ``None`` when limits are allowed.
    """
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


def validate_worker_payload(operation: str, payload: JSONValue) -> WorkerPayload:
    """Validate an isolated worker result before the harness consumes it.

    :param operation: ``preflight`` or ``sample`` worker operation.
    :param payload: Decoded JSON result from the worker process.
    :returns: Original mutable payload after envelope, status, required fields, and
        sample measurement validation.
    :raises EvidenceError: The result kind, status, fields, numeric values, or
        digests violate the worker contract.
    """
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
        return cast(WorkerPayload, payload)
    if payload.get("kind") != expected_kind:
        raise EvidenceError("benchmark_worker_kind_invalid")
    if operation == "preflight":
        if status != "PASS":
            raise EvidenceError("benchmark_worker_preflight_status_invalid")
        required = ("source", "identity", "access", "harness")
        if any(not isinstance(payload.get(key), Mapping) for key in required):
            raise EvidenceError("benchmark_worker_preflight_fields_invalid")
        return cast(WorkerPayload, payload)
    if (
        operation != "sample"
        or not isinstance(status, str)
        or status not in {"PASS", "PARTIAL"}
    ):
        raise EvidenceError("benchmark_worker_sample_status_invalid")
    required = ("source", "identity", "measurement", "harness")
    if any(not isinstance(payload.get(key), Mapping) for key in required):
        raise EvidenceError("benchmark_worker_sample_fields_invalid")
    measurement = payload["measurement"]
    if not isinstance(measurement, Mapping):
        raise EvidenceError("benchmark_worker_sample_fields_invalid")
    validate_measurement(measurement)
    return cast(WorkerPayload, payload)


def validate_measurement(measurement: JSONObject) -> WorkerMeasurement:
    """Validate every count, duration, and digest before statistical operations.

    :param measurement: Decoded measurement object from a sample envelope.
    :returns: The same mapping with the established measurement field contract.
    :raises EvidenceError: If required fields are missing, numbers are Boolean,
        nonfinite or negative, wall time is zero, or a digest is not 64 hex digits.
    """
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
    for key in numeric:
        value = measurement[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EvidenceError("benchmark_worker_measurement_values_invalid")
        try:
            valid = math.isfinite(float(value)) and value >= 0
        except OverflowError:
            valid = False
        if not valid:
            raise EvidenceError("benchmark_worker_measurement_values_invalid")
    wall_seconds = measurement["wall_seconds"]
    if not isinstance(wall_seconds, (int, float)) or wall_seconds <= 0:
        raise EvidenceError("benchmark_worker_wall_time_invalid")
    for key in ("semantic_sha256", "strict_sha256"):
        value = measurement[key]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value.lower())
        ):
            raise EvidenceError("benchmark_worker_digest_invalid")
    return cast(WorkerMeasurement, measurement)


def same_preflight_identity(preflights: Mapping[str, WorkerPayload]) -> bool:
    """Compare baseline and candidate preflight target and access identity.

    :param preflights: Named ``baseline`` and ``candidate`` preflight results.
    :returns: Whether their nonempty target identities and access records match.
    :raises KeyError: If the named baseline or candidate preflight is absent.
    """  # noqa: DOC502 - Required variant fields use direct mapping access.
    baseline = preflights["baseline"]
    candidate = preflights["candidate"]
    return (
        baseline.get("identity") == candidate.get("identity")
        and baseline.get("identity") is not None
        and baseline.get("access") == candidate.get("access")
    )


def sample_matches_preflight(preflight: WorkerPayload, sample: WorkerPayload) -> bool:
    """Detect target, source, credential, or harness drift after preflight.

    :param preflight: Passed identity result for one variant.
    :param sample: Measured result for that same variant.
    :returns: Whether identity, source, and harness fields still match exactly.
    """
    return all(
        sample.get(key) == preflight.get(key)
        for key in ("identity", "source", "harness")
    )


def _percentile(values: Sequence[float], percentile: float) -> float:
    """Interpolate an ordered percentile from observed sample values.

    :param values: Nonempty numeric samples.
    :param percentile: Fraction between zero and one selected by the caller.
    :returns: Linearly interpolated percentile value.
    :raises IndexError: ``values`` is empty.
    """  # noqa: DOC502 - Percentile interpolation indexes the ordered samples.
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summary(values: Sequence[float]) -> MetricSummary:
    """Summarize a nonempty set of benchmark measurements.

    :param values: Numeric samples from one variant and metric.
    :returns: Minimum, median, mean, p95, maximum, sample deviation, and
        coefficient of variation percent.
    :raises statistics.StatisticsError: If values is empty and no mean exists.
    """  # noqa: DOC502 - Statistics functions reject an empty sample set.
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


def variant_summary(samples: Sequence[WorkerPayload]) -> VariantSummary:
    """Summarize one variant's worker measurement records.

    :param samples: Successful sample results containing measurement fields.
    :returns: Sample count, distributions for time/bytes, distinct record and
        error counts, command counts, digests, and statuses.
    :raises KeyError: If a sample lacks a required measurement field.
    :raises statistics.StatisticsError: If no samples are available to summarize.
    """  # noqa: DOC502 - Field access, statistics, and ratio arithmetic can fail.
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
        "statuses": sorted({sample["status"] for sample in samples}),
    }


def correctness(summaries: Mapping[str, VariantSummary]) -> CorrectnessEvidence:
    """Compare baseline and candidate semantic correctness evidence.

    :param summaries: Variant summaries keyed ``baseline`` and ``candidate``.
    :returns: PASS/BLOCKED decision plus stable semantic digest, record count,
        zero-error, status, and strict-digest comparison fields.
    :raises KeyError: A required variant or summary field is absent.
    """  # noqa: DOC502 - Required variant fields use direct mapping access.
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
    pairs: Sequence[Mapping[str, WorkerPayload]],
    summaries: Mapping[str, VariantSummary],
    minimum_improvement: Optional[float],
    minimum_faster_pairs: Optional[int],
    maximum_p95_regression: Optional[float],
) -> PerformanceEvidence:
    """Evaluate paired wall-time change and stable command-call reduction.

    :param pairs: Matched baseline/candidate sample pairs.
    :param summaries: Per-variant p95 timing and command-count summaries.
    :param minimum_improvement: Optional minimum median paired improvement percent.
    :param minimum_faster_pairs: Optional minimum count of faster candidate pairs.
    :param maximum_p95_regression: Optional upper bound on candidate p95 change.
    :returns: PASS/BLOCKED acceptance, measured direction, paired distribution,
        faster-pair count, p95 change, call reduction, and threshold results.
    :raises ZeroDivisionError: A baseline wall time or p95 value is zero.
    :raises KeyError: If a required pair or summary measurement is absent.
    :raises statistics.StatisticsError: If there are no matched pairs to summarize.
    """  # noqa: DOC502 - Field access, statistics, and ratio arithmetic can fail.
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
    thresholds: MutableMapping[str, bool] = {}
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
