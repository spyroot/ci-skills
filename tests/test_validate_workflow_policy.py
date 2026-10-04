"""Regression tests for the required validation workflow gates."""

from __future__ import annotations

import os
import subprocess

import yaml
from conftest import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "validate.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow() -> dict:
    return yaml.safe_load(_workflow_text())


def _changed_file_script() -> str:
    for step in _workflow()["jobs"]["validate"]["steps"]:
        if step.get("name") == "Classify changed files":
            return step["run"]
    raise AssertionError("Classify changed files step is missing")


def _git(repo, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repo, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _run_changed_file_classifier(repo, tmp_path, base_sha: str, head_sha: str):
    output = tmp_path / "github-output"
    env = os.environ.copy()
    env.update(
        {
            "COMPARISON_BASE": base_sha,
            "GITHUB_OUTPUT": str(output),
            "GITHUB_SHA": head_sha,
        }
    )
    subprocess.run(
        ["bash", "-c", _changed_file_script()],
        cwd=repo,
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    return dict(
        line.split("=", 1)
        for line in output.read_text(encoding="utf-8").splitlines()
    )


def _seed_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "unit@example.test")
    _git(repo, "config", "user.name", "Unit Test")
    (repo / "README.md").write_text("# unit\n", encoding="utf-8")
    (repo / "app.py").write_text("print('unit')\n", encoding="utf-8")
    base_sha = _commit(repo, "initial")
    return repo, base_sha


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


def test_changed_file_classifier_counts_deleted_python_paths(tmp_path):
    """Deleting code still requires the non-Markdown validation path."""
    repo, base_sha = _seed_repo(tmp_path)
    (repo / "app.py").unlink()
    head_sha = _commit(repo, "delete-python")

    outputs = _run_changed_file_classifier(repo, tmp_path, base_sha, head_sha)

    assert outputs["non_markdown_count"] == "1"


def test_changed_file_classifier_counts_python_to_markdown_renames(tmp_path):
    """A rename cannot hide the old Python path behind a Markdown destination."""
    repo, base_sha = _seed_repo(tmp_path)
    _git(repo, "mv", "app.py", "notes.md")
    head_sha = _commit(repo, "rename-python-to-markdown")

    outputs = _run_changed_file_classifier(repo, tmp_path, base_sha, head_sha)

    assert outputs["changed_count"] == "1"
    assert outputs["non_markdown_count"] == "1"


def test_validate_workflow_requires_live_receipt_validation_gate():
    """Merge validation must reject absent or stale live acceptance receipts."""
    text = _workflow_text()

    for required in (
        "live",
        "receipt",
        "execution_host",
        "tested_revision",
        "credential_sources",
        "live_checks",
        "required_checks",
    ):
        assert required in text
