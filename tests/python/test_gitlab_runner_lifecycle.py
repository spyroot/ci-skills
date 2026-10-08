"""Runner relation reads and reversible record deletion contracts."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from tests.python.conftest import import_script_module

ACTION = import_script_module("core.gitlab_actions")
API = import_script_module("core.gitlab_api")
RUNNERS = import_script_module("core.gitlab_runners")
REPORT = import_script_module("core.report")
ROOT = Path(__file__).parents[2]
VERIFIED_SKILL = {
    "algorithm": "sha256-tree-v1",
    "digest": "b" * 64,
    "file_count": 1,
    "revision": {"value": "a" * 40, "source": "git", "verified": True},
    "consuming_project": {"commit": None, "source": None},
}
VERIFIED_IDENTITY = {
    "id": 1,
    "username": "unit",
    "web_url": "https://gitlab.example.test/unit",
}
VERIFIED_TARGET = {
    "kind": "project",
    "id": 42,
    "full_path": "team/repo",
    "web_url": "https://gitlab.example.test/team/repo",
}
READ_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas/results/gitlab-runner-read.schema.json").read_text())
)
DELETE_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas/results/gitlab-runner-delete.schema.json").read_text())
)


def _plan(
    operation: str,
    body: dict[str, Any],
    resource_id: int | None = None,
    runner_snapshot: dict[str, Any] | None = None,
) -> ACTION.ActionPlan:
    return ACTION.ActionPlan(
        kind="gitlab_runner",
        operation=operation,
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/repo",
        target_file="/selected/target.toml",
        target_source="argv:--target;target:gitlab.project",
        body=body,
        resource_id=resource_id,
        runner_snapshot=runner_snapshot,
        revision="a" * 40,
    )


class FakeAPI:
    def __init__(self, responses):
        self.responses = {key: list(value) for key, value in responses.items()}
        self.calls = []

    def _next(self, method, endpoint):
        self.calls.append((method, endpoint))
        value = self.responses[(method, endpoint)].pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def get_json(self, session, endpoint):
        return self._next("GET", endpoint)

    def delete_json(self, session, endpoint):
        return self._next("DELETE", endpoint)


def _list():
    return "projects/42/runners?per_page=100&page=1"


def _runner(identifier=23, **changes):
    return {
        "id": identifier,
        "description": "owned-runner",
        "runner_type": "project_type",
        "tag_list": ["smoke"],
        "status": "online",
        "online": True,
        "paused": False,
        "is_shared": False,
        "access_level": "ref_protected",
        "job_execution_status": "idle",
        "projects": [{"id": 42}],
        "contacted_at": "2026-10-08T00:00:00Z",
        "version": "18.0",
        "platform": "linux",
        "architecture": "amd64",
        **changes,
    }


def test_get_from_failed_job_resolves_runner_relation_without_raw_payload():
    plan = _plan("get", {"job_id": 9})
    api = FakeAPI(
        {
            ("GET", "projects/42/jobs/9"): [
                {
                    "id": 9,
                    "status": "failed",
                    "failure_reason": "runner_system_failure",
                    "pipeline": {"id": 7},
                    "runner": {"id": 23},
                }
            ],
            ("GET", "runners/23"): [_runner()],
            ("GET", _list()): [[{"id": 23}]],
        }
    )
    result = RUNNERS.read(api, object(), plan, 42)
    assert result.related_job == {
        "id": 9,
        "status": "failed",
        "failure_reason": "runner_system_failure",
        "pipeline_id": 7,
        "runner_id": 23,
    }
    assert result.records[0]["access_level"] == "ref_protected"
    assert result.records[0]["tag_list"] == ["smoke"]
    assert result.records[0]["projects"] == [42]
    assert "ip_address" not in result.records[0]

    rendered = ACTION._result(
        plan,
        "PASS",
        phase="READ",
        mutated=False,
        records=result.records,
        related_job=result.related_job,
        readback={"verified": True, "record_count": 1, "truncated": False},
        identity=VERIFIED_IDENTITY,
        verified_target=VERIFIED_TARGET,
        credential_source="unit",
        credential_digest="sha256:unit",
        skill=VERIFIED_SKILL,
    )
    READ_VALIDATOR.validate(rendered)
    human = REPORT.human(rendered)
    assert "Related job: id=9" in human
    assert "access=ref_protected" in human


def test_get_from_unassigned_job_reports_relation_without_guessing_runner():
    plan = _plan("get", {"job_id": 9})
    api = FakeAPI(
        {
            ("GET", "projects/42/jobs/9"): [
                {"id": 9, "status": "pending", "runner": None}
            ]
        }
    )
    result = RUNNERS.read(api, object(), plan, 42)
    assert result.records == []
    assert result.related_job["runner_id"] is None
    assert api.calls == [("GET", "projects/42/jobs/9")]


def test_list_filters_protected_runner_and_limits_public_fields():
    plan = _plan(
        "list", {"description": None, "limit": 10, "filters": ["protected", "online"]}
    )
    api = FakeAPI(
        {
            ("GET", _list()): [
                [
                    {"id": 23, "description": "owned-runner"},
                    {"id": 24, "description": "other"},
                ]
            ],
            ("GET", "runners/23"): [_runner()],
            ("GET", "runners/24"): [_runner(24, access_level="not_protected")],
        }
    )
    result = RUNNERS.read(api, object(), plan, 42)
    assert [item["id"] for item in result.records] == [23]
    assert result.truncated is False


def test_delete_requires_scope_and_global_404_readback(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    snapshot = {
        "id": 23,
        "present": True,
        "runner_type": "project_type",
        "description": "owned-runner",
        "project_ids": [42],
    }
    plan = _plan("delete", {"description": "owned-runner"}, resource_id=23)
    api = FakeAPI(
        {
            ("GET", "runners/23"): [
                _runner(),
                _runner(),
                API.GitLabAPIError("provider_404"),
            ],
            ("GET", _list()): [[{"id": 23}], [{"id": 23}], []],
            ("DELETE", "runners/23"): [{}],
        }
    )
    planned = RUNNERS.delete_snapshot(api, object(), plan, 42)
    assert planned == snapshot
    plan = replace(plan, runner_snapshot=planned)
    record = RUNNERS.apply(api, object(), plan, 42)
    assert record["action"] == "APPLIED"
    assert record["after"]["global_get_status"] == 404
    assert record["after"]["scoped_absent"] is True
    assert api.calls.index(("DELETE", "runners/23")) > api.calls.index(("GET", _list()))
    rendered = ACTION._result(
        plan,
        "PASS",
        phase="APPLY",
        mutated=True,
        result_action="APPLIED",
        records=[record],
        readback=record,
        identity=VERIFIED_IDENTITY,
        verified_target=VERIFIED_TARGET,
        credential_source="unit",
        credential_digest="sha256:unit",
        skill=VERIFIED_SKILL,
    )
    DELETE_VALIDATOR.validate(rendered)
    rendered["skill"] = {"revision": {"value": "a" * 40, "verified": False}}
    with pytest.raises(ValidationError):
        DELETE_VALIDATOR.validate(rendered)


def test_repeat_delete_is_verified_no_op_without_write(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    plan = _plan(
        "delete",
        {"description": None},
        resource_id=23,
        runner_snapshot={"id": 23, "present": False},
    )
    api = FakeAPI(
        {
            ("GET", "runners/23"): [API.GitLabAPIError("provider_404")],
            ("GET", _list()): [[]],
        }
    )
    record = RUNNERS.apply(api, object(), plan, 42)
    assert record["action"] == "NO_OP"
    assert record["mutated"] is False
    assert all(method == "GET" for method, _ in api.calls)


def test_delete_refuses_runner_outside_selected_project(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    plan = _plan(
        "delete",
        {"description": None},
        resource_id=23,
        runner_snapshot={
            "id": 23,
            "present": True,
            "runner_type": "project_type",
            "description": "owned-runner",
            "project_ids": [42],
        },
    )
    api = FakeAPI(
        {
            ("GET", "runners/23"): [_runner()],
            ("GET", _list()): [[]],
        }
    )
    with pytest.raises(ACTION.ActionError, match="runner_scope_readback_mismatch"):
        RUNNERS.apply(api, object(), plan, 42)
    assert all(method == "GET" for method, _ in api.calls)


@pytest.mark.parametrize(
    ("observed", "reason"),
    [
        (_runner(description="replacement"), "runner_changed_rerun_live_plan"),
        (_runner(projects=[{"id": 42}, {"id": 99}]), "runner_changed_rerun_live_plan"),
    ],
)
def test_delete_refuses_identity_or_assignment_drift_before_write(
    monkeypatch, tmp_path, observed: dict[str, Any], reason: str
) -> None:
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    plan = _plan(
        "delete",
        {"description": None},
        resource_id=23,
        runner_snapshot={
            "id": 23,
            "present": True,
            "runner_type": "project_type",
            "description": "owned-runner",
            "project_ids": [42],
        },
    )
    api = FakeAPI(
        {
            ("GET", "runners/23"): [observed],
            ("GET", _list()): [[{"id": 23}]],
        }
    )
    with pytest.raises(ACTION.ActionError, match=reason):
        RUNNERS.apply(api, object(), plan, 42)
    assert all(method == "GET" for method, _ in api.calls)


def test_delete_plan_requires_numeric_id_and_live_snapshot() -> None:
    args = ACTION.action_parser("gitlab_runner").parse_args(
        ["delete", "--description", "owned-runner", "--dry-run"]
    )
    target = SimpleNamespace(gitlab=SimpleNamespace(runner_id=None))
    with pytest.raises(ACTION.ActionError, match="runner_delete_requires_runner_id"):
        RUNNERS.prepare(args, target, "project")
    offline = _plan("delete", {"description": None}, resource_id=23)
    assert offline.public()["confirmation_ready"] is False
    assert offline.public()["runner_snapshot"] is None


@pytest.mark.parametrize("action", ["get", "list", "delete"])
def test_runner_live_operation_refuses_unverified_skill_before_api(
    monkeypatch, capsys, action: str
) -> None:
    arguments = [action, "--json"]
    if action != "list":
        arguments.extend(["--runner-id", "23"])
    if action == "delete":
        arguments.append("--live-plan")
    plan = _plan(
        action,
        {"description": None},
        resource_id=23 if action != "list" else None,
    )
    session = SimpleNamespace(
        skill={"revision": {"value": "a" * 40, "verified": False}},
        credential_source="unit",
        credential_digest="sha256:unit",
    )
    monkeypatch.setattr(
        ACTION, "resolve_gitlab_target", lambda *a, **kw: (object(), "unit")
    )
    monkeypatch.setattr(ACTION, "make_plan", lambda *a, **kw: plan)
    monkeypatch.setattr(ACTION, "bind_gitlab_session", lambda *a, **kw: session)
    monkeypatch.setattr(
        ACTION, "GlabAPIClient", lambda **kw: pytest.fail("API client must not run")
    )
    assert ACTION.run_action_cli("gitlab_runner", arguments) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "BLOCKED"
    assert output["errors"][0]["reason"] == "skill_revision_unverified"


def test_runner_live_result_schemas_reject_unverified_skill_and_empty_identity() -> (
    None
):
    plan = _plan("get", {"description": None}, resource_id=23)
    result = ACTION._result(
        plan,
        "PASS",
        phase="READ",
        mutated=False,
        records=[RUNNERS._record(_runner())],
        related_job=None,
        readback={"verified": True, "record_count": 1, "truncated": False},
        identity=VERIFIED_IDENTITY,
        verified_target=VERIFIED_TARGET,
        credential_source="unit",
        credential_digest="sha256:unit",
        skill=VERIFIED_SKILL,
    )
    READ_VALIDATOR.validate(result)
    result["skill"] = {"revision": {"value": "a" * 40, "verified": False}}
    with pytest.raises(ValidationError):
        READ_VALIDATOR.validate(result)
    result["skill"] = VERIFIED_SKILL
    result["identity"] = {}
    with pytest.raises(ValidationError):
        READ_VALIDATOR.validate(result)


def test_runner_delete_live_plan_binds_assignments_in_digest() -> None:
    offline = _plan("delete", {"description": None}, resource_id=23)
    snapshot = {
        "id": 23,
        "present": True,
        "runner_type": "project_type",
        "description": "owned-runner",
        "project_ids": [42],
    }
    planned = replace(offline, runner_snapshot=snapshot)
    changed = replace(planned, runner_snapshot={**snapshot, "project_ids": [42, 99]})
    assert offline.digest != planned.digest != changed.digest
    assert planned.public()["confirmation_ready"] is True
    result = ACTION._result(
        planned,
        "PLANNED",
        phase="LIVE_PLAN",
        mutated=False,
        readback={"verified": True, "runner_snapshot": snapshot},
        identity=VERIFIED_IDENTITY,
        verified_target=VERIFIED_TARGET,
        credential_source="unit",
        credential_digest="sha256:unit",
        skill=VERIFIED_SKILL,
    )
    DELETE_VALIDATOR.validate(result)
