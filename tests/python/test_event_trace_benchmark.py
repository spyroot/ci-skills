"""Offline contract tests for the live event-trace A/B harness.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import MutableSequence
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Optional

import pytest
from conftest import REPO_ROOT

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue

BENCHMARK_ROOT = REPO_ROOT / "ci-skills" / "benchmarks"
sys.path.insert(0, str(BENCHMARK_ROOT))

import event_trace_ab as harness
import event_trace_evidence as evidence
import event_trace_worker as worker
import source_identity as provenance

SHA = "a" * 40
OTHER_SHA = "b" * 40
DIGEST = "d" * 64
IDENTITY = {"target_sha256": "t" * 64}
ACCESS = {"profile": "base", "surface_status": {"kubernetes": "PASS"}}
HARNESS = {
    "algorithm": "sha256-tree-v1",
    "digest": "h" * 64,
    "file_count": 4,
    "revision": SHA,
    "subtree": "benchmarks",
}


def _source(revision: str) -> JSONObject:
    """Build a deterministic source identity for one checkout revision.

    :param revision: Commit SHA represented in the worker envelope.
    :returns: Source digest and revision mapping for a fixture envelope.
    """
    return {"digest": revision[0] * 64, "revision": {"value": revision}}


def _preflight(revision: str) -> evidence.WorkerPayload:
    """Build a passing worker preflight envelope for one revision.

    :param revision: Checkout commit SHA assigned to the source identity.
    :returns: Preflight mapping with harness, source, target, and access identity.
    """
    return {
        "schema_version": "1.0",
        "kind": "event_trace_benchmark_preflight",
        "status": "PASS",
        "harness": HARNESS,
        "source": _source(revision),
        "identity": IDENTITY,
        "access": ACCESS,
    }


def _sample(revision: str, wall: float, calls: int) -> evidence.WorkerPayload:
    """Build a passing event-trace sample with controlled measurements.

    :param revision: Checkout commit SHA assigned to the sample source.
    :param wall: Wall-clock seconds represented in the measurement.
    :param calls: Count of kubectl calls represented in the measurement.
    :returns: Worker sample mapping with provenance and measurement fields.
    """
    return {
        "schema_version": "1.0",
        "kind": "event_trace_benchmark_sample",
        "status": "PASS",
        "harness": HARNESS,
        "source": _source(revision),
        "identity": IDENTITY,
        "measurement": {
            "wall_seconds": wall,
            "cpu_seconds": wall / 2,
            "collector_json_bytes": 100,
            "record_count": 3,
            "error_count": 0,
            "kubectl_calls": calls,
            "semantic_sha256": DIGEST,
            "strict_sha256": DIGEST,
        },
    }


def _run_arguments() -> argparse.Namespace:
    """Select bounded paired benchmark arguments for harness tests.

    :returns: Namespace identifying both revisions, target, intervals, and limits.
    """
    return argparse.Namespace(
        harness_sha=SHA,
        baseline_root="/baseline",
        baseline_sha=SHA,
        candidate_root="/candidate",
        candidate_sha=OTHER_SHA,
        target="/target.toml",
        executor_label="unit-kubernetes-runner",
        from_time="2026-01-01T00:00:00Z",
        to_time="2026-01-01T00:01:00Z",
        warmups=1,
        samples=5,
        sample_timeout_seconds=60,
        total_timeout_seconds=600,
        minimum_improvement_percent=10.0,
        minimum_faster_pairs=None,
        maximum_p95_regression_percent=None,
    )


@pytest.mark.parametrize("payload", ([], {}, {"status": "PASS"}))
def test_worker_envelope_rejects_malformed_json_shapes(payload: JSONValue) -> None:
    """Reject non-envelope JSON roots and objects missing required fields.

    :param payload: Empty list or incomplete mapping supplied as worker output.
    """
    with pytest.raises(evidence.EvidenceError):
        evidence.validate_worker_payload("sample", payload)


def test_worker_boundary_returns_structured_failure_for_non_object_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Convert non-object worker JSON into a blocked result with stderr digest.

    :param monkeypatch: Pytest fixture replacing subprocess worker output.
    """
    monkeypatch.setattr(
        harness.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="[]\n", stderr="provider detail", returncode=0
        ),
    )

    result = harness._worker(
        "sample",
        root=Path("/baseline"),
        revision=SHA,
        harness_sha=SHA,
        target=Path("/target.toml"),
        from_time="2026-01-01T00:00:00Z",
        to_time="2026-01-01T00:01:00Z",
        timeout=1,
    )

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "benchmark_worker_non_object_json"
    assert len(result["stderr_sha256"]) == 64


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    (
        ("wall_seconds", 0, "benchmark_worker_wall_time_invalid"),
        ("cpu_seconds", "bad", "benchmark_worker_measurement_values_invalid"),
        ("semantic_sha256", "not-a-digest", "benchmark_worker_digest_invalid"),
    ),
)
def test_worker_envelope_rejects_invalid_measurements(
    field: str, value: JSONValue, reason: str
) -> None:
    """Reject an invalid measurement value with its specific reason.

    :param field: Measurement key replaced in an otherwise valid sample.
    :param value: Invalid value assigned to that measurement key.
    :param reason: Expected validation error category for the invalid value.
    """
    payload = _sample(SHA, wall=1.0, calls=1)
    payload["measurement"][field] = value
    with pytest.raises(evidence.EvidenceError, match=reason):
        evidence.validate_worker_payload("sample", payload)


