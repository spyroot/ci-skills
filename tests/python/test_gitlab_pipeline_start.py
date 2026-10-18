"""Focused contracts for planning and starting one exact GitLab pipeline."""

from __future__ import annotations

import argparse
import fcntl
from collections.abc import Mapping, MutableSequence, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Union

import pytest
from conftest import import_script_module

START = import_script_module("lib.python.core.gitlab.gitlab_pipeline_start")
CALLBACK = import_script_module("lib.python.core.gitlab.callback")
SESSION = import_script_module("lib.python.core.gitlab.gitlab_session")
TARGET = import_script_module("lib.python.core.target")
ACTION = import_script_module("lib.python.core.gitlab.gitlab_actions")
API = import_script_module("lib.python.core.gitlab.gitlab_api")
SCRIPT = import_script_module("gitlab_pipeline")
SCHEMA = import_script_module("lib.python.core.schema")
WORKFLOW = import_script_module("lib.python.core.gitlab.gitlab_action")

if TYPE_CHECKING:
    from lib.python.core.gitlab.callback import GitLabState
    from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
    from lib.python.core.json_types import JSONObject, JSONValue

SHA = "a" * 40
PLAN_VARIABLE = "CI_SKILLS_PIPELINE_PLAN_DIGEST"
PIPELINE_LIST = (
    f"projects/42/pipelines?ref=main&sha={SHA}&source=api&per_page=100&page=1"
)


@pytest.fixture(autouse=True)
def isolated_create_locks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep real create guards and pending markers inside each test directory."""
    tmp_path.chmod(0o700)
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path)


def _marker(state: GitLabState) -> JSONObject:
    """Return the provider variable that binds a pipeline to its exact plan."""
    return {
        "key": PLAN_VARIABLE,
        "value": state.plan.digest,
        "variable_type": "env_var",
    }


def _session() -> BoundGitLabSession:
    """Return a bound nonsecret GitLab test session."""
    return SESSION.BoundGitLabSession(
        origin="https://gitlab.example.test",
        host="gitlab.example.test",
        target_kind="project",
        target_reference="unit/repo",
        target_source="fixture",
        target_file=Path("/selected/target.toml"),
        credential_source="env:GITLAB_TOKEN",
        credential_digest="sha256:unit",
        execution_host="unit-host",
        skill={
            "algorithm": "sha256-tree-v1",
            "digest": "d" * 64,
            "functional_digest": "e" * 64,
            "file_count": 1,
            "revision": {"value": SHA, "source": "fixture", "verified": True},
            "consuming_project": {"commit": None, "source": None},
        },
        environment={},
    )


def _target() -> TARGET.GitLabOperationTarget:
    """Return a GitLab-only target without credential lookup."""
    return TARGET.GitLabOperationTarget(
        gitlab=TARGET.GitLabTarget(
            url="https://gitlab.example.test",
            host="gitlab.example.test",
            project="unit/repo",
        ),
        source_file=Path("/selected/target.toml"),
    )


def _plan(body: JSONObject) -> ACTION.ActionPlan:
    """Build one project-scoped pipeline start plan."""
    return ACTION.ActionPlan(
        kind=START.PIPELINE_KIND,
        operation="start",
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="unit/repo",
        target_file="/selected/target.toml",
        target_source="fixture",
        body=body,
        revision=SHA,
    )


class StartAPI:
    """Serve scripted provider calls with an exact call journal."""

    def __init__(
        self,
        responses: Mapping[tuple[str, str], Sequence[Union[JSONValue, BaseException]]],
    ) -> None:
        """Retain ordered results by method and endpoint."""
        self.responses = {key: list(values) for key, values in responses.items()}
        self.calls: MutableSequence[tuple[str, str, JSONValue]] = []

    def _next(self, method: str, endpoint: str, body: JSONValue = None) -> JSONValue:
        """Record a call and consume its next response."""
        self.calls.append((method, endpoint, body))
        value = self.responses[(method, endpoint)].pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def get_json(self, _session: BoundGitLabSession, endpoint: str) -> JSONValue:
        """Return one scripted GET response."""
        return self._next("GET", endpoint)

    def post_json(
        self, _session: BoundGitLabSession, endpoint: str, body: JSONValue
    ) -> JSONValue:
        """Return one scripted POST response."""
        return self._next("POST", endpoint, body)


def _state(api: StartAPI, body: JSONObject) -> GitLabState:
    """Bind a direct plan and provider double to shared state."""
    state = CALLBACK.GitLabState(args=argparse.Namespace())
    state.plan = _plan(body)
    state.gitlab = SESSION.GitLabService(api, _session(), 42)
    return state


def _pipeline(pipeline_id: int = 73) -> JSONObject:
    """Return a provider pipeline at the selected ref and commit."""
    return {
        "id": pipeline_id,
        "project_id": 42,
        "ref": "main",
        "sha": SHA,
        "status": "running",
    }


def test_pipeline_parser_preserves_bare_and_explicit_get() -> None:
    """Keep historical bare reads equivalent to explicit get."""
    bare = SCRIPT.build_parser().parse_args(["--pipeline-id", "17"])
    explicit = SCRIPT.build_parser().parse_args(["get", "--pipeline-id", "17"])
    assert (bare.action, bare.pipeline_id) == ("get", 17)
    assert (explicit.action, explicit.pipeline_id) == ("get", 17)


def test_offline_start_prepares_without_provider_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build a dry-run plan without resolving a ref or contacting GitLab."""
    args = SCRIPT.build_parser().parse_args(
        ["start", "--ref", "main", "--variable", "DEPLOY=unit", "--json"]
    )
    monkeypatch.setattr(
        START,
        "resolve_gitlab_target",
        lambda *_args, **_kwargs: (_target(), "fixture"),
    )
    monkeypatch.setattr(
        START,
        "_resolved_start_plan",
        lambda _state: pytest.fail("offline start contacted the provider"),
    )
    workflow = START.start_workflow(args)
    workflow.prepare()
    assert workflow.mode == START.ActionMode.OFFLINE
    assert workflow.state.report["status"] == "DRY_RUN"
    assert workflow.state.plan.body["variables"] == [{"key": "DEPLOY", "value": "unit"}]
    with pytest.raises(ACTION.ActionError, match="offline_workflow"):
        workflow.callbacks()


