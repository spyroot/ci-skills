"""Pipeline progress uses one selected GitLab session and validates API shape.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Optional, Union

import pytest
from conftest import import_script_module

PIPELINES = import_script_module("lib.python.core.gitlab.gitlab_pipelines")
GITLAB_SESSION = import_script_module("lib.python.core.gitlab.gitlab_session")
SCRIPT = import_script_module("gitlab_pipeline")


if TYPE_CHECKING:
    from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
    from lib.python.core.json_types import JSONObject, JSONValue
    from lib.python.core.target import Target


def _session() -> BoundGitLabSession:
    """Create the concrete GitLab session used by pipeline read fixtures.

    :returns: Bound session carrying target and credential provenance.
    """
    return GITLAB_SESSION.BoundGitLabSession(
        origin="https://gitlab.example.test",
        host="gitlab.example.test",
        target_kind="project",
        target_reference="unit/repo",
        target_source="fixture",
        target_file=Path("/selected/target.toml"),
        credential_source="env:GITLAB_TOKEN",
        credential_digest="sha256:unit",
        execution_host="unit-host",
        skill={"digest": "unit", "revision": {"value": "a" * 40, "verified": True}},
        environment={},
    )


def _job(job_id: int, status: str = "success", stage: str = "test") -> JSONObject:
    """Create a job record belonging to fixture pipeline 17.

    :param job_id: Job ID and suffix used in the generated name.
    :param status: Provider job status used for progress counts.
    :param stage: Stage name used for stage-level summaries.
    :returns: Minimal GitLab job record with pipeline relation.
    """
    return {
        "id": job_id,
        "name": f"job-{job_id}",
        "stage": stage,
        "status": status,
        "pipeline": {"id": 17},
    }


class PipelineAPI:
    """Return scripted pipeline and paged-job responses for bounded reads."""

    def __init__(self, pages: Optional[Mapping[int, JSONValue]] = None):
        """Set scripted job pages and start a provider request journal.

        :param pages: Page-number mapping; absent uses two default jobs.
        """
        self.pages = pages or {1: [_job(1), _job(2, "running", "deploy")]}
        self.calls: MutableSequence[tuple[BoundGitLabSession, str]] = []

    def get_json(self, session: BoundGitLabSession, endpoint: str) -> JSONValue:
        """Answer the selected pipeline detail or paginated jobs endpoint.

        :param session: Bound session recorded for identity assertions.
        :param endpoint: Pipeline detail or numbered jobs API path.
        :returns: Pipeline record or jobs page for the requested path.
        """
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
    """Write a target selecting one GitLab project.

    :param tmp_path: Directory receiving the target TOML file.
    :returns: Path to the written target.
    """
    path = tmp_path / "target.toml"
    path.write_text(
        '[gitlab]\nurl = "https://gitlab.example.test"\nproject = "unit/repo"\n',
        encoding="utf-8",
    )
    return path


def test_pipeline_read_summarizes_stages_without_fabricating_pipeline_health() -> None:
    """Count terminal jobs by stage while preserving provider pipeline status.
    :returns: None; assertions in the body validate the regression.
    """
    session = _session()
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
def test_pipeline_read_rejects_malformed_job_evidence(
    pages: Mapping[int, JSONValue],
) -> None:
    """Reject invalid page envelopes or jobs without required fields.

    :param pages: Malformed first-page mapping supplied to the API double.

    :returns: None; assertions in the body validate the regression.
    """
    with pytest.raises(
        ValueError, match="pipeline_jobs_.*invalid|pipeline_job_response_invalid"
    ):
        PIPELINES.read_pipeline(_session(), 42, 17, api_client=PipelineAPI(pages))


def test_pipeline_read_rejects_wrong_pipeline_reference() -> None:
    """Reject a job whose pipeline relation differs from the selected ID.
    :returns: None; assertions in the body validate the regression.
    """
    api = PipelineAPI({1: [{**_job(1), "pipeline": {"id": 99}}]})
    with pytest.raises(ValueError, match="pipeline_job_reference_mismatch"):
        PIPELINES.read_pipeline(_session(), 42, 17, api_client=api)


def test_pipeline_read_marks_overflow_partial_without_unbounded_pagination() -> None:
    """Stop after five full job pages and report overflow evidence.
    :returns: None; assertions in the body validate the regression.
    """
    pages = {
        page: [_job((page - 1) * 100 + item) for item in range(1, 101)]
        for page in range(1, 6)
    }
    pages[6] = [_job(501)]
    api = PipelineAPI(pages)

    record, errors = PIPELINES.read_pipeline(_session(), 42, 17, api_client=api)

    assert record["progress"]["total"] == 500
    assert errors == [{"source": "jobs", "reason": "job_limit_exceeded"}]
    assert len(api.calls) == 7  # one pipeline, five pages, one overflow probe


def test_pipeline_live_uses_selected_project_and_same_bound_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Use exact project access and the same bound session for pipeline read.

    :param tmp_path: Directory receiving a project target.
    :param monkeypatch: Pytest fixture replacing GitLab bind, gate, and read.
    :param capsys: Pytest fixture capturing the machine report.

    :returns: None; assertions in the body validate the regression.
    """
    path = _target(tmp_path)
    monkeypatch.setenv("CI_SKILLS_TARGET", str(path))
    observed: MutableMapping[str, JSONValue] = {}
    session = SimpleNamespace(
        environment={"GITLAB_TOKEN": "selected-secret"},
        execution_host="worker.example.test",
        target_source="env:CI_SKILLS_TARGET",
        skill={"revision": {"value": "a" * 40}},
    )

    def bind(
        _target: Target,
        **kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> SimpleNamespace:
        """Record project binding options and return the selected session.

        :param _target: Parsed target accepted by the fake binder.
        :param **kwargs: Keyword arguments forwarded to the fake command boundary.
        :returns: Bound fixture session with selected child environment.

        """
        observed["binding"] = kwargs
        return session

    def read(
        bound: BoundGitLabSession, project_id: int, pipeline_id: int
    ) -> tuple[JSONValue, ...]:
        """Record the selected pipeline read on the bound session.

        :param bound: Session passed from access checking to pipeline read.
        :param project_id: Verified project ID from access read-back.
        :param pipeline_id: Caller-selected pipeline ID.
        :returns: Successful fixture record and no read errors.
        """
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

    code = SCRIPT.main(["--pipeline-id", "17", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert code == 0
    assert data["status"] == "PASS"
    assert observed["binding"]["target_reference"] == "unit/repo"
    assert observed["read"] == (session, 42, 17)
    assert "selected-secret" not in json.dumps(data)


def test_pipeline_dry_run_never_binds_or_reads_gitlab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Show planned pipeline access without binding or provider reads.

    :param tmp_path: Directory receiving a project target.
    :param monkeypatch: Pytest fixture rejecting live bind and read calls.
    :param capsys: Pytest fixture capturing the dry-run report.

    :returns: None; assertions in the body validate the regression.
    """
    path = _target(tmp_path)
    monkeypatch.setenv("CI_SKILLS_TARGET", str(path))
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

    code = SCRIPT.main(["--pipeline-id", "17", "--dry-run", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert code == 0
    assert data["status"] == "DRY_RUN"
    assert data["access"]["observed_capability"] == []


def test_pipeline_missing_id_has_machine_failure_without_credential_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report missing pipeline ID as usage failure before credential binding.

    :param tmp_path: Directory receiving a valid project target.
    :param monkeypatch: Pytest fixture rejecting credential bind calls.
    :param capsys: Pytest fixture capturing the argument failure.

    :returns: None; assertions in the body validate the regression.
    """
    path = _target(tmp_path)
    monkeypatch.setenv("CI_SKILLS_TARGET", str(path))
    monkeypatch.setattr(
        SCRIPT,
        "bind_gitlab_session",
        lambda *_args, **_kwargs: pytest.fail("missing id bound credentials"),
    )

    code = SCRIPT.main(["--json"])
    data = json.loads(capsys.readouterr().out)

    assert code == 2
    assert data["status"] == "BLOCKED"
    assert data["errors"][0]["source"] == "arguments"


def test_pipeline_access_failure_skips_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Preserve a project access refusal without reading pipeline data.

    :param tmp_path: Directory receiving a project target.
    :param monkeypatch: Pytest fixture replacing access and rejecting reads.
    :param capsys: Pytest fixture capturing the blocked report.

    :returns: None; assertions in the body validate the regression.
    """
    path = _target(tmp_path)
    monkeypatch.setenv("CI_SKILLS_TARGET", str(path))
    session = SimpleNamespace(
        environment={"GITLAB_TOKEN": "selected-secret"},
        execution_host="worker.example.test",
        target_source="env:CI_SKILLS_TARGET",
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

    code = SCRIPT.main(["--pipeline-id", "17", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert code == 2
    assert data["status"] == "BLOCKED"
    assert data["errors"] == [{"source": "target", "reason": "target_path_mismatch"}]


def test_pipeline_malformed_provider_response_has_machine_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Render malformed jobs evidence as a structured pipeline failure.

    :param tmp_path: Directory receiving a project target.
    :param monkeypatch: Pytest fixture replacing bind, access, and API pages.
    :param capsys: Pytest fixture capturing the blocked report.

    :returns: None; assertions in the body validate the regression.
    """
    path = _target(tmp_path)
    monkeypatch.setenv("CI_SKILLS_TARGET", str(path))
    session = SimpleNamespace(
        environment={"GITLAB_TOKEN": "selected-secret"},
        execution_host="worker.example.test",
        target_source="env:CI_SKILLS_TARGET",
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

    code = SCRIPT.main(["--pipeline-id", "17", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert code == 2
    assert data["status"] == "BLOCKED"
    assert data["errors"][0] == {
        "source": "pipeline",
        "reason": "pipeline_jobs_envelope_invalid",
    }