def test_performance_cannot_pass_without_an_explicit_threshold() -> None:
    """Block performance acceptance when no threshold is configured."""
    pairs = [
        {
            "baseline": _sample(SHA, wall=2.0, calls=2),
            "candidate": _sample(OTHER_SHA, wall=1.0, calls=1),
        }
        for _ in range(5)
    ]
    summaries = {
        name: evidence.variant_summary([pair[name] for pair in pairs])
        for name in ("baseline", "candidate")
    }

    result = evidence.performance(
        pairs,
        summaries,
        minimum_improvement=None,
        minimum_faster_pairs=None,
        maximum_p95_regression=None,
    )

    assert result["acceptance"] == "BLOCKED"
    assert result["required_results"]["thresholds_configured"] is False


def test_slower_candidate_cannot_pass_a_permissive_p95_threshold() -> None:
    """Block a slower candidate even with a permissive p95 regression limit."""
    pairs = [
        {
            "baseline": _sample(SHA, wall=1.0, calls=2),
            "candidate": _sample(OTHER_SHA, wall=2.0, calls=1),
        }
        for _ in range(5)
    ]
    summaries = {
        name: evidence.variant_summary([pair[name] for pair in pairs])
        for name in ("baseline", "candidate")
    }

    result = evidence.performance(
        pairs,
        summaries,
        minimum_improvement=None,
        minimum_faster_pairs=None,
        maximum_p95_regression=200,
    )

    assert result["acceptance"] == "BLOCKED"
    assert result["required_results"]["candidate_median_faster"] is False


def test_every_sample_must_match_its_preflight_identity_and_source() -> None:
    """Reject sample target identity drift from the matching preflight."""
    preflight = _preflight(SHA)
    sample = _sample(SHA, wall=1.0, calls=1)
    assert evidence.sample_matches_preflight(preflight, sample)

    sample["identity"] = {"target_sha256": "changed"}
    assert not evidence.sample_matches_preflight(preflight, sample)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    (
        ("warmups", 4, "warmups_out_of_range"),
        ("samples", 21, "samples_out_of_range"),
        ("sample_timeout_seconds", 301, "sample_timeout_out_of_range"),
        ("total_timeout_seconds", 1801, "total_timeout_out_of_range"),
    ),
)
def test_live_read_budget_has_hard_upper_bounds(
    field: str, value: int, reason: str
) -> None:
    """Reject each warmup, sample, or deadline value above its hard limit.

    :param field: Budget field changed from the valid argument set.
    :param value: Out-of-range value assigned to that field.
    :param reason: Expected limit-validation reason for the selected field.
    """
    arguments = SimpleNamespace(
        warmups=1,
        samples=5,
        sample_timeout_seconds=60,
        total_timeout_seconds=600,
        minimum_faster_pairs=None,
    )
    setattr(arguments, field, value)
    assert evidence.validate_limits(arguments) == reason