def test_live_resolution_binds_default_ref_sha_and_protection() -> None:
    """Resolve an omitted ref to the protected default branch and exact SHA."""
    api = StartAPI(
        {
            ("GET", "projects/42"): [{"id": 42, "default_branch": "release/1.0"}],
            ("GET", "projects/42/repository/commits/release%2F1.0"): [{"id": SHA}],
            ("GET", "projects/42/repository/branches/release%2F1.0"): [
                {"protected": True}
            ],
        }
    )
    state = _state(
        api,
        {
            "requested_ref": None,
            "variables": [],
            "require_protected_ref": True,
            "expected_sha": SHA,
        },
    )
    resolved = START._resolved_start_plan(state)
    assert resolved.body["ref"] == "release/1.0"
    assert resolved.body["resolved_sha"] == SHA
    assert resolved.body["protected_ref"] is True
    assert [(method, endpoint) for method, endpoint, _body in api.calls] == [
        ("GET", "projects/42"),
        ("GET", "projects/42/repository/commits/release%2F1.0"),
        ("GET", "projects/42/repository/branches/release%2F1.0"),
    ]


def test_live_resolution_rejects_expected_sha_drift_before_apply() -> None:
    """Block when the selected ref no longer matches the expected SHA."""
    api = StartAPI(
        {
            ("GET", "projects/42"): [{"id": 42, "default_branch": "main"}],
            ("GET", "projects/42/repository/commits/main"): [{"id": "b" * 40}],
        }
    )
    state = _state(
        api,
        {
            "requested_ref": "main",
            "variables": [],
            "require_protected_ref": True,
            "expected_sha": SHA,
        },
    )
    with pytest.raises(ACTION.ActionError, match="pipeline_expected_sha_mismatch"):
        START._resolved_start_plan(state)
    assert [method for method, _endpoint, _body in api.calls] == ["GET", "GET"]


