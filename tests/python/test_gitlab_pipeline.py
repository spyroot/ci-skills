"""Pipeline progress uses one selected GitLab session and validates API shape."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.python.conftest import import_script_module

PIPELINES = import_script_module("core.gitlab_pipelines")
SCRIPT = import_script_module("gitlab_pipeline")


def _job(job_id: int, status: str = "success", stage: str = "test") -> dict:
    return {
        "id": job_id,
        "name": f"job-{job_id}",
        "stage": stage,
        "status": status,
        "pipeline": {"id": 17},
    }


class PipelineAPI:
    def __init__(self, pages: dict[int, object] | None = None) -> None:
        self.pages = pages or {1: [_job(1), _job(2, "running", "deploy")]}
        self.calls: list[tuple[object, str]] = []

    def get_json(self, session, endpoint):
        self.calls.append((session, endpoint))
        if endpoint == "projects/42/pipelines/17":
            return {
                "id": 17,
                "project_id": 42,
                "status": "running",
                "ref": "main",
                "sha": "a" * 40,
            }
        prefix = "projects/42/pipelines/17/jobs?per_page=100&page="
        assert endpoint.startswith(prefix)
        return self.pages.get(int(endpoint.removeprefix(prefix)), [])


def _target(tmp_path: Path) -> Path:
    path = tmp_path / "target.toml"
    path.write_text(
        '[gitlab]\nurl = "https://gitlab.example.test"\nproject = "unit/repo"\n',
        encoding="utf-8",
    )
    return path


def test_pipeline_read_summarizes_stages_without_fabricating_pipeline_health():
    session = object()
    api = PipelineAPI()

    record, errors = PIPELINES.read_pipeline(session, 42, 17, api_client=api)

    assert errors == []
    assert record["status"] == "running"
    assert record["progress"] == {
        "total": 2,
        "terminal": 1,
        "terminal_percent": 50.0,
        "by_status": {"running": 1, "success": 1},
    }
    assert record["stages"] == [
        {
            "name": "deploy",
            "total": 1,
            "terminal": 0,
            "terminal_percent": 0.0,
            "by_status": {"running": 1},
        },
        {
            "name": "test",
            "total": 1,
            "terminal": 1,
            "terminal_percent": 100.0,
            "by_status": {"success": 1},
        },
    ]
    assert len(api.calls) == 2
    assert all(bound is session for bound, _endpoint in api.calls)


@pytest.mark.parametrize(
    "pages",
    [{1: {}}, {1: None}, {1: [{"id": 1, "status": "success"}]}],
)
def test_pipeline_read_rejects_malformed_job_evidence(pages):
    with pytest.raises(
        ValueError, match="pipeline_jobs_.*invalid|pipeline_job_response_invalid"
    ):
        PIPELINES.read_pipeline(object(), 42, 17, api_client=PipelineAPI(pages))


def test_pipeline_read_rejects_wrong_pipeline_reference():
    api = PipelineAPI({1: [{**_job(1), "pipeline": {"id": 99}}]})
    with pytest.raises(ValueError, match="pipeline_job_reference_mismatch"):
        PIPELINES.read_pipeline(object(), 42, 17, api_client=api)


def test_pipeline_read_marks_overflow_partial_without_unbounded_pagination():
    pages = {
        page: [_job((page - 1) * 100 + item) for item in range(1, 101)]
        for page in range(1, 6)
    }
    pages[6] = [_job(501)]
    api = PipelineAPI(pages)

    record, errors = PIPELINES.read_pipeline(object(), 42, 17, api_client=api)

    assert record["progress"]["total"] == 500
    assert errors == [{"source": "jobs", "reason": "job_limit_exceeded"}]
    assert len(api.calls) == 7  # one pipeline, five pages, one overflow probe


def test_pipeline_live_uses_selected_project_and_same_bound_session(
    tmp_path, monkeypatch, capsys
):
    path = _target(tmp_path)
    observed: dict[str, object] = {}
    session = SimpleNamespace(
        environment={"GITLAB_TOKEN": "selected-secret"},
        execution_host="worker.example.test",
        target_source="argv:--target",
        skill={"revision": {"value": "a" * 40}},
    )

    def bind(_target, **kwargs):
        observed["binding"] = kwargs
        return session

    def read(bound, project_id, pipeline_id):
        observed["read"] = (bound, project_id, pipeline_id)
        return {"id": pipeline_id, "status": "success", "progress": {"total": 0}}, []

    monkeypatch.setattr(SCRIPT, "bind_gitlab_session", bind)
    monkeypatch.setattr(
        SCRIPT,
        "check_gitlab_operation_access",
        lambda bound: {
            "status": "PASS",
            "errors": [],
            "target": {"kind": "project", "id": 42, "full_path": "unit/repo"},
        },
    )
    monkeypatch.setattr(SCRIPT, "read_pipeline", read)

    code = SCRIPT.main(["--target", str(path), "--pipeline-id", "17", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert code == 0
    assert data["status"] == "PASS"
    assert observed["binding"]["target_reference"] == "unit/repo"
    assert observed["read"] == (session, 42, 17)
    assert "selected-secret" not in json.dumps(data)


def test_pipeline_dry_run_never_binds_or_reads_gitlab(tmp_path, monkeypatch, capsys):
    path = _target(tmp_path)
    monkeypatch.setattr(
        SCRIPT,
        "bind_gitlab_session",
        lambda *_args, **_kwargs: pytest.fail("dry run bound credentials"),
    )
    monkeypatch.setattr(
        SCRIPT,
        "read_pipeline",
        lambda *_args, **_kwargs: pytest.fail("dry run read the pipeline"),
    )

    code = SCRIPT.main(
        ["--target", str(path), "--pipeline-id", "17", "--dry-run", "--json"]
    )
    data = json.loads(capsys.readouterr().out)

    assert code == 0
    assert data["status"] == "DRY_RUN"
    assert data["access"]["observed_capability"] == []


def test_pipeline_missing_id_has_machine_failure_without_credential_lookup(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        SCRIPT,
        "bind_gitlab_session",
        lambda *_args, **_kwargs: pytest.fail("missing id bound credentials"),
    )

    code = SCRIPT.main(["--target", str(_target(tmp_path)), "--json"])
    data = json.loads(capsys.readouterr().out)

    assert code == 2
    assert data["status"] == "BLOCKED"
    assert data["errors"][0]["source"] == "arguments"


def test_pipeline_access_failure_skips_reads(tmp_path, monkeypatch, capsys):
    path = _target(tmp_path)
    session = SimpleNamespace(
        environment={"GITLAB_TOKEN": "selected-secret"},
        execution_host="worker.example.test",
        target_source="argv:--target",
        skill={"revision": {"value": "a" * 40}},
    )
    monkeypatch.setattr(SCRIPT, "bind_gitlab_session", lambda *_a, **_k: session)
    monkeypatch.setattr(
        SCRIPT,
        "check_gitlab_operation_access",
        lambda _session: {
            "status": "BLOCKED",
            "errors": [{"source": "target", "reason": "target_path_mismatch"}],
        },
    )
    monkeypatch.setattr(
        SCRIPT,
        "read_pipeline",
        lambda *_a, **_k: pytest.fail("pipeline read ran after access failure"),
    )

    code = SCRIPT.main(["--target", str(path), "--pipeline-id", "17", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert code == 2
    assert data["status"] == "BLOCKED"
    assert data["errors"] == [{"source": "target", "reason": "target_path_mismatch"}]


def test_pipeline_malformed_provider_response_has_machine_failure(
    tmp_path, monkeypatch, capsys
):
    path = _target(tmp_path)
    session = SimpleNamespace(
        environment={"GITLAB_TOKEN": "selected-secret"},
        execution_host="worker.example.test",
        target_source="argv:--target",
        skill={"revision": {"value": "a" * 40}},
    )
    monkeypatch.setattr(SCRIPT, "bind_gitlab_session", lambda *_a, **_k: session)
    monkeypatch.setattr(
        SCRIPT,
        "check_gitlab_operation_access",
        lambda _session: {
            "status": "PASS",
            "errors": [],
            "target": {"kind": "project", "id": 42, "full_path": "unit/repo"},
        },
    )
    monkeypatch.setattr(
        SCRIPT,
        "read_pipeline",
        lambda *_a, **_k: PIPELINES.read_pipeline(
            session, 42, 17, api_client=PipelineAPI({1: {}})
        ),
    )

    code = SCRIPT.main(["--target", str(path), "--pipeline-id", "17", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert code == 2
    assert data["status"] == "BLOCKED"
    assert data["errors"][0] == {
        "source": "pipeline",
        "reason": "pipeline_jobs_envelope_invalid",
    }