def test_total_deadline_stops_before_another_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return zero remaining time after the total benchmark deadline.

    :param monkeypatch: Pytest fixture setting the monotonic clock past deadline.
    """
    arguments = SimpleNamespace(sample_timeout_seconds=300)
    monkeypatch.setattr(harness.time, "monotonic", lambda: 101.0)
    assert harness._remaining_timeout(arguments, 100.0) == 0


def test_clean_exact_harness_provenance_and_dirty_rejection(tmp_path: Path) -> None:
    """Fingerprint an exact clean subtree and reject mismatched or dirty source.

    :param tmp_path: Pytest directory containing a temporary committed Git tree.
    """
    root = tmp_path / "repo"
    subtree = root / "benchmarks"
    subtree.mkdir(parents=True)
    (subtree / "tool.py").write_text("VALUE = 1\n", encoding="utf-8")
    for arguments in (
        ("init", "-q"),
        ("config", "user.email", "unit@example.test"),
        ("config", "user.name", "unit"),
        ("add", "benchmarks/tool.py"),
        ("commit", "-qm", "fixture"),
    ):
        subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    exact = provenance.source_identity(root, revision, subtree)
    assert exact["revision"] == revision
    assert exact["subtree"] == "benchmarks"
    assert len(exact["digest"]) == 64

    with pytest.raises(
        provenance.SourceIdentityError, match="source_revision_mismatch"
    ):
        provenance.source_identity(root, OTHER_SHA, subtree)

    (subtree / "tool.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(provenance.SourceIdentityError, match="source_subtree_dirty"):
        provenance.source_identity(root, revision, subtree)


def test_run_fingerprints_each_checkout_at_its_skill_subtree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fingerprint baseline and candidate at each imported ci-skills subtree.

    :param monkeypatch: Pytest fixture selecting Kubernetes mode and capturing identities.
    """
    arguments = _run_arguments()
    subtrees: MutableSequence[Path] = []

    monkeypatch.setattr(harness.platform, "system", lambda: "Linux")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")

    def recording_identity(root: Path, revision: str, subtree: Path) -> JSONObject:
        """Capture requested subtree paths, then stop after both checkouts.

        :param root: Checkout root supplied by the harness.
        :param revision: Exact checkout revision supplied by the harness.
        :param subtree: Tree path included in the source fingerprint.
        :returns: Fixture harness identity before the stop condition.
        :raises provenance.SourceIdentityError: After recording the third identity request.
        """
        subtrees.append(Path(subtree))
        if len(subtrees) == 3:
            raise provenance.SourceIdentityError("stop_after_fingerprints")
        return HARNESS

    monkeypatch.setattr(harness, "source_identity", recording_identity)

    result = harness.run(arguments)

    assert result["reason"] == "stop_after_fingerprints"
    assert subtrees[1:] == [
        Path(arguments.baseline_root).resolve() / "ci-skills",
        Path(arguments.candidate_root).resolve() / "ci-skills",
    ]


def test_worker_refuses_a_checkout_without_the_skill_library(tmp_path: Path) -> None:
    """Reject a checkout with no ci-skills Python library.

    :param tmp_path: Empty Pytest checkout root passed to the worker.
    """
    with pytest.raises(RuntimeError, match="skill_library_missing"):
        worker._source(tmp_path, SHA)


def test_worker_refuses_the_pre_move_library_layout(tmp_path: Path) -> None:
    """Reject a checkout containing only the obsolete library layout.

    :param tmp_path: Pytest checkout root with the old core directory.
    """
    (tmp_path / "ci-skills" / "lib" / "core").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="skill_library_missing"):
        worker._source(tmp_path, SHA)


def test_worker_fingerprints_the_skill_tree_and_imports_its_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Import the current library path after fingerprinting the skill tree.

    :param tmp_path: Pytest checkout root receiving the current library path.
    :param monkeypatch: Pytest fixture recording provenance and isolating sys.path.
    """
    (tmp_path / "ci-skills" / "lib" / "python").mkdir(parents=True)
    bootstrap = tmp_path / "ci-skills" / "bin" / "_bootstrap.py"
    bootstrap.parent.mkdir()
    bootstrap.write_text(
        (REPO_ROOT / "ci-skills" / "bin" / "_bootstrap.py").read_text()
    )
    calls: MutableSequence[tuple[Path, str, Path]] = []
    monkeypatch.setattr(worker, "source_identity", lambda *args: calls.append(args))
    monkeypatch.setattr(worker.sys, "path", list(sys.path))

    library = worker._source(tmp_path, SHA)

    root = tmp_path.resolve()
    assert calls == [(root, SHA, root / "ci-skills")]
    assert library == root / "ci-skills" / "lib" / "python"
    assert worker.sys.path[0] == str(root / "ci-skills")


def test_run_alternates_pairs_and_passes_only_with_grounded_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alternate baseline and candidate samples and require grounded acceptance.

    :param monkeypatch: Pytest fixture replacing platform, identity, and worker reads.
    """
    arguments = _run_arguments()
    calls: MutableSequence[tuple[str, str]] = []

    monkeypatch.setattr(harness.platform, "system", lambda: "Linux")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    monkeypatch.setattr(harness, "source_identity", lambda *args: HARNESS)

    def fake_worker(
        operation: str,
        root: Path,
        revision: str,
        harness_sha: str,
        target: Path,
        from_time: Optional[str],
        to_time: Optional[str],
        timeout: float,
    ) -> evidence.WorkerPayload:
        """Return preflight or faster candidate samples in requested order.

        :param operation: Worker preflight or sample operation requested by the harness.
        :param root: Checkout root accepted by the worker replacement.
        :param revision: Selected checkout revision represented in the envelope.
        :param harness_sha: Harness commit accepted by the worker replacement.
        :param target: Runtime target path accepted without reading credentials.
        :param from_time: Optional sample-window start accepted by the worker.
        :param to_time: Optional sample-window end accepted by the worker.
        :param timeout: Worker deadline accepted without starting a process.
        :returns: Matching passing preflight or measured sample envelope.
        """
        calls.append((operation, revision))
        if operation == "preflight":
            return _preflight(revision)
        return _sample(
            revision,
            wall=2.0 if revision == SHA else 1.0,
            calls=2 if revision == SHA else 1,
        )

    monkeypatch.setattr(harness, "_worker", fake_worker)

    result = harness.run(arguments)

    assert result["status"] == "PASS"
    assert result["workload"]["maximum_live_operations"] == 14
    measured_order = [
        revision for operation, revision in calls if operation == "sample"
    ]
    assert measured_order == [
        SHA,
        OTHER_SHA,
        SHA,
        OTHER_SHA,
        OTHER_SHA,
        SHA,
        SHA,
        OTHER_SHA,
        OTHER_SHA,
        SHA,
        SHA,
        OTHER_SHA,
    ]


