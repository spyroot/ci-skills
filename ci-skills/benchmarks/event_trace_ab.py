#!/usr/bin/env python3
"""Compare two exact event-trace implementations on one Kubernetes runner.

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
import socket
import subprocess
import sys
import time
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional, TypedDict, Unpack

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue

from event_trace_evidence import (
    CorrectnessEvidence,
    EvidenceError,
    PerformanceEvidence,
    VariantSummary,
    WorkerPayload,
    correctness,
    performance,
    same_preflight_identity,
    sample_matches_preflight,
    validate_limits,
    validate_worker_payload,
    variant_summary,
)
from source_identity import SourceIdentity, SourceIdentityError, source_identity


class BenchmarkDetails(TypedDict, total=False):
    """Evidence captured before a benchmark reaches its final acceptance decision.

    :param harness: Exact measured harness source identity.
    :param variant_sources: Exact baseline and candidate source identities.
    :param preflights: Worker preflights keyed by variant.
    :param warmups: Alternating warmup samples retained for a blocked warmup result.
    :param samples: Alternating measured samples retained for reporting or a block.

    .. attribute :: harness
        :type: SourceIdentity

        Exact measured harness source identity.

    .. attribute :: variant_sources
        :type: Mapping[str, SourceIdentity]

        Exact baseline and candidate source identities.

    .. attribute :: preflights
        :type: Mapping[str, WorkerPayload]

        Worker preflights keyed by variant.

    .. attribute :: warmups
        :type: Sequence[WorkerPayload]

        Alternating warmup samples retained for a blocked warmup result.

    .. attribute :: samples
        :type: Sequence[WorkerPayload]

        Alternating measured samples retained for reporting or a block.
    """

    harness: SourceIdentity
    variant_sources: Mapping[str, SourceIdentity]
    preflights: Mapping[str, WorkerPayload]
    warmups: Sequence[WorkerPayload]
    samples: Sequence[WorkerPayload]


class BenchmarkEvidence(BenchmarkDetails, total=False):
    """JSON evidence for a planned, completed, or blocked A/B benchmark.

    BenchmarkDetails documents the inherited source and worker observations.

    :param schema_version: Evidence contract version.
    :param kind: A/B benchmark report kind.
    :param status: PASS, DRY_RUN, or BLOCKED result.
    :param captured_at: UTC time when this report was constructed.
    :param reason: Stable failure reason, or None after successful acceptance.
    :param revisions: Requested exact harness, baseline, and candidate commits.
    :param environment: Executor identity and runtime metadata without raw hostname.
    :param workload: Fixed event window, sample budget, and measurement boundary.
    :param plan: Unexecuted operation budget and caller thresholds.
    :param correctness: Required semantic acceptance results.
    :param performance: Paired timing, call counts, and performance acceptance.
    :param summary: Per-variant metric and correctness summaries.

    .. attribute :: schema_version
        :type: str

        Evidence contract version.

    .. attribute :: kind
        :type: str

        A/B benchmark report kind.

    .. attribute :: status
        :type: str

        PASS, DRY_RUN, or BLOCKED result.

    .. attribute :: captured_at
        :type: str

        UTC time when this report was constructed.

    .. attribute :: reason
        :type: Optional[str]

        Stable failure reason, or None after successful acceptance.

    .. attribute :: revisions
        :type: Mapping[str, str]

        Requested exact harness, baseline, and candidate commits.

    .. attribute :: environment
        :type: JSONObject

        Executor identity and runtime metadata without raw hostname.

    .. attribute :: workload
        :type: JSONObject

        Fixed event window, sample budget, and measurement boundary.

    .. attribute :: plan
        :type: JSONObject

        Unexecuted operation budget and caller thresholds.

    .. attribute :: correctness
        :type: CorrectnessEvidence

        Required semantic acceptance results.

    .. attribute :: performance
        :type: PerformanceEvidence

        Paired timing, call counts, and performance acceptance.

    .. attribute :: summary
        :type: Mapping[str, VariantSummary]

        Per-variant metric and correctness summaries.
    """

    schema_version: str
    kind: str
    status: str
    captured_at: str
    reason: Optional[str]
    revisions: Mapping[str, str]
    environment: JSONObject
    workload: JSONObject
    plan: JSONObject
    correctness: CorrectnessEvidence
    performance: PerformanceEvidence
    summary: Mapping[str, VariantSummary]


class BenchmarkStopped(EvidenceError):
    """A required phase failed, with already collected evidence retained."""

    def __init__(self, reason: str, evidence: BenchmarkDetails):
        """Record a blocked phase and its reportable measurements.

        :param reason: Stable acceptance or identity failure category.
        :param evidence: Source, preflight, warmup, or sample fields collected so far.
        """
        super().__init__(reason)
        self.evidence = evidence


HARNESS_ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).with_name("event_trace_worker.py")


def _environment(executor_label: str) -> JSONObject:
    """Record the live benchmark executor without exposing its hostname.

    :param executor_label: Caller-supplied label for the authorized executor.
    :returns: Label, hashed hostname, platform, Python/CPU metadata, Kubernetes
        namespace if mounted, and detected CI provider.
    :raises OSError: If the mounted namespace file cannot be read.
    :raises UnicodeError: If the mounted namespace file contains invalid UTF-8.
    """  # noqa: DOC502 - Executor namespace reads may fail or contain invalid text.
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


def _blocked(arguments: argparse.Namespace, reason: str) -> BenchmarkEvidence:
    """Build a bounded benchmark failure envelope before measurements.

    :param arguments: Exact harness/variant revisions, executor, and time window.
    :param reason: Stable reason explaining why acceptance was blocked.
    :returns: Timestamped BLOCKED evidence with revision, environment, and
        workload identities.
    :raises OSError: If mounted executor namespace metadata cannot be read.
    :raises UnicodeError: If mounted namespace metadata is not valid UTF-8.
    """  # noqa: DOC502 - Executor namespace reads may fail or contain invalid text.
    return {
        "schema_version": "1.0",
        "kind": "event_trace_ab_benchmark",
        "status": "BLOCKED",
        "captured_at": datetime.now(UTC).isoformat(),
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
    from_time: Optional[str],
    to_time: Optional[str],
    timeout: float,
) -> WorkerPayload:
    """Call one isolated benchmark worker with a bounded timeout.

    :param operation: ``preflight`` or ``sample`` operation.
    :param root: Exact source checkout to load in the worker.
    :param revision: Full source commit SHA.
    :param harness_sha: Full harness commit SHA.
    :param target: Selected target TOML path passed to the worker.
    :param from_time: Fixed sample-window start, or ``None`` for preflight.
    :param to_time: Fixed sample-window end, or ``None`` for preflight.
    :param timeout: Maximum worker process runtime in seconds.
    :returns: Validated worker payload, or a BLOCKED record for timeout,
        malformed output, or nonzero exit without an explicit blocked status.
    :raises OSError: If the worker process cannot start.
    :raises UnicodeError: If worker stdout or stderr cannot be decoded as text.
    """  # noqa: DOC502 - Worker process creation and output decoding can fail.
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
        decoded: JSONValue = json.loads(result.stdout)
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
    """Bound a worker call by both sample and whole-benchmark deadlines.

    :param arguments: Per-sample timeout setting.
    :param deadline: Monotonic deadline for the whole benchmark.
    :returns: Positive remaining seconds capped by the sample bound, or zero
        after the total deadline.
    """
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
) -> WorkerPayload:
    """Dispatch a worker only while the total benchmark deadline remains.

    :param arguments: Harness SHA, fixed window, and timeout settings.
    :param deadline: Monotonic whole-benchmark deadline.
    :param operation: ``preflight`` or ``sample``.
    :param root: Variant source checkout.
    :param revision: Exact variant commit SHA.
    :param target: Selected target file shared across variants.
    :returns: Worker payload or a BLOCKED total-timeout record.
    :raises OSError: If the worker process cannot start.
    :raises UnicodeError: If worker stdout or stderr cannot be decoded as text.
    """  # noqa: DOC502 - Worker process creation and output decoding can fail.
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
    arguments: argparse.Namespace, reason: str, **evidence: Unpack[BenchmarkDetails]
) -> BenchmarkEvidence:
    """Attach measured evidence to a blocked benchmark record.

    :param arguments: Exact revisions, executor, and workload window.
    :param reason: Stable benchmark failure reason.
    :param **evidence: Declared BenchmarkDetails fields collected before a block.
    :returns: BLOCKED envelope augmented with supplied evidence fields.
    :raises OSError: If mounted executor namespace metadata cannot be read.
    :raises UnicodeError: If mounted namespace metadata is not valid UTF-8.
    """  # noqa: DOC502 - Executor namespace reads may fail or contain invalid text.
    return {**_blocked(arguments, reason), **evidence}


class BenchmarkRun:
    """Execute exact-source preflight, warmup, and paired measurement phases."""

    def __init__(self, arguments: argparse.Namespace):
        """Verify all source identities before creating the worker schedule.

        :param arguments: Exact revisions/roots, target, fixed window, and sample bounds.
        :raises SourceIdentityError: If a requested source tree is not exact and clean.
        :raises OSError: If source files cannot be read or Git cannot start.
        :raises subprocess.TimeoutExpired: If a source Git query exceeds its bound.
        """  # noqa: DOC502 - Exact source verification performs bounded Git/file reads.
        self.arguments = arguments
        baseline_root = Path(arguments.baseline_root).resolve()
        candidate_root = Path(arguments.candidate_root).resolve()
        self.target = Path(arguments.target).resolve()
        self.harness = source_identity(
            HARNESS_ROOT, arguments.harness_sha, HARNESS_ROOT / "benchmarks"
        )
        self.variant_sources = {
            "baseline": source_identity(
                baseline_root, arguments.baseline_sha, baseline_root / "ci-skills"
            ),
            "candidate": source_identity(
                candidate_root, arguments.candidate_sha, candidate_root / "ci-skills"
            ),
        }
        self.variants = {
            "baseline": (baseline_root, arguments.baseline_sha.lower()),
            "candidate": (candidate_root, arguments.candidate_sha.lower()),
        }
        self.deadline = time.monotonic() + arguments.total_timeout_seconds
        self.preflights: Mapping[str, WorkerPayload] = {}
        self.raw_samples: MutableSequence[WorkerPayload] = []

    def preflight(self) -> None:
        """Require live access and matching target/credential/harness identities.

        :raises BenchmarkStopped: If either access preflight fails or identities differ.
        :raises OSError: If a worker process cannot be started.
        :raises UnicodeError: If captured worker output cannot be decoded.
        """  # noqa: DOC503 - Worker launch and decoding failures propagate.
        self.preflights = {
            name: _call_worker(
                self.arguments, self.deadline, "preflight", root, revision, self.target
            )
            for name, (root, revision) in self.variants.items()
        }
        if any(item.get("status") != "PASS" for item in self.preflights.values()):
            raise BenchmarkStopped(
                "live_access_preflight_failed",
                {
                    "harness": self.harness,
                    "variant_sources": self.variant_sources,
                    "preflights": self.preflights,
                },
            )
        evidence: BenchmarkDetails = {
            "harness": self.harness,
            "preflights": self.preflights,
        }
        if any(
            item.get("harness") != self.harness for item in self.preflights.values()
        ):
            raise BenchmarkStopped("benchmark_harness_identity_mismatch", evidence)
        if not same_preflight_identity(self.preflights):
            raise BenchmarkStopped("target_or_credential_source_mismatch", evidence)

    def measured(self, variant: str) -> WorkerPayload:
        """Measure one named variant under the shared benchmark deadline.

        :param variant: Baseline or candidate name in the constructed schedule.
        :returns: Worker sample with its variant label added.
        :raises KeyError: If the requested variant is outside the two-source schedule.
        :raises OSError: If a worker process cannot be started.
        :raises UnicodeError: If captured worker output cannot be decoded.
        """  # noqa: DOC502 - Variant indexing and isolated worker execution can fail.
        root, revision = self.variants[variant]
        payload = _call_worker(
            self.arguments, self.deadline, "sample", root, revision, self.target
        )
        payload["variant"] = variant
        return payload

    @staticmethod
    def order(index: int) -> tuple[str, str]:
        """Alternate the first variant across warmups or sample pairs.

        :param index: Zero-based position within the current phase.
        :returns: Baseline/candidate for even positions, candidate/baseline for odd.
        """
        return (
            ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
        )

    def warmup(self) -> None:
        """Run the configured alternating warmups and verify identity stability.

        :raises BenchmarkStopped: If a warmup fails or drifts from its preflight identity.
        :raises OSError: If a worker process cannot be started.
        :raises UnicodeError: If captured worker output cannot be decoded.
        """  # noqa: DOC503 - Worker launch and decoding failures propagate.
        warmups = [
            self.measured(variant)
            for index in range(self.arguments.warmups)
            for variant in self.order(index)
        ]
        if any(
            item.get("status") != "PASS"
            or not sample_matches_preflight(self.preflights[item["variant"]], item)
            for item in warmups
        ):
            raise BenchmarkStopped(
                "warmup_failed_or_identity_drifted",
                {
                    "harness": self.harness,
                    "preflights": self.preflights,
                    "warmups": warmups,
                },
            )

    def samples(self) -> Sequence[Mapping[str, WorkerPayload]]:
        """Read matched pairs and require measurement and identity validity.

        :returns: Ordered baseline/candidate sample pairs after warmup.
        :raises BenchmarkStopped: If a measured worker fails or source/target identity drifts.
        :raises OSError: If a worker process cannot be started.
        :raises UnicodeError: If captured worker output cannot be decoded.
        """  # noqa: DOC503 - Worker launch and decoding failures propagate.
        pairs: MutableSequence[Mapping[str, WorkerPayload]] = []
        for index in range(self.arguments.samples):
            pair: MutableMapping[str, WorkerPayload] = {}
            for variant in self.order(index):
                sample = self.measured(variant)
                sample["pair"] = index + 1
                pair[variant] = sample
                self.raw_samples.append(sample)
            pairs.append(pair)
        evidence: BenchmarkDetails = {
            "harness": self.harness,
            "preflights": self.preflights,
            "samples": self.raw_samples,
        }
        if any(
            item.get("status") not in {"PASS", "PARTIAL"} for item in self.raw_samples
        ):
            raise BenchmarkStopped("measurement_failed", evidence)
        if any(
            not sample_matches_preflight(self.preflights[item["variant"]], item)
            for item in self.raw_samples
        ):
            raise BenchmarkStopped("measurement_identity_drifted", evidence)
        return pairs

    def result(self, pairs: Sequence[Mapping[str, WorkerPayload]]) -> BenchmarkEvidence:
        """Apply the existing correctness and performance criteria to measured pairs.

        :param pairs: Complete matched pairs already checked against preflight identity.
        :returns: Final PASS/BLOCKED evidence with unchanged thresholds and measurements.
        :raises KeyError: If a pair lacks required measurement or variant fields.
        :raises ValueError: If no samples exist or numeric measurements are malformed.
        :raises ZeroDivisionError: If a baseline duration or p95 is zero.
        :raises OSError: If executor namespace metadata cannot be read.
        :raises UnicodeError: If mounted namespace metadata is not valid text.
        """  # noqa: DOC502 - Identity, worker, statistics, and output helpers can fail.
        grouped = {
            name: [pair[name] for pair in pairs] for name in ("baseline", "candidate")
        }
        summaries = {name: variant_summary(items) for name, items in grouped.items()}
        correctness_result = correctness(summaries)
        performance_result = performance(
            pairs,
            summaries,
            minimum_improvement=self.arguments.minimum_improvement_percent,
            minimum_faster_pairs=self.arguments.minimum_faster_pairs,
            maximum_p95_regression=self.arguments.maximum_p95_regression_percent,
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
            "captured_at": datetime.now(UTC).isoformat(),
            "revisions": {
                "harness": self.arguments.harness_sha.lower(),
                "baseline": self.arguments.baseline_sha.lower(),
                "candidate": self.arguments.candidate_sha.lower(),
            },
            "harness": self.harness,
            "variant_sources": self.variant_sources,
            "environment": _environment(self.arguments.executor_label),
            "workload": {
                "from": self.arguments.from_time,
                "to": self.arguments.to_time,
                "warmups": self.arguments.warmups,
                "samples_per_variant": self.arguments.samples,
                "maximum_live_operations": 2
                + 2 * (self.arguments.warmups + self.arguments.samples),
                "sample_timeout_seconds": self.arguments.sample_timeout_seconds,
                "total_timeout_seconds": self.arguments.total_timeout_seconds,
                "order": "alternating paired A/B then B/A",
                "boundary": "collect_events; access preflight excluded",
            },
            "preflights": self.preflights,
            "correctness": correctness_result,
            "performance": performance_result,
            "summary": summaries,
            "samples": self.raw_samples,
        }

    def execute(self) -> BenchmarkEvidence:
        """Run the declared phases in order and evaluate the resulting pair set.

        :returns: Completed correctness and performance evidence.
        :raises BenchmarkStopped: If preflight, warmup, or measured identity fails.
        :raises KeyError: If accepted worker evidence lacks a required measurement.
        :raises ValueError: If accepted samples cannot form valid statistics.
        :raises ZeroDivisionError: If a baseline duration or p95 is zero.
        :raises OSError: If a worker cannot start or executor metadata cannot be read.
        :raises UnicodeError: If worker output or mounted metadata cannot be decoded.
        """  # noqa: DOC502 - Identity, worker, statistics, and output helpers can fail.
        self.preflight()
        self.warmup()
        return self.result(self.samples())


def run(arguments: argparse.Namespace) -> BenchmarkEvidence:
    """Compare two clean revisions through matched live event reads.

    :param arguments: Exact roots/SHAs, target, fixed window, limits, and thresholds.
    :returns: Existing PASS/BLOCKED benchmark envelope with collected phase evidence.
    :raises OSError: If a source/namespace file is unreadable or Git/a worker cannot start.
    :raises UnicodeError: If captured worker or namespace text cannot be decoded.
    :raises subprocess.TimeoutExpired: If a source identity Git query exceeds its bound.
    :raises KeyError: If accepted worker evidence lacks a required measurement.
    :raises ValueError: If accepted samples cannot form valid statistics.
    :raises ZeroDivisionError: If a baseline duration or p95 is zero.
    """  # noqa: DOC502 - Identity, worker, statistics, and output helpers can fail.
    if platform.system() != "Linux" or not os.environ.get("KUBERNETES_SERVICE_HOST"):
        return _blocked(arguments, "authorized_kubernetes_runner_required")
    try:
        return BenchmarkRun(arguments).execute()
    except SourceIdentityError as exc:
        return _blocked(arguments, str(exc))
    except BenchmarkStopped as exc:
        return _blocked_with(arguments, str(exc), **exc.evidence)


def _format_seconds(value: float) -> str:
    """Format one benchmark duration for the evidence table.

    :param value: Duration in seconds.
    :returns: Three-decimal text with a seconds suffix.
    """
    return f"{value:.3f} s"


def _change(baseline: float, candidate: float) -> str:
    """Format candidate wall-time change as an improvement percentage.

    :param baseline: Baseline duration in seconds.
    :param candidate: Candidate duration in seconds.
    :returns: Signed one-decimal improvement text; zero baseline yields zero.
    """
    percent = (candidate - baseline) / baseline * 100 if baseline else 0.0
    return f"{-percent:+.1f}% improvement"


def _markdown(evidence: BenchmarkEvidence) -> str:
    """Render pinned A/B evidence as a reviewer-facing Markdown table.

    :param evidence: Benchmark report or plan with revision, environment, and
        optional summary/correctness/performance fields.
    :returns: Markdown table of source identity, timing, semantics, calls,
        acceptance, environment, and measurement limits.
    :raises KeyError: Required revision fields are absent.
    """  # noqa: DOC502 - Required revision and measurement fields use indexing.
    base = evidence.get("summary", {}).get("baseline")
    candidate = evidence.get("summary", {}).get("candidate")
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
        (
            f"| Harness exact SHA | "
            f"`{revisions['harness']}` | "
            f"`{revisions['harness']}` | "
            f"{'planned' if evidence.get('status') == 'DRY_RUN' else 'pinned'} |"
        ),
        f"| Harness tree SHA-256 | `{harness_digest}` | `{harness_digest}` | content identity |",
        (
            f"| Source exact SHA | "
            f"`{revisions['baseline']}` | "
            f"`{revisions['candidate']}` | "
            f"{'planned' if evidence.get('status') == 'DRY_RUN' else 'pinned'} |"
        ),
        f"| Source tree SHA-256 | `{baseline_digest}` | `{candidate_digest}` | content identity |",
        (
            f"| Samples | "
            f"{base['sample_count'] if base else 0} | "
            f"{candidate['sample_count'] if candidate else 0} | "
            f"warmups excluded |"
        ),
    ]
    if base and candidate:
        base_wall = base["wall_seconds"]
        candidate_wall = candidate["wall_seconds"]
        lines += [
            (
                f"| Wall p50 | "
                f"{_format_seconds(base_wall['p50'])} | "
                f"{_format_seconds(candidate_wall['p50'])} | "
                f"{_change(base_wall['p50'], candidate_wall['p50'])} |"
            ),
            (
                f"| Wall p95 | "
                f"{_format_seconds(base_wall['p95'])} | "
                f"{_format_seconds(candidate_wall['p95'])} | "
                f"{_change(base_wall['p95'], candidate_wall['p95'])} |"
            ),
            (
                f"| Wall CV | "
                f"{base_wall['cv_percent']:.1f}% | "
                f"{candidate_wall['cv_percent']:.1f}% | "
                f"variance read-back |"
            ),
            (
                f"| Record counts | "
                f"`{base['record_counts']}` | "
                f"`{candidate['record_counts']}` | "
                f"{'PASS' if correctness_result.get('record_counts_equal') else 'BLOCKED'} |"
            ),
            (
                f"| Semantic SHA-256 | "
                f"`{base['semantic_sha256'][0] if len(base['semantic_sha256']) == 1 else 'unstable'}` | "
                f"`{candidate['semantic_sha256'][0] if len(candidate['semantic_sha256']) == 1 else 'unstable'}` | "
                f"{'PASS' if correctness_result.get('semantic_equal') else 'BLOCKED'} |"
            ),
            (
                f"| Collector errors | "
                f"`{base['error_counts']}` | "
                f"`{candidate['error_counts']}` | "
                f"{'PASS' if correctness_result.get('no_errors') else 'BLOCKED'} |"
            ),
            (
                f"| Kubernetes calls | "
                f"`{base['kubectl_call_counts']}` | "
                f"`{candidate['kubectl_call_counts']}` | "
                f"{'PASS' if performance_result.get('stable_kubectl_call_reduction') else 'BLOCKED'} |"
            ),
            (
                f"| Faster sample pairs | "
                f"n/a | "
                f"{performance_result.get('candidate_faster_pairs', 0)}/{base['sample_count'] if base else 0} | "
                f"observed |"
            ),
        ]
    lines += [
        f"| Correctness gate | n/a | n/a | **{correctness_result.get('status', 'NOT_RUN')}** |",
        (
            f"| Performance acceptance | "
            f"n/a | "
            f"n/a | "
            f"**{performance_result.get('acceptance', 'NOT_RUN')}** |"
        ),
        f"| Overall | n/a | n/a | **{evidence.get('status', 'BLOCKED')}** |",
        "",
        "Environment and limits:",
        "",
        f"- Executor: `{evidence.get('environment', {}).get('executor_label', 'unknown')}` in Kubernetes namespace `{evidence.get('environment', {}).get('kubernetes_namespace') or 'unknown'}`.",
        f"- Window: `{evidence.get('workload', {}).get('from')}` through `{evidence.get('workload', {}).get('to')}`.",
        (
            "- No measurements were run; this artifact records the planned boundary."
            if evidence.get("status") == "DRY_RUN"
            else "- Measurements cover `collect_events()` with the pinned target and credential identity. Preflight time is excluded."
        ),
        "- A PASS applies only to this executor, target, window, harness SHA, and source SHA pair.",
        "",
    ]
    return "\n".join(lines)


def _write(path: Path, body: str) -> None:
    """Replace one benchmark evidence file through a temporary sibling.

    :param path: Selected JSON or Markdown destination.
    :param body: Complete UTF-8 content to write.
    :raises OSError: Parent creation, temporary write, or replacement fails.
    """  # noqa: DOC502 - Directory creation and evidence writes may fail.
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_text(body, encoding="utf-8")
    os.replace(temporary, path)


def _parser() -> argparse.ArgumentParser:
    """Declare exact-source live A/B benchmark inputs and output paths.

    :returns: Parser for harness/variant SHAs and roots, target, window,
        sample budget, thresholds, JSON/Markdown destinations, and plan mode.
    """
    parser = argparse.ArgumentParser(
        description="Benchmark exact baseline and candidate event collectors live.",
        epilog=(
            "Run only in an authorized Linux Kubernetes Job or Kubernetes-backed "
            "CI runner with the same target and effective credentials mounted."
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


def _plan(arguments: argparse.Namespace) -> BenchmarkEvidence:
    """Describe the benchmark budget without running measurements.

    :param arguments: Revisions, executor, window, sample counts, timeouts, and
        acceptance thresholds.
    :returns: DRY_RUN record with maximum live-operation count and thresholds.
    :raises OSError: If mounted executor namespace metadata cannot be read.
    :raises UnicodeError: If mounted namespace metadata is not valid UTF-8.
    """  # noqa: DOC502 - Executor namespace reads may fail or contain invalid text.
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
    """Validate arguments, run or plan the benchmark, and write evidence.

    :returns: Zero for PASS or DRY_RUN; two for BLOCKED acceptance.
    :raises SystemExit: Command-line arguments are invalid or help is requested.
    :raises OSError: If evidence files, namespace metadata, or worker processes fail.
    :raises UnicodeError: If worker or mounted metadata is not decodable.
    :raises subprocess.TimeoutExpired: If a source identity Git query exceeds its bound.
    :raises KeyError: If accepted evidence lacks a required measurement or revision.
    :raises ValueError: If accepted evidence cannot form valid statistics.
    :raises ZeroDivisionError: If a baseline duration or p95 is zero.
    """  # noqa: DOC502 - Identity, worker, statistics, and output helpers can fail.
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
