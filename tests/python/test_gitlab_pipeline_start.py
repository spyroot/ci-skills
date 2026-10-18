"""Focused contracts for planning and starting one exact GitLab pipeline."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, MutableSequence, Sequence
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

if TYPE_CHECKING:
    from lib.python.core.gitlab.callback import GitLabState
    from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
    from lib.python.core.json_types import JSONObject, JSONValue

SHA = "a" * 40


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
        skill={"digest": "unit", "revision": {"value": SHA, "verified": True}},
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
    result = START.start_pipeline(state)
    assert result["action"] == "APPLIED"
    assert result["verified"] is True
    assert result["after"]["status"] == "running"
    assert api.calls == [
        (
            "POST",
            "projects/42/pipeline",
            {
                "ref": "main",
                "variables": [{"key": "DEPLOY", "value": "unit"}],
            },
        ),
        ("GET", "projects/42/pipelines/73", None),
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
    with pytest.raises(ACTION.ActionError, match="pipeline_readback_identity_mismatch"):
        START.start_pipeline(state)
    assert state.mutated is True


def test_start_preserves_unknown_mutation_when_post_fails() -> None:
    """Keep mutation unknown when creation fails at the provider boundary."""
    api = StartAPI(
        {("POST", "projects/42/pipeline"): [API.GitLabAPIError("provider_5xx")]}
    )
    state = _state(api, {"ref": "main", "resolved_sha": SHA, "variables": []})
    with pytest.raises(API.GitLabAPIError, match="provider_5xx"):
        START.start_pipeline(state)
    assert state.mutated is None
    assert len(api.calls) == 1