def test_apply_sequence_confirms_after_resolution_and_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep exact-plan confirmation between resolution and the write."""
    args = SCRIPT.build_parser().parse_args(
        ["start", "--ref", "main", "--apply", "--confirm-plan", "f" * 64]
    )
    monkeypatch.setattr(
        START,
        "resolve_gitlab_target",
        lambda *_args, **_kwargs: (_target(), "fixture"),
    )
    workflow = START.start_workflow(args)
    workflow.prepare()
    assert [type(callback).__name__ for callback in workflow.callbacks()] == [
        "GitLabAuth",
        "ResolvePipelineStart",
        "ConfirmGitLabOperation",
        "StartPipeline",
    ]


def test_start_posts_once_and_verifies_exact_get_readback() -> None:
    """Create one pipeline and verify its ref and SHA by independent GET."""
    created = {
        "id": 73,
        "project_id": 42,
        "ref": "main",
        "sha": SHA,
        "status": "pending",
    }
    api = StartAPI(
        {
            ("POST", "projects/42/pipeline"): [created],
            ("GET", "projects/42/pipelines/73"): [{**created, "status": "running"}],
        }
    )
    state = _state(
        api,
        {
            "ref": "main",
            "resolved_sha": SHA,
            "variables": [{"key": "DEPLOY", "value": "unit"}],
        },
    )
    api.responses[("GET", PIPELINE_LIST)] = [[]]
    api.responses[("GET", "projects/42/pipelines/73/variables")] = [[_marker(state)]]
    result = START.start_pipeline(state)
    assert result["action"] == "APPLIED"
    assert result["verified"] is True
    assert result["after"]["status"] == "running"
    assert api.calls == [
        ("GET", PIPELINE_LIST, None),
        (
            "POST",
            "projects/42/pipeline",
            {
                "ref": "main",
                "variables": [{"key": "DEPLOY", "value": "unit"}, _marker(state)],
            },
        ),
        ("GET", "projects/42/pipelines/73", None),
        ("GET", "projects/42/pipelines/73/variables", None),
    ]


def test_start_rejects_readback_drift_after_known_write() -> None:
    """Fail verification while preserving that the write occurred."""
    created = {
        "id": 73,
        "project_id": 42,
        "ref": "main",
        "sha": SHA,
        "status": "pending",
    }
    api = StartAPI(
        {
            ("POST", "projects/42/pipeline"): [created],
            ("GET", "projects/42/pipelines/73"): [{**created, "sha": "b" * 40}],
        }
    )
    state = _state(api, {"ref": "main", "resolved_sha": SHA, "variables": []})
    api.responses[("GET", PIPELINE_LIST)] = [[]]
    with pytest.raises(ACTION.ActionError, match="pipeline_readback_identity_mismatch"):
        START.start_pipeline(state)
    assert state.mutated is True


def test_start_rejects_readback_pipeline_id_drift() -> None:
    """Reject a positive GET identity that differs from the created pipeline."""
    created = {
        "id": 73,
        "project_id": 42,
        "ref": "main",
        "sha": SHA,
        "status": "pending",
    }
    api = StartAPI(
        {
            ("POST", "projects/42/pipeline"): [created],
            ("GET", "projects/42/pipelines/73"): [{**created, "id": 74}],
        }
    )
    state = _state(api, {"ref": "main", "resolved_sha": SHA, "variables": []})
    api.responses[("GET", PIPELINE_LIST)] = [[]]
    with pytest.raises(ACTION.ActionError, match="pipeline_readback_identity_mismatch"):
        START.start_pipeline(state)
    assert state.mutated is True


def test_start_preserves_unknown_mutation_when_post_fails() -> None:
    """Keep mutation unknown when creation fails at the provider boundary."""
    api = StartAPI(
        {("POST", "projects/42/pipeline"): [API.GitLabAPIError("provider_5xx")]}
    )
    state = _state(api, {"ref": "main", "resolved_sha": SHA, "variables": []})
    api.responses[("GET", PIPELINE_LIST)] = [[], []]
    with pytest.raises(API.GitLabAPIError, match="pipeline_start_outcome_uncertain"):
        START.start_pipeline(state)
    assert state.mutated is None
    assert [method for method, _endpoint, _body in api.calls] == ["GET", "POST", "GET"]


def test_start_repeated_plan_posts_once_then_returns_verified_no_op() -> None:
    """Retrying the same confirmed inputs reuses the independently verified ID."""
    pipeline = _pipeline()
    api = StartAPI(
        {
            ("GET", PIPELINE_LIST): [[], [pipeline]],
            ("POST", "projects/42/pipeline"): [pipeline, pipeline],
            ("GET", "projects/42/pipelines/73"): [pipeline, pipeline],
        }
    )
    body = {"ref": "main", "resolved_sha": SHA, "variables": []}
    first, second = _state(api, body), _state(api, body)
    api.responses[("GET", "projects/42/pipelines/73/variables")] = [
        [_marker(first)],
        [_marker(first)],
        [_marker(first)],
    ]
    assert START.start_pipeline(first)["action"] == "APPLIED"
    result = START.start_pipeline(second)
    assert result["action"] == "NO_OP"
    assert (result["id"], result["verified"], result["mutated"]) == (73, True, False)
    assert second.mutated is False
    assert [method for method, _endpoint, _body in api.calls].count("POST") == 1


def test_start_distinct_explicit_run_ids_create_distinct_intended_builds() -> None:
    """Caller run IDs distinguish otherwise identical starts without random state."""
    api = StartAPI(
        {
            ("GET", PIPELINE_LIST): [[], [_pipeline()]],
            ("POST", "projects/42/pipeline"): [_pipeline(), _pipeline(74)],
            ("GET", "projects/42/pipelines/73"): [_pipeline()],
            ("GET", "projects/42/pipelines/74"): [_pipeline(74)],
        }
    )
    states = []
    for run_id in ("build-a", "build-b"):
        args = SCRIPT.build_parser().parse_args(["start", "--run-id", run_id])
        plan = START.prepare_start_plan(args, _target(), "fixture")
        state = _state(api, {})
        state.plan = replace(
            plan, body={**plan.body, "ref": "main", "resolved_sha": SHA}
        )
        states.append(state)
    first, second = states
    assert first.plan.digest != second.plan.digest
    api.responses[("GET", "projects/42/pipelines/73/variables")] = [
        [_marker(first)],
        [_marker(first)],
    ]
    api.responses[("GET", "projects/42/pipelines/74/variables")] = [[_marker(second)]]
    assert START.start_pipeline(first)["id"] == 73
    assert START.start_pipeline(second)["id"] == 74
    requests = [body for method, _endpoint, body in api.calls if method == "POST"]
    assert len(requests) == 2
    assert [body["variables"] for body in requests] == [
        [_marker(first)],
        [_marker(second)],
    ]


def test_start_lost_post_response_reconciles_the_created_id_without_reposting(
    tmp_path: Path,
) -> None:
    """A timed-out POST can already have created its marked pipeline."""
    pipeline = _pipeline()
    api = StartAPI(
        {
            ("GET", PIPELINE_LIST): [[], [pipeline]],
            ("POST", "projects/42/pipeline"): [API.GitLabAPIError("timeout")],
            ("GET", "projects/42/pipelines/73"): [pipeline],
        }
    )
    state = _state(api, {"ref": "main", "resolved_sha": SHA, "variables": []})
    api.responses[("GET", "projects/42/pipelines/73/variables")] = [
        [_marker(state)],
        [_marker(state)],
    ]
    result = START.start_pipeline(state)
    assert (result["action"], result["id"], result["verified"]) == ("APPLIED", 73, True)
    assert state.mutated is True
    assert [method for method, _endpoint, _body in api.calls].count("POST") == 1
    assert next(tmp_path.glob("*.lock")).read_bytes() == b""


def test_start_rejects_reserved_marker_before_provider_calls() -> None:
    """The caller cannot overwrite the variable used for plan reconciliation."""
    args = SCRIPT.build_parser().parse_args(
        ["start", "--variable", f"{PLAN_VARIABLE}=caller-value"]
    )
    with pytest.raises(ACTION.ActionError, match="pipeline_variable_reserved"):
        START.prepare_start_plan(args, _target(), "fixture")
    api = StartAPI({})
    state = _state(
        api,
        {
            "ref": "main",
            "resolved_sha": SHA,
            "variables": [{"key": PLAN_VARIABLE, "value": "caller-value"}],
        },
    )
    with pytest.raises(ACTION.ActionError, match="pipeline_variable_reserved"):
        START.start_pipeline(state)
    assert api.calls == []


def test_start_ambiguous_plan_matches_refuse_another_post() -> None:
    """Two pipelines with the same marker cannot be safely selected or recreated."""
    api = StartAPI({("GET", PIPELINE_LIST): [[_pipeline(), _pipeline(74)]]})
    state = _state(api, {"ref": "main", "resolved_sha": SHA, "variables": []})
    for pipeline_id in (73, 74):
        api.responses[("GET", f"projects/42/pipelines/{pipeline_id}/variables")] = [
            [_marker(state)]
        ]
    with pytest.raises(ACTION.ActionError, match="pipeline_plan_identity_ambiguous"):
        START.start_pipeline(state)
    assert all(method == "GET" for method, _endpoint, _body in api.calls)
    assert state.mutated is False


@pytest.mark.parametrize("failure", ("page-bound", "variable-read"))
def test_start_incomplete_lookup_refuses_a_post(failure: str) -> None:
    """Neither an exhausted page bound nor a failed marker read proves absence."""
    api = StartAPI({})
    state = _state(api, {"ref": "main", "resolved_sha": SHA, "variables": []})
    if failure == "page-bound":
        for page in range(1, 21):
            endpoint = PIPELINE_LIST.rsplit("=", 1)[0] + f"={page}"
            api.responses[("GET", endpoint)] = [[_pipeline()] * 100]
        expected_error = ACTION.ActionError
        reason = "pagination_limit_blocks_duplicate_check"
        expected_calls = 20
    else:
        api.responses[("GET", PIPELINE_LIST)] = [[_pipeline()]]
        api.responses[("GET", "projects/42/pipelines/73/variables")] = [
            API.GitLabAPIError("provider_403")
        ]
        expected_error = API.GitLabAPIError
        reason = "provider_403"
        expected_calls = 2
    with pytest.raises(expected_error, match=reason):
        START.start_pipeline(state)
    assert len(api.calls) == expected_calls
    assert all(method == "GET" for method, _endpoint, _body in api.calls)
    assert state.mutated is False


def test_start_holds_real_create_guard_through_lookup_post_and_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Provider callbacks observe one held guard and a pre-POST recovery marker."""
    api = StartAPI(
        {
            ("GET", PIPELINE_LIST): [[]],
            ("POST", "projects/42/pipeline"): [_pipeline()],
            ("GET", "projects/42/pipelines/73"): [_pipeline()],
        }
    )
    state = _state(api, {"ref": "main", "resolved_sha": SHA, "variables": []})
    api.responses[("GET", "projects/42/pipelines/73/variables")] = [[_marker(state)]]
    original_next = api._next

    def guarded_provider_call(
        method: str, endpoint: str, body: JSONValue = None
    ) -> JSONValue:
        """Fail if any provider call runs outside the actual exclusive guard."""
        lock_files = list(tmp_path.glob("*.lock"))
        assert len(lock_files) == 1
        with (
            lock_files[0].open("r+b") as descriptor,
            pytest.raises(BlockingIOError),
        ):
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if method == "POST":
            assert lock_files[0].read_bytes() == b"pending\n"
        return original_next(method, endpoint, body)

    monkeypatch.setattr(api, "_next", guarded_provider_call)
    assert START.start_pipeline(state)["verified"] is True
    lock_file = next(tmp_path.glob("*.lock"))
    assert lock_file.read_bytes() == b""
    with lock_file.open("r+b") as descriptor:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(descriptor, fcntl.LOCK_UN)


