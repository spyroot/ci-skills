"""Deterministic graph and deadline contracts for GitLab pipeline watch."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, MutableSequence, Sequence
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from typing import TYPE_CHECKING, Optional

import pytest
from conftest import import_script_module

ACTION = import_script_module("lib.python.core.gitlab.gitlab_actions")
GRAPH = import_script_module("lib.python.core.gitlab.gitlab_pipeline_graph")
REPORT = import_script_module("lib.python.core.report")
SCRIPT = import_script_module("gitlab_pipeline")
TYPES = import_script_module("lib.python.core.gitlab.gitlab_pipeline_watch_types")
WATCH = import_script_module("lib.python.core.gitlab.gitlab_pipeline_watch")
WATCH_RUNTIME = import_script_module(
    "lib.python.core.gitlab.gitlab_pipeline_watch_runtime"
)
API = import_script_module("lib.python.core.gitlab.gitlab_api")
RUNTIME = import_script_module("lib.python.core.runtime")
SCHEMA = import_script_module("lib.python.core.schema")
TARGET = import_script_module("lib.python.core.target")

if TYPE_CHECKING:
    from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
    from lib.python.core.json_types import JSONObject, JSONValue

SHA = "a" * 40


class WatchAPI:
    """Delegate reads to a deterministic answer function."""

    def __init__(self, answer: Callable[[str, int], JSONValue]) -> None:
        """Retain the responder and initialize an endpoint journal."""
        self.answer = answer
        self.calls: MutableSequence[str] = []

    def get_json(
        self,
        _session: BoundGitLabSession,
        endpoint: str,
        *,
        deadline: object = None,
    ) -> JSONValue:
        """Record and answer one provider read."""
        del deadline
        self.calls.append(endpoint)
        return self.answer(endpoint, len(self.calls))


class Clock:
    """Advance deterministic time only when watch requests sleep."""

    def __init__(self) -> None:
        """Start time and sleep history at zero."""
        self.value = 0.0
        self.sleeps: MutableSequence[float] = []

    def monotonic(self) -> float:
        """Return deterministic elapsed seconds."""
        return self.value

    def sleep(self, seconds: float) -> None:
        """Record and apply one bounded delay."""
        self.sleeps.append(seconds)
        self.value += seconds


def _job(
    job_id: int,
    pipeline_id: int,
    *,
    status: str = "success",
    web_url: str | None = None,
) -> JSONObject:
    """Create one compact provider job response."""
    return {
        "id": job_id,
        "name": f"job-{job_id}",
        "stage": "test",
        "status": status,
        "failure_reason": "script_failure" if status == "failed" else None,
        "web_url": web_url,
        "pipeline": {"id": pipeline_id},
    }


def _pipeline(project_id: int, pipeline_id: int, status: str) -> JSONObject:
    """Create one compact provider pipeline response."""
    return {
        "id": pipeline_id,
        "project_id": project_id,
        "status": status,
        "ref": "main",
        "sha": SHA,
    }


def _single_pipeline_answer(status: str) -> Callable[[str, int], JSONValue]:
    """Build a responder for one root pipeline."""

    def answer(endpoint: str, _call: int) -> JSONValue:
        """Answer root detail, jobs, or bridges."""
        if endpoint == "projects/42/pipelines/17":
            return _pipeline(42, 17, status)
        if endpoint.startswith("projects/42/pipelines/17/jobs?"):
            return [
                _job(
                    101,
                    17,
                    status=status,
                    web_url=(
                        "https://gitlab.example.test/unit/repo/-/jobs/101"
                        if status == "failed"
                        else None
                    ),
                )
            ]
        if endpoint.startswith("projects/42/pipelines/17/bridges?"):
            return []
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    return answer


@pytest.mark.parametrize("interval", ("nan", "inf", "-inf"))
def test_watch_rejects_nonfinite_interval_before_provider_contact(
    interval: str,
) -> None:
    """Reject every nonfinite parser value before constructing provider work."""
    args = SCRIPT.build_parser().parse_args(
        ["watch", "--pipeline-id", "17", "--interval", interval]
    )
    with pytest.raises(
        ACTION.ActionError,
        match="pipeline_watch_interval_and_timeout_must_be_positive",
    ):
        WATCH._watch_action(args)


@pytest.mark.parametrize(
    ("status", "expected_success", "expected_choice_ids"),
    (("success", True, []), ("failed", False, ["job:101", "pipeline:17"])),
)
def test_terminal_pipeline_distinguishes_completion_from_success(
    status: str, expected_success: bool, expected_choice_ids: list[str]
) -> None:
    """Treat every terminal graph as complete while success stays strict."""
    api = WatchAPI(_single_pipeline_answer(status))
    settled: MutableSequence[str] = []
    result = WATCH_RUNTIME.watch_pipeline(
        api,
        object(),
        42,
        TYPES.PipelineWatchAction(17, None, 1.0, 10.0),
        monotonic=lambda: 0.0,
        sleep=lambda _seconds: pytest.fail("terminal pipeline requested sleep"),
        settled=settled.append,
    )
    assert result["complete"] is True
    assert result["success"] is expected_success
    assert result["polls"] == 1
    assert [choice["id"] for choice in result["choices"]] == expected_choice_ids
    assert any(line.startswith("pipeline project=42 id=17") for line in settled)


def test_human_report_preserves_mixed_pipeline_outcome_and_next_steps() -> None:
    """Render mixed settlement, transitions, choices, and bounded errors."""
    observations = (
        TYPES.PipelineObservation(
            route=TYPES.PipelineRoute(TYPES.PipelineKey(42, 17), "root", 0),
            status="failed",
            ref="main",
            sha=SHA,
            jobs=(
                TYPES.WatchJob(
                    id=101,
                    name="unit-failure",
                    stage="test",
                    status="failed",
                    failure_reason="script_failure",
                    web_url="https://gitlab.example.test/unit/repo/-/jobs/101",
                ),
            ),
            children=(),
            errors=(),
        ),
        TYPES.PipelineObservation(
            route=TYPES.PipelineRoute(TYPES.PipelineKey(42, 18), "related", 0),
            status="success",
            ref="main",
            sha=SHA,
            jobs=(),
            children=(),
            errors=(),
        ),
    )
    progress = WATCH_RUNTIME.WatchProgress(
        changes=[{"at_seconds": 4.5, "pipeline_id": 17, "status": "failed"}],
        change_count=1,
    )
    observed = WATCH_RUNTIME._watch_result(
        progress,
        observations,
        [{"source": "changes", "reason": "changes_limit_exceeded", "count": 51}],
        polls=3,
        elapsed=4.5,
    )
    data = REPORT.report(
        TYPES.PIPELINE_WATCH_KIND,
        "https://gitlab.example.test",
        {"project": "unit/repo", "pipeline_id": 17},
        observed["records"],
        observed["errors"],
    )
    data.update(observed)

    rendered = REPORT.human(data)

    assert "PARTIAL  complete: yes  success: no" in rendered
    assert "17  failed  root" in rendered
    assert "18  success  related" in rendered
    assert "Changes\n  4.5 s  17  failed" in rendered
    assert "Results\n  job:101" in rendered
    assert "Retrieve: gitlab_job.py --job-url" in rendered
    assert "pipeline:17" in rendered
    assert "Retrieve: gitlab_pipeline.py get --project 42 --pipeline-id 17" in rendered
    assert "Errors\n  changes: changes_limit_exceeded count=51" in rendered


def test_watch_timeout_is_bounded_by_remaining_deadline() -> None:
    """Stop a running graph at its deadline without an extra sleep."""
    api = WatchAPI(_single_pipeline_answer("running"))
    clock = Clock()
    result = WATCH_RUNTIME.watch_pipeline(
        api,
        object(),
        42,
        TYPES.PipelineWatchAction(17, None, 2.0, 5.0),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert result["complete"] is False
    assert result["success"] is False
    assert result["polls"] == 4
    assert result["elapsed_seconds"] == 5.0
    assert clock.sleeps == [2.0, 2.0, 1.0]


def test_watch_pagination_stops_after_overflow_probe() -> None:
    """Read five full pages and report one nonempty overflow page."""

    def answer(endpoint: str, _call: int) -> JSONValue:
        """Return five full pages followed by one overflow item."""
        page = int(endpoint.rsplit("page=", 1)[1])
        if page <= TYPES.MAX_PAGES:
            return [
                {"id": (page - 1) * TYPES.PAGE_SIZE + item}
                for item in range(TYPES.PAGE_SIZE)
            ]
        return [{"id": 501}]

    api = WatchAPI(answer)
    items, errors = GRAPH._paged(api, object(), "projects/42/pipelines", "related")
    assert len(items) == TYPES.MAX_PAGES * TYPES.PAGE_SIZE
    assert errors == [{"source": "related", "reason": "related_limit_exceeded"}]
    assert len(api.calls) == TYPES.MAX_PAGES + 1


def test_scope_follows_children_and_related_names_without_project_confusion() -> None:
    """Follow matching nodes, terminate cycles, and reject another project."""

    def answer(endpoint: str, _call: int) -> JSONValue:
        """Serve a root, one child, one related node, and one false match."""
        details = {
            "projects/42/pipelines/17": _pipeline(42, 17, "success"),
            "projects/42/pipelines/18": _pipeline(42, 18, "success"),
            "projects/43/pipelines/20": _pipeline(43, 20, "success"),
        }
        if endpoint in details:
            return details[endpoint]
        if endpoint.startswith("projects/42/pipelines/17/jobs?"):
            return [_job(101, 17)]
        if endpoint.startswith("projects/42/pipelines/18/jobs?"):
            return [_job(102, 18)]
        if endpoint.startswith("projects/43/pipelines/20/jobs?"):
            return [_job(103, 20)]
        if endpoint.startswith("projects/42/pipelines/17/bridges?"):
            return [
                {
                    "name": "deploy-child",
                    "downstream_pipeline": {"id": 20, "project_id": 43},
                }
            ]
        if endpoint.startswith("projects/43/pipelines/20/bridges?"):
            return [
                {
                    "name": "cycle-root",
                    "downstream_pipeline": {"id": 17, "project_id": 42},
                }
            ]
        if endpoint.startswith("projects/42/pipelines/18/bridges?"):
            return []
        if endpoint.startswith("projects/42/pipelines?ref=main&"):
            return [
                {"id": 18, "project_id": 42, "name": "acceptance-linux"},
                {"id": 19, "project_id": 99, "name": "acceptance-linux"},
                {"id": 16, "project_id": 42, "name": "acceptance-linux"},
                {"id": 21, "project_id": 42, "name": "unrelated"},
            ]
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    api = WatchAPI(answer)
    observations, errors = GRAPH._scope(
        api,
        object(),
        TYPES.PipelineRoute(TYPES.PipelineKey(42, 17), "root", 0),
        r"^acceptance-",
    )
    assert errors == []
    assert [observation.route.key for observation in observations] == [
        TYPES.PipelineKey(42, 17),
        TYPES.PipelineKey(42, 18),
        TYPES.PipelineKey(43, 20),
    ]
    assert len(api.calls) == 10
    assert not any(endpoint.startswith("projects/99/") for endpoint in api.calls)


def test_watch_starts_no_more_reads_after_the_shared_deadline() -> None:
    """A slow root read cannot start jobs or bridge requests after timeout."""
    clock = Clock()

    def answer(endpoint: str, _call: int) -> JSONValue:
        """Return a root only after consuming the entire invocation budget."""
        assert endpoint == "projects/42/pipelines/17"
        clock.value = 1.0
        return _pipeline(42, 17, "running")

    api = WatchAPI(answer)
    result = WATCH_RUNTIME.watch_pipeline(
        api,
        object(),
        42,
        TYPES.PipelineWatchAction(17, None, 0.5, 1.0),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert api.calls == ["projects/42/pipelines/17"]
    assert result["complete"] is False
    assert result["success"] is False
    assert any("deadline" in error["reason"] for error in result["errors"])
    assert result["elapsed_seconds"] == 1.0
    assert clock.sleeps == []


def test_watch_transport_caps_requests_and_retry_delays_by_whole_timeout() -> None:
    """A retryable read cannot exceed the remaining watch budget."""
    clock = Clock()
    timeouts: MutableSequence[float] = []

    def command(
        _argv: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, Optional[str]],
        max_stdout_bytes: int = API.MAX_RESPONSE_BYTES,
    ) -> RUNTIME.CommandResult:
        """Spend part of the budget and ask for an over-budget provider delay."""
        del env, max_stdout_bytes
        timeouts.append(timeout)
        clock.value += 0.75
        return RUNTIME.CommandResult(
            ("glab",),
            1,
            "HTTP/2 429 Too Many Requests\r\nRetry-After: 1\r\n\r\n{}",
            "",
        )

    api = API.GlabAPIClient(command=command, timeout=25, sleep=clock.sleep)
    result = WATCH_RUNTIME.watch_pipeline(
        api,
        SimpleNamespace(host="gitlab.example.test", environment={}),
        42,
        TYPES.PipelineWatchAction(17, None, 0.5, 1.0),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert timeouts == [1.0]
    assert sum(clock.sleeps) <= 0.25
    assert result["complete"] is False
    assert result["success"] is False
    assert any("deadline" in error["reason"] for error in result["errors"])
    assert result["elapsed_seconds"] <= 1.0


def test_watch_reads_jobs_and_bridges_concurrently_and_retains_both() -> None:
    """Independent collections rendezvous before either response is available."""
    both_started = Barrier(2, timeout=5)

    def answer(endpoint: str, _call: int) -> JSONValue:
        """Require two active collection readers without using fixed sleeps."""
        if endpoint == "projects/42/pipelines/17":
            return _pipeline(42, 17, "success")
        both_started.wait()
        if "/jobs?" in endpoint:
            return [_job(101, 17)]
        assert "/bridges?" in endpoint
        return []

    api = WatchAPI(answer)
    result = WATCH_RUNTIME.watch_pipeline(
        api,
        object(),
        42,
        TYPES.PipelineWatchAction(17, None, 1.0, 10.0),
        monotonic=lambda: 0.0,
        sleep=lambda _seconds: pytest.fail("settled graph requested sleep"),
    )
    assert result["complete"] is True
    assert result["success"] is True
    assert result["records"][0]["jobs"] == {
        "total": 1,
        "by_status": {"success": 1},
    }
    assert len(api.calls) == 3


def test_scope_overflow_reports_unique_dropped_pipelines_once() -> None:
    """Repeated bridge identities cannot inflate or repeat the overflow warning."""
    children = [
        {
            "name": f"child-{pipeline_id}",
            "downstream_pipeline": {"id": pipeline_id, "project_id": 42},
        }
        for pipeline_id in range(100, 100 + TYPES.MAX_PIPELINES + 3)
    ]

    def answer(endpoint: str, _call: int) -> JSONValue:
        """Offer fifty-three children and repeat the final four bridge routes."""
        if "/jobs?" in endpoint:
            return []
        if "/bridges?" in endpoint:
            return children + children[-4:] if "/17/" in endpoint else []
        pipeline_id = int(endpoint.rsplit("/", 1)[1])
        return _pipeline(42, pipeline_id, "success")

    observations, errors = GRAPH._scope(
        WatchAPI(answer),
        object(),
        TYPES.PipelineRoute(TYPES.PipelineKey(42, 17), "root", 0),
        None,
    )
    assert len(observations) == TYPES.MAX_PIPELINES
    assert errors == [{"source": "scope", "reason": "scope_limit_exceeded", "count": 4}]


@pytest.mark.parametrize(
    ("arguments", "reason"),
    (
        ([], "pipeline_id_must_be_positive"),
        (["--pipeline-id", "-1"], "pipeline_id_must_be_positive"),
        (
            ["--pipeline-id", "17", "--interval", "nan"],
            "pipeline_watch_interval_and_timeout_must_be_positive",
        ),
    ),
)
def test_invalid_watch_arguments_emit_schema_valid_blocked_report(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    reason: str,
) -> None:
    """Preflight failures retain the complete advertised watch envelope."""
    target = TARGET.GitLabOperationTarget(
        gitlab=TARGET.GitLabTarget(
            url="https://gitlab.example.test",
            host="gitlab.example.test",
            project="unit/repo",
        ),
        source_file=Path("/selected/target.toml"),
    )
    monkeypatch.setattr(
        SCRIPT, "_select_target", lambda _args: (target, "fixture", "unit/repo")
    )
    code = SCRIPT.main(["watch", *arguments, "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 2
    assert data["kind"] == "gitlab_pipeline_watch"
    assert data["status"] == "BLOCKED"
    assert data["complete"] is False
    assert data["success"] is False
    assert data["polls"] == 0
    assert data["records"] == []
    assert any(reason in error["reason"] for error in data["errors"])
    SCHEMA.validate_specification(data, "results/gitlab-pipeline-watch.schema.json")


def test_missing_watch_target_emits_schema_valid_blocked_report(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing optional GitLab configuration yields usable watch failure evidence."""

    def missing_target(_args: object) -> None:
        """Represent the target resolver's classified configuration failure."""
        raise TARGET.TargetError("gitlab_configuration_missing")

    monkeypatch.setattr(SCRIPT, "_select_target", missing_target)
    code = SCRIPT.main(["watch", "--pipeline-id", "17", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 2
    assert data["kind"] == "gitlab_pipeline_watch"
    assert data["status"] == "BLOCKED"
    assert data["complete"] is False
    assert data["success"] is False
    assert data["records"] == []
    assert data["errors"][0]["reason"] == "gitlab_configuration_missing"
    SCHEMA.validate_specification(data, "results/gitlab-pipeline-watch.schema.json")
