"""Deterministic graph and deadline contracts for GitLab pipeline watch."""

from __future__ import annotations

from collections.abc import Callable, MutableSequence
from typing import TYPE_CHECKING

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

    def get_json(self, _session: BoundGitLabSession, endpoint: str) -> JSONValue:
        """Record and answer one provider read."""
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
