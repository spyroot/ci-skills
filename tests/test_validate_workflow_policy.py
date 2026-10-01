"""Regression tests for the required validation workflow gates."""

from __future__ import annotations

import yaml
from conftest import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "validate.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_validate_workflow_runs_required_static_policy_gates():
    """The validation workflow wires the static gates that unit tests cannot replace."""
    text = _workflow_text()

    assert "actionlint" in text or "yamllint" in text
    assert "markdownlint" in text
    assert "ruff format --check" in text
    assert "git diff --check" in text
    assert any(
        scanner in text for scanner in ("gitleaks", "detect-secrets", "trivy fs")
    )


def test_validate_workflow_runs_installed_package_smoke():
    """The validation workflow runs the isolated installed-package smoke."""
    text = _workflow_text()

    assert "tests/test_installed_package.py" in text


def test_the_live_acceptance_step_is_unconditional_and_may_not_fail():
    """The central gate must not be skippable or advisory.

    Gated on `non_markdown_count`, a Markdown-only change could edit the very
    claims it backs and skip it. Marked `continue-on-error`, or moved to its own
    workflow, it would be advisory -- `validate` is the required check.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["validate"]["steps"]

    matching = [
        step for step in steps if "check_live_acceptance.py" in str(step.get("run", ""))
    ]
    assert len(matching) == 1, "exactly one acceptance step must run the tool"
    step = matching[0]
    assert "if" not in step, "the acceptance step must not be conditional"
    assert step.get("continue-on-error") in (None, False)
    assert "|| true" not in str(step.get("run"))

    gated = [
        index
        for index, item in enumerate(steps)
        if "non_markdown_count" in str(item.get("if", ""))
    ]
    assert gated, "the gated Python steps should still exist"
    assert steps.index(step) < min(gated), (
        "the acceptance step must precede the steps a Markdown-only change skips"
    )
