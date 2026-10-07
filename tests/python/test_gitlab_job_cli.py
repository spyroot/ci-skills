"""GitLab job CLI uses only the selected GitLab target and bound session."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from tests.python.conftest import import_script_module

CLI = import_script_module("core.cli")
COLLECT = import_script_module("core.collect")
REPORT = import_script_module("core.report")
RUNTIME = import_script_module("core.runtime")
JOBS = import_script_module("core.gitlab_jobs")
SCRIPT = import_script_module("gitlab_job")
TARGET = import_script_module("core.target")

JOB_URL = "https://gitlab.example.test/unit/repo/-/jobs/123"


def _target(tmp_path: Path) -> Path:
    path = tmp_path / "target.toml"
    path.write_text(
        '[github]\ninvalid = "ignored"\n'
        '[gitlab]\nurl = "https://gitlab.example.test"\n'
        'project = "unit/repo"\n'
        '[kubernetes]\ninvalid = "ignored"\n',
        encoding="utf-8",
    )
    return path


def _args(path: Path, *extra: str):
    return SCRIPT.build_parser().parse_args(
        ["--target", str(path), "--job-url", JOB_URL, "--json", *extra]
    )


def test_live_job_uses_exact_gitlab_project_access_and_bound_token(
    tmp_path, monkeypatch, capsys
):
    path = _target(tmp_path)
    assert TARGET.load_gitlab_target(path).gitlab.project == "unit/repo"
    with pytest.raises(TARGET.TargetError):
        TARGET.load_target(path)
    observed = {}
    session = SimpleNamespace(
        environment={"GITLAB_TOKEN": "selected-token"},
        execution_host="worker.example.test",
        target_source="argv:--target;argv:--job-url",
        skill={"revision": {"value": "a" * 40}},
    )

    def bind(_target, **kwargs):
        observed["binding"] = kwargs
        return session

    def collect(target, args, *, credential):
        observed["target"] = target
        observed["credential"] = credential
        return REPORT.report("gitlab_job", target.gitlab.url, {}, [{"job_id": 123}], [])

    monkeypatch.setattr(CLI, "bind_gitlab_session", bind)
    monkeypatch.setattr(
        CLI,
        "check_gitlab_operation_access",
        lambda bound: {
            "status": "PASS",
            "errors": [],
            "identity": {"id": 17},
            "target": {"kind": "project", "id": 42, "full_path": "unit/repo"},
        },
    )
    monkeypatch.setattr(COLLECT, "collect_gitlab_job", collect)
    monkeypatch.setattr(
        CLI, "bind_sources", lambda *_args, **_kwargs: pytest.fail("full bind used")
    )
    monkeypatch.setattr(
        CLI, "check_access", lambda *_args, **_kwargs: pytest.fail("full gate used")
    )

    result = CLI.execute_gitlab_job(_args(path))
    data = json.loads(capsys.readouterr().out)

    assert result == 0
    assert data["status"] == "PASS"
    assert data["access"]["target"]["id"] == 42
    assert observed["binding"]["target_kind"] == "project"
    assert observed["binding"]["target_reference"] == "unit/repo"
    assert observed["credential"] == {"GITLAB_TOKEN": "selected-token"}
    assert isinstance(observed["target"], TARGET.GitLabOperationTarget)
    assert "selected-token" not in json.dumps(data)


def test_job_access_failure_preserves_failed_subcheck_and_skips_collection(
    tmp_path, monkeypatch, capsys
):
    path = _target(tmp_path)
    session = SimpleNamespace(
        environment={"GITLAB_TOKEN": "selected-token"},
        execution_host="worker.example.test",
        target_source="argv:--target;argv:--job-url",
        skill={"revision": {"value": "a" * 40}},
    )
    monkeypatch.setattr(CLI, "bind_gitlab_session", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(
        CLI,
        "check_gitlab_operation_access",
        lambda _session: {
            "status": "BLOCKED",
            "errors": [{"source": "target", "reason": "target_path_mismatch"}],
        },
    )
    monkeypatch.setattr(
        COLLECT,
        "collect_gitlab_job",
        lambda *_args, **_kwargs: pytest.fail("job read ran after access failure"),
    )

    result = CLI.execute_gitlab_job(_args(path))
    data = json.loads(capsys.readouterr().out)

    assert result == 2
    assert data["status"] == "BLOCKED"
    assert data["errors"] == [{"source": "target", "reason": "target_path_mismatch"}]


def test_job_dry_run_never_binds_or_reads_gitlab(tmp_path, monkeypatch, capsys):
    path = _target(tmp_path)
    monkeypatch.setattr(
        CLI,
        "bind_gitlab_session",
        lambda *_args, **_kwargs: pytest.fail("dry run bound credentials"),
    )
    monkeypatch.setattr(
        CLI,
        "check_gitlab_operation_access",
        lambda *_args: pytest.fail("dry run read access"),
    )

    result = CLI.execute_gitlab_job(_args(path, "--dry-run"))
    data = json.loads(capsys.readouterr().out)

    assert result == 0
    assert data["status"] == "DRY_RUN"
    assert data["access"]["target"] == {
        "kind": "project",
        "reference": "unit/repo",
    }
    assert data["access"]["observed_capability"] == []


def test_job_url_must_match_selected_gitlab_host_before_binding(
    tmp_path, monkeypatch, capsys
):
    path = _target(tmp_path)
    monkeypatch.setattr(
        CLI,
        "bind_gitlab_session",
        lambda *_args, **_kwargs: pytest.fail("wrong host bound credentials"),
    )
    args = SCRIPT.build_parser().parse_args(
        [
            "--target",
            str(path),
            "--job-url",
            "https://other.example.test/unit/repo/-/jobs/123",
            "--json",
        ]
    )

    result = CLI.execute_gitlab_job(args)
    data = json.loads(capsys.readouterr().out)

    assert result == 2
    assert data["errors"][0]["source"] == "arguments"
    assert "exact selected GitLab FQDN" in data["errors"][0]["reason"]


def test_bound_job_collector_checks_runner_on_exact_host(tmp_path, monkeypatch):
    target = TARGET.load_gitlab_target(_target(tmp_path))
    calls = []

    def read(argv, **kwargs):
        calls.append((tuple(argv), kwargs["env"]))
        endpoint = argv[-1]
        payload = {
            "projects/unit%2Frepo/jobs/123": {
                "id": 123,
                "runner": {"id": 9},
                "pipeline": {"id": 77},
            },
            "projects/unit%2Frepo/pipelines/77": {"id": 77, "status": "failed"},
            "runners/9": {"id": 9, "description": "selected-runner"},
        }[endpoint]
        return RUNTIME.CommandResult(tuple(argv), 0, json.dumps(payload), "")

    def trace(argv, **kwargs):
        calls.append((tuple(argv), kwargs["env"]))
        return RUNTIME.CommandResult(tuple(argv), 0, "trace line\n", "")

    monkeypatch.setattr(COLLECT, "run_command", read)
    monkeypatch.setattr(COLLECT, "run_command_tail", trace)
    result = COLLECT.collect_gitlab_job(
        target,
        SimpleNamespace(job_url=JOB_URL, search=None),
        credential={"GITLAB_TOKEN": "selected-token"},
    )

    assert result["status"] == "PASS"
    assert result["records"][0]["runner"]["id"] == 9
    assert {argv[-1] for argv, _env in calls} == {
        "projects/unit%2Frepo/jobs/123",
        "projects/unit%2Frepo/pipelines/77",
        "runners/9",
        "projects/unit%2Frepo/jobs/123/trace",
    }
    assert all(
        argv[argv.index("--hostname") + 1] == "gitlab.example.test"
        and env == {"GITLAB_TOKEN": "selected-token"}
        for argv, env in calls
    )
    assert "selected-token" not in json.dumps(result)


@pytest.mark.parametrize("mode", ("--json", "--yaml"))
def test_list_uses_bound_project_and_emits_schema_valid_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    """Read the selected project once and emit the declared agent result.

    :param tmp_path: Isolated target file directory.
    :param monkeypatch: Replacement for live GitLab access.
    :param capsys: Captured command output.
    :param mode: Requested machine output mode.
    """
    path = _target(tmp_path)
    session = SimpleNamespace(
        environment={"GITLAB_TOKEN": "selected-token"},
        execution_host="worker.example.test",
        target_source="argv:--target;argv:--project",
        skill={
            "digest": "a" * 64,
            "file_count": 1,
            "revision": {"value": "b" * 40, "source": "test", "verified": True},
        },
    )
    observed: dict[str, Any] = {}

    def bind(_target: object, **kwargs: object) -> SimpleNamespace:
        """Return the same bound session the command must use.

        :param _target: Parsed target configuration.
        :param kwargs: Selected project binding values.
        :returns: Test session for this invocation.
        """
        observed["binding"] = kwargs
        return session

    def list_selected(bound: object, project_id: int, query: Any) -> Any:
        """Capture the exact selected project job read.

        :param bound: Bound GitLab session.
        :param project_id: Verified project ID.
        :param query: Validated job-list query.
        :returns: Empty valid job-list result.
        """
        observed["read"] = (bound, project_id, query.limit)
        return JOBS.JobListResult([], [], False)

    monkeypatch.setattr(CLI, "bind_gitlab_session", bind)
    monkeypatch.setattr(
        CLI,
        "check_gitlab_operation_access",
        lambda _bound: {
            "status": "PASS",
            "errors": [],
            "identity": {
                "id": 17,
                "username": "operator",
                "web_url": "https://gitlab.example.test/operator",
            },
            "target": {
                "kind": "project",
                "id": 42,
                "full_path": "unit/repo",
                "web_url": "https://gitlab.example.test/unit/repo",
            },
            "observed_capability": ["identity_read", "target_read"],
        },
    )
    monkeypatch.setattr(JOBS, "list_jobs", list_selected)
    args = SCRIPT.build_parser().parse_args(
        ["list", "--target", str(path), "--project", "unit/repo", "--limit", "7", mode]
    )

    assert CLI.execute_gitlab_job(args) == 0
    output = capsys.readouterr().out
    data = json.loads(output) if mode == "--json" else yaml.safe_load(output)
    schema = json.loads(
        (
            Path(__file__).parents[2] / "schemas/results/gitlab-job-list.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(data)
    assert data["status"] == "PASS"
    assert data["operation"] == "list"
    assert observed["binding"]["target_reference"] == "unit/repo"
    assert observed["read"] == (session, 42, 7)
    assert "selected-token" not in output


def test_list_access_refusal_skips_job_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Preserve a GitLab access refusal without reading project jobs.

    :param tmp_path: Isolated target file directory.
    :param monkeypatch: Replacement for live GitLab access.
    :param capsys: Captured blocked result.
    """
    path = _target(tmp_path)
    session = SimpleNamespace(
        environment={"GITLAB_TOKEN": "selected-token"},
        execution_host="worker.example.test",
        target_source="argv:--target;argv:--project",
        skill={"revision": {"value": "b" * 40}},
    )
    monkeypatch.setattr(CLI, "bind_gitlab_session", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(
        CLI,
        "check_gitlab_operation_access",
        lambda _bound: {
            "status": "BLOCKED",
            "errors": [{"source": "target", "reason": "target_path_mismatch"}],
            "identity": None,
            "target": None,
            "observed_capability": [],
        },
    )
    monkeypatch.setattr(
        JOBS,
        "list_jobs",
        lambda *_args: pytest.fail("job read ran after access refusal"),
    )
    args = SCRIPT.build_parser().parse_args(
        ["list", "--target", str(path), "--project", "unit/repo", "--json"]
    )

    assert CLI.execute_gitlab_job(args) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "BLOCKED"
    assert data["records"] == []
    assert data["errors"] == [{"source": "target", "reason": "target_path_mismatch"}]
