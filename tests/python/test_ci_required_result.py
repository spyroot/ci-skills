"""Regression coverage for closed-world required CI evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from tests.python.conftest import REPO_ROOT, load_module

GATE = load_module("ci_required_result", REPO_ROOT / "tools" / "ci_required_result.py")
CONTRACT_ROOT = REPO_ROOT
COMMIT = "a" * 40
PIPELINE_ID = "regression-pipeline"
RUNNER_DIGEST = "sha256:" + "b" * 64


def _contract_path(*parts: str) -> Path:
    return CONTRACT_ROOT.joinpath(*parts)


def _emit_args(job: str, outcomes: dict[str, str], output: Path) -> argparse.Namespace:
    return argparse.Namespace(
        inventory=_contract_path("inventory", "ci", "smoke-tests.yaml"),
        binding=REPO_ROOT / "standards-binding.yaml",
        job=job,
        commit=COMMIT,
        pipeline_id=PIPELINE_ID,
        runner_digest=RUNNER_DIGEST,
        step_results=json.dumps(outcomes, sort_keys=True),
        junit=[],
        tap=[],
        output_dir=output,
        dry_run=False,
    )


def _aggregate_args(output: Path, *, commit: str = COMMIT) -> argparse.Namespace:
    return argparse.Namespace(
        inventory=_contract_path("inventory", "ci", "smoke-tests.yaml"),
        binding=REPO_ROOT / "standards-binding.yaml",
        job="required-result",
        workflow=_contract_path(".github", "workflows", "validate.yml"),
        commit=commit,
        pipeline_id=PIPELINE_ID,
        runner_digest=RUNNER_DIGEST,
        input_dir=output,
        dry_run=False,
    )


def _success_steps(job: str) -> dict[str, str]:
    record = GATE._inventory(_emit_args(job, {}, Path("unused")).inventory)[job]
    return {step: "success" for step in record["requiredSteps"]}


def _complete_predecessors(output: Path) -> None:
    inventory = GATE._inventory(_aggregate_args(output).inventory)
    for job, record in inventory.items():
        if not record.get("aggregationJob"):
            assert GATE.emit(_emit_args(job, _success_steps(job), output)) == 0


def _alter(path: Path, field: str, value: object) -> None:
    record = json.loads(path.read_text(encoding="utf-8"))
    record[field] = value
    path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")


def test_complete_required_evidence_aggregates_only_when_every_job_matches(tmp_path):
    _complete_predecessors(tmp_path)

    assert GATE.aggregate(_aggregate_args(tmp_path)) == 0


def test_emit_rejects_missing_required_step(tmp_path):
    outcomes = _success_steps("validate")
    outcomes.pop(next(iter(outcomes)))

    with pytest.raises(GATE.EvidenceError, match="step_results_missing"):
        GATE.emit(_emit_args("validate", outcomes, tmp_path))


def test_aggregate_rejects_warning_or_skipped_evidence(tmp_path):
    _complete_predecessors(tmp_path)
    for kind in ("ci", "smoke"):
        _alter(tmp_path / kind / "validate.json", "warnings", 1)

    assert GATE.aggregate(_aggregate_args(tmp_path)) == 1

    _complete_predecessors(tmp_path)
    for kind in ("ci", "smoke"):
        _alter(tmp_path / kind / "validate.json", "skipped_required_tests", 1)

    assert GATE.aggregate(_aggregate_args(tmp_path)) == 1


def test_aggregate_rejects_wrong_runtime_identity(tmp_path):
    _complete_predecessors(tmp_path)

    assert GATE.aggregate(_aggregate_args(tmp_path, commit="d" * 40)) == 1


def test_aggregate_rejects_missing_or_extra_inventoried_job(tmp_path):
    _complete_predecessors(tmp_path)
    for kind in ("ci", "smoke"):
        (tmp_path / kind / "test-install.json").unlink()

    with pytest.raises(GATE.EvidenceError, match="unreadable_evidence"):
        GATE.aggregate(_aggregate_args(tmp_path))

    _complete_predecessors(tmp_path)
    for kind in ("ci", "smoke"):
        source = tmp_path / kind / "validate.json"
        (tmp_path / kind / "unexpected.json").write_text(source.read_text())

    assert GATE.aggregate(_aggregate_args(tmp_path)) == 1
