"""Regression coverage for closed-world required CI evidence.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import REPO_ROOT, load_module

GATE = load_module("ci_required_result", REPO_ROOT / "tools" / "ci_required_result.py")
CONTRACT_ROOT = REPO_ROOT
COMMIT = "a" * 40
PIPELINE_ID = "regression-pipeline"
RUNNER_DIGEST = "sha256:" + "b" * 64


if TYPE_CHECKING:
    from lib.python.core.json_types import JSONValue


def _contract_path(*parts: str) -> Path:
    """Resolve an inventoried CI contract path from the repository root.

    :param *parts: Path or command fragments joined by the helper.
    :returns: Joined repository path.

    """
    return CONTRACT_ROOT.joinpath(*parts)


def _emit_args(
    job: str, outcomes: Mapping[str, str], output: Path
) -> argparse.Namespace:
    """Build one required-job evidence emitter invocation.

    :param job: Inventoried CI job whose steps are reported.
    :param outcomes: Step names mapped to observed result states.
    :param output: Directory receiving CI and smoke evidence files.
    :returns: Parsed-argument-shaped namespace for the emitter.
    """
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
    """Build the required-result aggregator invocation.

    :param output: Directory containing predecessor evidence files.
    :param commit: Commit identity expected to match every file.
    :returns: Argument namespace selecting inventory, workflow, and runtime ID.
    """
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


def _success_steps(job: str) -> Mapping[str, str]:
    """Mark every inventoried step of one job successful.

    :param job: Job key looked up in smoke-test inventory.
    :returns: Required step names mapped to ``success``.
    """
    record = GATE._inventory(_emit_args(job, {}, Path("unused")).inventory)[job]
    return {step: "success" for step in record["requiredSteps"]}


def _complete_predecessors(output: Path) -> None:
    """Emit matching passing evidence for every non-aggregate job.

    :param output: Directory receiving CI and smoke predecessor records.

    :returns: None; the helper records behavior through its side effects.
    """
    inventory = GATE._inventory(_aggregate_args(output).inventory)
    for job, record in inventory.items():
        if not record.get("aggregationJob"):
            assert GATE.emit(_emit_args(job, _success_steps(job), output)) == 0


def _alter(path: Path, field: str, value: JSONValue) -> None:
    """Change one field in a generated JSON evidence file.

    :param path: Existing evidence file under the scratch output directory.
    :param field: Top-level evidence key to overwrite.
    :param value: Replacement used to simulate a failed gate.

    :returns: None; the helper records behavior through its side effects.
    """
    record: MutableMapping[str, JSONValue] = json.loads(
        path.read_text(encoding="utf-8")
    )
    record[field] = value
    path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")


def test_complete_required_evidence_aggregates_only_when_every_job_matches(
    tmp_path: Path,
) -> None:
    """Accept a closed set of matching successful predecessor receipts.

    :param tmp_path: Scratch evidence directory for all inventoried jobs.

    :returns: None; assertions in the body validate the regression.
    """
    _complete_predecessors(tmp_path)

    assert GATE.aggregate(_aggregate_args(tmp_path)) == 0


def test_emit_rejects_missing_required_step(tmp_path: Path) -> None:
    """Refuse evidence that omits one required inventory step.

    :param tmp_path: Scratch output directory for the attempted receipt.

    :returns: None; assertions in the body validate the regression.
    """
    outcomes = _success_steps("validate")
    outcomes.pop(next(iter(outcomes)))

    with pytest.raises(GATE.EvidenceError, match="step_results_missing"):
        GATE.emit(_emit_args("validate", outcomes, tmp_path))


def test_aggregate_rejects_warning_or_skipped_evidence(tmp_path: Path) -> None:
    """Fail aggregate when CI or smoke evidence has warnings or skips.

    :param tmp_path: Scratch evidence directory modified between checks.

    :returns: None; assertions in the body validate the regression.
    """
    _complete_predecessors(tmp_path)
    for kind in ("ci", "smoke"):
        _alter(tmp_path / kind / "validate.json", "warnings", 1)

    assert GATE.aggregate(_aggregate_args(tmp_path)) == 1

    _complete_predecessors(tmp_path)
    for kind in ("ci", "smoke"):
        _alter(tmp_path / kind / "validate.json", "skipped_required_tests", 1)

    assert GATE.aggregate(_aggregate_args(tmp_path)) == 1


@pytest.mark.parametrize(
    ("kind", "path", "field"),
    (
        ("ci", "cleanup", "status"),
        ("smoke", "checks", "cleanup"),
    ),
)
def test_aggregate_rejects_failed_cleanup_despite_passed_top_status(
    tmp_path: Path, kind: str, path: Path, field: str
) -> None:
    """Fail aggregate for nested cleanup failure beneath a PASS receipt.

    :param tmp_path: Scratch evidence directory for predecessor jobs.
    :param kind: CI or smoke receipt branch to corrupt.
    :param path: Parent object containing the nested cleanup field.
    :param field: Nested status key changed to failed.

    :returns: None; assertions in the body validate the regression.
    """
    _complete_predecessors(tmp_path)
    record = json.loads((tmp_path / kind / "validate.json").read_text(encoding="utf-8"))
    record[path][field] = "failed"
    (tmp_path / kind / "validate.json").write_text(
        json.dumps(record, sort_keys=True), encoding="utf-8"
    )

    assert GATE.aggregate(_aggregate_args(tmp_path)) == 1


def test_aggregate_rejects_wrong_runtime_identity(tmp_path: Path) -> None:
    """Reject evidence whose commit differs from aggregator identity.

    :param tmp_path: Scratch evidence directory for predecessor jobs.

    :returns: None; assertions in the body validate the regression.
    """
    _complete_predecessors(tmp_path)

    assert GATE.aggregate(_aggregate_args(tmp_path, commit="d" * 40)) == 1


def test_aggregate_rejects_missing_or_extra_inventoried_job(tmp_path: Path) -> None:
    """Reject both absent required jobs and undeclared extra receipts.

    :param tmp_path: Scratch evidence directory modified between checks.

    :returns: None; assertions in the body validate the regression.
    """
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