@pytest.mark.parametrize("mode", ("offline", "live-plan", "apply"))
def test_start_reports_satisfy_the_advertised_result_schema(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Validate real start reports at each boundary against the installed schema."""
    args = SCRIPT.build_parser().parse_args(["start", "--ref", "main", "--json"])
    monkeypatch.setattr(
        START,
        "resolve_gitlab_target",
        lambda *_args, **_kwargs: (_target(), "fixture"),
    )
    workflow = START.start_workflow(args)
    workflow.prepare()
    if mode == "offline":
        data = workflow.state.report
        assert data["status"] == "DRY_RUN"
    else:
        pipeline = {
            "id": 73,
            "project_id": 42,
            "ref": "main",
            "sha": SHA,
            "status": "pending",
        }
        state = _state(
            StartAPI(
                {
                    ("POST", "projects/42/pipeline"): [pipeline],
                    ("GET", "projects/42/pipelines/73"): [pipeline],
                }
            ),
            {
                "ref": "main",
                "resolved_sha": SHA,
                "variables": [],
                "protected_ref": False,
            },
        )
        state.access = {
            "identity": {
                "id": 9,
                "username": "unit-user",
                "web_url": "https://gitlab.example.test/unit-user",
            },
            "target": {
                "kind": "project",
                "id": 42,
                "full_path": "unit/repo",
                "web_url": "https://gitlab.example.test/unit/repo",
            },
        }
        if mode == "live-plan":
            workflow.state = state
            workflow.finish_plan()
            data = state.report
            assert data["status"] == "PLANNED"
            assert data["readback"]["sha"] == SHA
            assert state.gitlab.api.calls == []
        else:
            state.phase = "APPLY"
            state.gitlab.api.responses[("GET", PIPELINE_LIST)] = [[]]
            state.gitlab.api.responses[
                ("GET", "projects/42/pipelines/73/variables")
            ] = [[_marker(state)]]
            state.operation_result = START.start_pipeline(state)
            data = WORKFLOW._operation_report(state)
            assert data["status"] == "PASS"
            assert data["readback"]["id"] == 73
    SCHEMA.validate_specification(data, "results/gitlab-action.schema.json")
