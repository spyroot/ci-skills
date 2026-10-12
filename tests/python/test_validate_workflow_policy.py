"""Regression tests for the required validation workflow gates.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import shlex

import yaml
from conftest import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "validate.yml"


def _workflow_text() -> str:
    """Read the committed GitHub validation workflow for policy assertions.

    :returns: Full UTF-8 workflow source text.
    :raises OSError: The workflow file cannot be read.
    """  # noqa: DOC502 - Path.read_text propagates filesystem errors.
    return WORKFLOW.read_text(encoding="utf-8")


def _pytest_selectors(command: str) -> tuple[str, ...]:
    """Return positional Python-test selectors from one shell command.

    :param command: Workflow ``run`` script containing a pytest invocation.
    :returns: Test paths selected for execution, excluding ``--ignore`` paths.
    """
    tokens = shlex.split(command)
    return tuple(
        token
        for index, token in enumerate(tokens)
        if token.startswith("tests/python/")
        and (index == 0 or tokens[index - 1] != "--ignore")
    )


def test_validate_workflow_runs_required_static_policy_gates() -> None:
    """The validation workflow calls the shared static entrypoint."""
    text = _workflow_text()

    assert 'scripts/ci/static.sh --base "$base_sha"' in text
    assert "git diff --check" in text


def test_validate_workflow_runs_installed_package_smoke() -> None:
    """The validation workflow runs the isolated installed-package smoke."""
    text = _workflow_text()

    assert "tests/python/test_installed_package.py" in text


def test_acceptance_evaluator_is_offline_and_unconditional() -> None:
    """GitHub runs the offline evaluator without invoking live publication."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["validate"]["steps"]

    matching = [
        step
        for step in steps
        if "tests/python/test_live_acceptance.py"
        in _pytest_selectors(str(step.get("run", "")))
    ]
    assert len(matching) == 1, "exactly one offline acceptance evaluator must run"
    step = matching[0]
    assert "offline" in str(step.get("name", "")).lower()
    assert "if" not in step
    assert step.get("continue-on-error") in (None, False)
    assert "|| true" not in str(step.get("run"))
    assert "python tools/check_live_acceptance.py" not in _workflow_text()
