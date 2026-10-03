"""GitLab entrypoints honor one selected project binding without API calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import import_script_module

ACCESS = import_script_module("gitlab_access")
ACTION = import_script_module("core.gitlab_actions")
CLI = import_script_module("core.cli")
JOB = import_script_module("gitlab_job")
PIPELINE = import_script_module("gitlab_pipeline")


def _selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    ambient = project / ".ci-skills" / "target.toml"
    ambient.parent.mkdir()
    ambient.write_text(
        '[gitlab]\nurl = "https://ambient.example.test"\n'
        'project = "team/ambient"\n',
        encoding="utf-8",
    )
    bound = project / "bound-target.toml"
    bound.write_text(
        '[gitlab]\nurl = "https://bound.example.test"\n'
        'project = "team/bound"\n',
        encoding="utf-8",
    )
    binding = project / "binding.toml"
    binding.write_text(
        'schema_version = "1.0"\n'
        'target = "bound-target.toml"\n'
        '[kubernetes]\n'
        'sources = [{kind = "file", path = "missing-kubeconfig"}]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    monkeypatch.delenv("CI_SKILLS_TARGET", raising=False)
    monkeypatch.delenv("K8S_ADMIN_DIAGNOSTICS_BINDING", raising=False)
    return binding


def _invoke(kind: str, selector: list[str]) -> int:
    if kind == "action":
        return ACTION.run_action_cli(
            "gitlab_issue", ["open-bug", "--title", "test", "--json", *selector]
        )
    if kind == "access":
        return ACCESS.main(["check", "--dry-run", "--json", *selector])
    if kind == "pipeline":
        return PIPELINE.main(
            ["--pipeline-id", "17", "--dry-run", "--json", *selector]
        )
    args = JOB.build_parser().parse_args(
        [
            "--job-url",
            "https://bound.example.test/team/bound/-/jobs/123",
            "--dry-run",
            "--json",
            *selector,
        ]
    )
    return CLI.execute_gitlab_job(args)


@pytest.mark.parametrize("kind", ("action", "access", "pipeline", "job"))
@pytest.mark.parametrize("selection", ("explicit", "environment"))
def test_gitlab_entrypoint_uses_selected_binding_over_project_target(
    kind: str,
    selection: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    binding = _selection(tmp_path, monkeypatch)
    if selection == "explicit":
        selector = ["--binding", str(binding)]
        source = "argv:--binding"
        monkeypatch.setenv("K8S_ADMIN_DIAGNOSTICS_BINDING", str(tmp_path / "missing"))
    else:
        selector = []
        source = "env:K8S_ADMIN_DIAGNOSTICS_BINDING"
        monkeypatch.setenv("K8S_ADMIN_DIAGNOSTICS_BINDING", str(binding))

    code = _invoke(kind, selector)
    evidence = json.loads(capsys.readouterr().out)

    assert code == 0
    assert evidence["status"] == "DRY_RUN"
    assert evidence["target_source"] == (
        f"{source} -> binding:{binding.resolve()}"
        if kind != "action"
        else f"{source} -> binding:{binding.resolve()};target:gitlab.project"
    )
    if kind in {"action", "access"}:
        assert evidence["target"]["reference"] == "team/bound"
        assert evidence["target_file"] == str(
            (binding.parent / "bound-target.toml").resolve()
        )
    elif kind == "pipeline":
        assert evidence["target"] == "https://bound.example.test"
        assert evidence["filters"]["project"] == "team/bound"
    else:
        assert evidence["target"] == "https://bound.example.test"
        assert evidence["access"]["target"]["reference"] == "team/bound"


def test_missing_binding_blocks_without_falling_back_to_project_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _selection(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "K8S_ADMIN_DIAGNOSTICS_BINDING", str(tmp_path / "missing.toml")
    )

    code = _invoke("action", [])
    evidence = json.loads(capsys.readouterr().out)

    assert code == 2
    assert evidence["status"] == "BLOCKED"
    assert "binding_unavailable_or_invalid" in evidence["errors"][0]["reason"]


def test_conflicting_environment_selectors_block_without_target_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    binding = _selection(tmp_path, monkeypatch)
    monkeypatch.setenv("K8S_ADMIN_DIAGNOSTICS_BINDING", str(binding))
    monkeypatch.setenv(
        "CI_SKILLS_TARGET", str(binding.parent / ".ci-skills" / "target.toml")
    )

    code = _invoke("action", [])
    evidence = json.loads(capsys.readouterr().out)

    assert code == 2
    assert evidence["status"] == "BLOCKED"
    assert evidence["errors"][0]["reason"] == "environment_selector_conflict"
