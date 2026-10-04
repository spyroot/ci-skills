"""The GitHub validate route rejects the exact invented gate paths."""

from __future__ import annotations

import subprocess
import sys

from conftest import REPO_ROOT, load_module

sys.path.insert(0, str(REPO_ROOT / "tools"))
POLICY = load_module(
    "check_delivery_policy", REPO_ROOT / "tools" / "check_delivery_policy.py"
)


def _repository(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    workflow = tmp_path / ".github" / "workflows" / "validate.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "name: validate\non:\n  pull_request:\n  push:\n    branches: [main]\njobs:\n"
        "  validate:\n    runs-on: ubuntu-latest\n    steps: []\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    return tmp_path


def test_exact_approved_route_passes(tmp_path):
    assert POLICY.check(_repository(tmp_path)) == []


def test_removed_check_entrypoint_cannot_return(tmp_path):
    root = _repository(tmp_path)
    path = root / "scripts" / "check.sh"
    path.parent.mkdir()
    path.write_text("exit 0\n", encoding="utf-8")
    assert "unapproved_entrypoint:scripts/check.sh" in POLICY.check(root)


def test_second_workflow_cannot_create_another_required_route(tmp_path):
    root = _repository(tmp_path)
    (root / ".github" / "workflows" / "second.yml").write_text(
        "name: second\n", encoding="utf-8"
    )
    assert "workflow_route_unapproved" in POLICY.check(root)


def test_required_step_cannot_hide_failure(tmp_path):
    root = _repository(tmp_path)
    workflow = root / ".github" / "workflows" / "validate.yml"
    workflow.write_text(
        "name: validate\non:\n  pull_request:\n  push:\n    branches: [main]\njobs:\n"
        "  validate:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: false\n        continue-on-error: true\n",
        encoding="utf-8",
    )
    assert "step_may_bypass_required_result" in POLICY.check(root)


def test_required_step_cannot_be_conditional(tmp_path):
    root = _repository(tmp_path)
    workflow = root / ".github" / "workflows" / "validate.yml"
    workflow.write_text(
        "name: validate\non:\n  pull_request:\n  push:\n    branches: [main]\njobs:\n"
        "  validate:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: echo check\n        if: false\n",
        encoding="utf-8",
    )
    assert "required_step_may_be_skipped" in POLICY.check(root)
