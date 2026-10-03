"""Regression tests for the required validation workflow gates."""

from __future__ import annotations

import yaml
from conftest import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "validate.yml"
REQUIRED_PYTHON_STEPS = (
    "Python dependencies",
    "Ruff lint",
    "Ruff format",
    "Isolated installed-package smoke",
    "Tests",
)


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow_steps() -> list[dict]:
    workflow = yaml.safe_load(_workflow_text())
    return workflow["jobs"]["validate"]["steps"]


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

    A Markdown-only change can edit the claims this gate backs. Marking it
    advisory or moving it to another workflow would bypass `validate`.
    """
    steps = _workflow_steps()

    matching = [
        step for step in steps if "check_live_acceptance.py" in str(step.get("run", ""))
    ]
    assert len(matching) == 1, "exactly one acceptance step must run the tool"
    step = matching[0]
    assert "if" not in step, "the acceptance step must not be conditional"
    assert step.get("continue-on-error") in (None, False)
    assert "|| true" not in str(step.get("run"))

    python_steps = [
        index
        for index, item in enumerate(steps)
        if item.get("name") in REQUIRED_PYTHON_STEPS
    ]
    assert len(python_steps) == len(REQUIRED_PYTHON_STEPS)
    assert steps.index(step) < min(python_steps), (
        "the acceptance step must precede required Python validation"
    )


def test_required_python_steps_are_unconditional_and_non_advisory():
    """A documentation-only PR must exercise the same required Python gates."""
    steps = _workflow_steps()

    for name in REQUIRED_PYTHON_STEPS:
        matching = [step for step in steps if step.get("name") == name]
        assert len(matching) == 1, f"exactly one {name} step is required"
        step = matching[0]
        assert "if" not in step, f"{name} must not be conditional"
        assert step.get("continue-on-error") in (None, False)
        assert "|| true" not in str(step.get("run", ""))
