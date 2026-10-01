"""Regression tests for the required validation workflow gates."""

from __future__ import annotations

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
