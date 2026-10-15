"""Regression tests for the required validation workflow gates.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from typing import Final

import yaml
from conftest import REPO_ROOT, load_module, selected_test_paths

WORKFLOW: Final = REPO_ROOT / ".github" / "workflows" / "validate.yml"
ACCEPTANCE: Final = load_module(
    "workflow_policy_check_live_acceptance",
    REPO_ROOT / "tools" / "check_live_acceptance.py",
)
POLICY: Final = ACCEPTANCE.load_policy(
    REPO_ROOT / ACCEPTANCE.POLICY_FILE, REPO_ROOT / ACCEPTANCE.POLICY_SCHEMA_FILE
)
CHECKER_COMMAND: Final = "python tools/check_live_acceptance.py --root . --json"


def _workflow_text() -> str:
    """Read the committed GitHub validation workflow for policy assertions.

    :returns: Full UTF-8 workflow source text.
    :raises OSError: The workflow file cannot be read.
    """  # noqa: DOC502 - Path.read_text propagates filesystem errors.
    return WORKFLOW.read_text(encoding="utf-8")


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
        in selected_test_paths(str(step.get("run", "")))
    ]
    assert len(matching) == 1, "exactly one offline acceptance evaluator must run"
    step = matching[0]
    assert "offline" in str(step.get("name", "")).lower()
    assert "if" not in step
    assert step.get("continue-on-error") in (None, False)
    assert "|| true" not in str(step.get("run"))


def test_validate_workflow_evaluates_committed_receipts_unconditionally() -> None:
    """GitHub runs the committed-receipt checker as one unconditional step."""
    workflow = yaml.safe_load(_workflow_text())
    steps = workflow["jobs"]["validate"]["steps"]

    matching = [step for step in steps if CHECKER_COMMAND in str(step.get("run", ""))]
    assert len(matching) == 1, "exactly one committed-receipt evaluation must run"
    step = matching[0]
    assert step.get("id") == "live_acceptance"
    assert "if" not in step
    assert step.get("continue-on-error") in (None, False)
    assert "|| true" not in str(step.get("run"))
    named = [step.get("id") for step in steps if step.get("id")]
    assert named.index("live_acceptance") > named.index("whitespace"), (
        "the live-acceptance step runs after every other check step"
    )


def test_validate_workflow_schedules_the_declared_freshness_check() -> None:
    """The workflow's schedule matches the tracked live-acceptance policy."""
    workflow = yaml.safe_load(_workflow_text())
    triggers = workflow["on"]

    assert POLICY["scheduled_workflow"] == ".github/workflows/validate.yml"
    assert [entry["cron"] for entry in triggers["schedule"]] == [
        POLICY["scheduled_cron"]
    ]
    assert "workflow_dispatch" in triggers
    concurrency = workflow["concurrency"]
    assert concurrency["cancel-in-progress"] is False
    assert "github.sha" in concurrency["group"]
