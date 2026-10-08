"""Runner relation reads and reversible record deletion contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tests.python.conftest import import_script_module

ACTION = import_script_module("core.gitlab_actions")
API = import_script_module("core.gitlab_api")
RUNNERS = import_script_module("core.gitlab_runners")
REPORT = import_script_module("core.report")
ROOT = Path(__file__).parents[2]
READ_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas/results/gitlab-runner-read.schema.json").read_text())
)
DELETE_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas/results/gitlab-runner-delete.schema.json").read_text())
)


def _plan(operation, body, resource_id=None):
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
        identity={},
        verified_target={},
        credential_source="unit",
        credential_digest="sha256:unit",
        skill={},
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


def test_delete_requires_scope_and_global_404_readback(monkeypatch, tmp_path):
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    plan = _plan("delete", {"description": "owned-runner"}, resource_id=23)
    api = FakeAPI(
        {
            ("GET", "runners/23"): [_runner(), API.GitLabAPIError("provider_404")],
            ("GET", _list()): [[{"id": 23}], []],
            ("DELETE", "runners/23"): [{}],
        }
    )
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
        identity={},
        verified_target={},
        credential_source="unit",
        credential_digest="sha256:unit",
        skill={},
    )
    DELETE_VALIDATOR.validate(rendered)


def test_repeat_delete_is_verified_no_op_without_write(monkeypatch, tmp_path):
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    plan = _plan("delete", {"description": None}, resource_id=23)
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


def test_delete_refuses_runner_outside_selected_project(monkeypatch, tmp_path):
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    plan = _plan("delete", {"description": None}, resource_id=23)
    api = FakeAPI(
        {
            ("GET", "runners/23"): [_runner()],
            ("GET", _list()): [[]],
        }
    )
    with pytest.raises(ACTION.ActionError, match="runner_scope_readback_mismatch"):
        RUNNERS.apply(api, object(), plan, 42)
    assert all(method == "GET" for method, _ in api.calls)
