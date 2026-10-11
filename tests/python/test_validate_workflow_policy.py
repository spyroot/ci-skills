"""Regression tests for the required validation workflow gates."""

from __future__ import annotations

import yaml

from tests.python.conftest import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "validate.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_validate_workflow_runs_required_static_policy_gates():
    """The validation workflow calls the shared static entrypoint."""
    text = _workflow_text()

    assert 'scripts/ci/static.sh --base "$base_sha"' in text
    assert "git diff --check" in text


def test_validate_workflow_runs_installed_package_smoke():
    """The validation workflow runs the isolated installed-package smoke."""
    text = _workflow_text()

    assert "tests/python/test_installed_package.py" in text


def test_acceptance_evaluator_is_offline_and_unconditional():
    """GitHub runs the offline evaluator without invoking live publication."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["validate"]["steps"]

    matching = [
        step
        for step in steps
        if "tests/python/test_live_acceptance.py" in str(step.get("run", ""))
    ]
    assert len(matching) == 1, "exactly one offline acceptance evaluator must run"
    step = matching[0]
    assert "offline" in str(step.get("name", "")).lower()
    assert "if" not in step
    assert step.get("continue-on-error") in (None, False)
    assert "|| true" not in str(step.get("run"))
    assert "python tools/check_live_acceptance.py" not in _workflow_text()