def test_run_classifies_sample_identity_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block benchmark acceptance after one sample changes target identity.

    :param monkeypatch: Pytest fixture substituting identity and worker envelopes.
    """
    arguments = _run_arguments()
    sample_calls = 0
    monkeypatch.setattr(harness.platform, "system", lambda: "Linux")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    monkeypatch.setattr(harness, "source_identity", lambda *args: HARNESS)

    def fake_worker(
        operation: str,
        root: Path,
        revision: str,
        harness_sha: str,
        target: Path,
        from_time: Optional[str],
        to_time: Optional[str],
        timeout: float,
    ) -> evidence.WorkerPayload:
        """Change target identity on the third sample while preserving other samples.

        :param operation: Worker preflight or sample operation requested by the harness.
        :param root: Checkout root accepted by the worker replacement.
        :param revision: Selected checkout revision represented in the envelope.
        :param harness_sha: Harness commit accepted by the worker replacement.
        :param target: Runtime target path accepted without reading credentials.
        :param from_time: Optional sample-window start accepted by the worker.
        :param to_time: Optional sample-window end accepted by the worker.
        :param timeout: Worker deadline accepted without starting a process.
        :returns: Passing envelope or one sample with drifted target identity.
        """
        nonlocal sample_calls
        if operation == "preflight":
            return _preflight(revision)
        sample_calls += 1
        payload = _sample(
            revision,
            wall=2.0 if revision == SHA else 1.0,
            calls=2 if revision == SHA else 1,
        )
        if sample_calls == 3:
            payload["identity"] = {"target_sha256": "drifted"}
        return payload

    monkeypatch.setattr(harness, "_worker", fake_worker)

    result = harness.run(arguments)

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "measurement_identity_drifted"


def test_run_classifies_harness_digest_mismatch_before_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Block before sampling when candidate preflight harness digest differs.

    :param monkeypatch: Pytest fixture substituting platform, identity, and worker.
    """
    arguments = _run_arguments()
    monkeypatch.setattr(harness.platform, "system", lambda: "Linux")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    monkeypatch.setattr(harness, "source_identity", lambda *args: HARNESS)

    def fake_worker(
        operation: str,
        root: Path,
        revision: str,
        harness_sha: str,
        target: Path,
        from_time: Optional[str],
        to_time: Optional[str],
        timeout: float,
    ) -> evidence.WorkerPayload:
        """Return a candidate preflight with a changed harness digest.

        :param operation: Preflight operation required before sampling.
        :param root: Checkout root accepted by the worker replacement.
        :param revision: Selected checkout revision represented in the envelope.
        :param harness_sha: Harness commit accepted by the worker replacement.
        :param target: Runtime target path accepted without reading credentials.
        :param from_time: Optional sample-window start accepted by the worker.
        :param to_time: Optional sample-window end accepted by the worker.
        :param timeout: Worker deadline accepted without starting a process.
        :returns: Preflight envelope with changed candidate harness identity.
        """
        assert operation == "preflight"
        payload = _preflight(revision)
        if revision == OTHER_SHA:
            payload["harness"] = {**HARNESS, "digest": "0" * 64}
        return payload

    monkeypatch.setattr(harness, "_worker", fake_worker)

    result = harness.run(arguments)

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "benchmark_harness_identity_mismatch"


def test_existing_ci_test_step_collects_benchmark_contract_tests() -> None:
    """Keep benchmark contract tests in the repository's existing pytest step."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "validate.yml").read_text(
        encoding="utf-8"
    )
    assert "pytest -q --ignore=tests/test_installed_package.py" in workflow
    assert "tests/test_event_trace_benchmark.py" not in workflow
