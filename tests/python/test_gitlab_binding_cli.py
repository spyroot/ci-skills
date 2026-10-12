"""GitLab entrypoints honor one selected project binding without API calls.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from conftest import import_script_module

ACCESS = import_script_module("gitlab_access")
CLI = import_script_module("lib.python.core.cli")
ISSUE = import_script_module("gitlab_issue")
JOB = import_script_module("gitlab_job")
PIPELINE = import_script_module("gitlab_pipeline")


def _selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create ambient and bound GitLab targets with a project binding.

    :param tmp_path: Scratch project directory receiving all target files.
    :param monkeypatch: Pytest fixture selecting cwd and clearing env selectors.
    :returns: Path to binding that selects the bound target.
    """
    project = tmp_path / "project"
    project.mkdir()
    ambient = project / ".ci-skills" / "target.toml"
    ambient.parent.mkdir()
    ambient.write_text(
        '[gitlab]\nurl = "https://ambient.example.test"\nproject = "team/ambient"\n',
        encoding="utf-8",
    )
    bound = project / "bound-target.toml"
    bound.write_text(
        '[gitlab]\nurl = "https://bound.example.test"\nproject = "team/bound"\n',
        encoding="utf-8",
    )
    binding = project / "binding.toml"
    binding.write_text(
        'schema_version = "1.0"\n'
        'target = "bound-target.toml"\n'
        "[kubernetes]\n"
        'sources = [{kind = "file", path = "missing-kubeconfig"}]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    monkeypatch.delenv("CI_SKILLS_TARGET", raising=False)
    monkeypatch.delenv("K8S_ADMIN_DIAGNOSTICS_BINDING", raising=False)
    return binding


def _invoke(kind: str, selector: Sequence[str]) -> int:
    """Call one GitLab entrypoint with the selected binding arguments.

    :param kind: Action, access, pipeline, or job command to invoke.
    :param selector: Explicit ``--binding`` tokens or empty env-selector case.
    :returns: Command exit code.
    """
    if kind == "action":
        return ISSUE.main(["open-bug", "--title", "test", "--json", *selector])
    if kind == "access":
        return ACCESS.main(["check", "--dry-run", "--json", *selector])
    if kind == "pipeline":
        return PIPELINE.main(["--pipeline-id", "17", "--dry-run", "--json", *selector])
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
    """Prefer explicit or env binding over the ambient project target.

    :param kind: GitLab entrypoint whose dry-run selection is checked.
    :param selection: Explicit flag or environment binding tier.
    :param tmp_path: Scratch project with ambient and bound targets.
    :param monkeypatch: Pytest fixture setting cwd and selected binding.
    :param capsys: Pytest fixture capturing the machine report.

    :returns: None; assertions in the body validate the regression.
    """
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
    """Block a missing selected binding instead of using ambient target.

    :param tmp_path: Scratch project and missing binding path.
    :param monkeypatch: Pytest fixture setting the binding selector.
    :param capsys: Pytest fixture capturing the blocked action result.

    :returns: None; assertions in the body validate the regression.
    """
    _selection(tmp_path, monkeypatch)
    monkeypatch.setenv("K8S_ADMIN_DIAGNOSTICS_BINDING", str(tmp_path / "missing.toml"))

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
    """Block simultaneous target and binding env selectors.

    :param tmp_path: Scratch project with target and binding files.
    :param monkeypatch: Pytest fixture setting conflicting variables.
    :param capsys: Pytest fixture capturing the blocked action result.

    :returns: None; assertions in the body validate the regression.
    """
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
