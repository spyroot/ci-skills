"""Runner relation reads and reversible record deletion contracts."""

from __future__ import annotations

import json
from copy import deepcopy
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
TAG_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas/results/gitlab-runner-tag.schema.json").read_text())
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


def test_runner_read_cli_writes_portable_live_receipt(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    plan = _plan("get", {"description": None}, resource_id=23)
    session = SimpleNamespace(
        skill=VERIFIED_SKILL,
        credential_source="file:/selected/credential",
        credential_digest="sha256:unit",
    )
    monkeypatch.setattr(
        ACTION, "resolve_gitlab_target", lambda *a, **kw: (object(), "unit")
    )
    monkeypatch.setattr(ACTION, "make_plan", lambda *a, **kw: plan)
    monkeypatch.setattr(ACTION, "bind_gitlab_session", lambda *a, **kw: session)
    monkeypatch.setattr(ACTION, "GlabAPIClient", lambda **kw: object())
    monkeypatch.setattr(
        ACTION,
        "check_gitlab_operation_access",
        lambda *a, **kw: {
            "status": "PASS",
            "identity": VERIFIED_IDENTITY,
            "target": VERIFIED_TARGET,
        },
    )
    monkeypatch.setattr(
        RUNNERS.ReadRunner,
        "run",
        lambda _self: RUNNERS.RunnerReadResult(
            [RUNNERS._record(_runner())], False, None
        ),
    )
    receipt_path = tmp_path / "runner-get.json"
    assert (
        ACTION.run_action_cli(
            "gitlab_runner",
            ["get", "--runner-id", "23", "--receipt-out", str(receipt_path), "--json"],
        )
        == 0
    )
    stdout = json.loads(capsys.readouterr().out)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert stdout["target_file"] == "/selected/target.toml"
    assert receipt["target_file"].startswith("path:")
    assert receipt["credential_source"].startswith("file:path:")
    assert receipt["records"][0]["id"] == 23
    READ_VALIDATOR.validate(receipt)


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


def test_runner_delete_partial_requires_verified_context_and_readback() -> None:
    snapshot = {
        "id": 23,
        "present": True,
        "runner_type": "project_type",
        "description": "owned-runner",
        "project_ids": [42],
    }
    plan = replace(
        _plan("delete", {"description": None}, resource_id=23),
        runner_snapshot=snapshot,
    )
    record = {
        "action": "UNVERIFIED",
        "id": 23,
        "verified": False,
        "mutated": None,
        "before": RUNNERS._record(_runner()),
        "after": {
            "verified": False,
            "scoped_absent": False,
            "global_get_status": 200,
            "write_error": "request_timeout",
        },
        "errors": [{"project_id": 42, "reason": "runner_delete_readback_mismatch"}],
        "cleanup": {
            "status": "NOT_PERFORMED",
            "reason": "runner_delete_outcome_unverified",
        },
    }
    result = ACTION._result(
        plan,
        "PARTIAL",
        phase="APPLY",
        mutated=None,
        result_action="UNVERIFIED",
        records=[record],
        readback=record,
        errors=[{"source": "project:42", "reason": "runner_delete_readback_mismatch"}],
        cleanup=record["cleanup"],
        identity=VERIFIED_IDENTITY,
        verified_target=VERIFIED_TARGET,
        credential_source="unit",
        credential_digest="sha256:unit",
        skill=VERIFIED_SKILL,
    )
    DELETE_VALIDATOR.validate(result)
    del result["identity"]
    with pytest.raises(ValidationError):
        DELETE_VALIDATOR.validate(result)


def test_runner_result_schemas_close_every_status_shape() -> None:
    live = {
        "identity": VERIFIED_IDENTITY,
        "verified_target": VERIFIED_TARGET,
        "credential_source": "unit",
        "credential_digest": "sha256:unit",
        "skill": VERIFIED_SKILL,
    }
    failure = [{"source": "gitlab_action", "reason": "provider_unavailable"}]
    no_write = {"status": "NOT_APPLICABLE", "reason": "no_write_attempted"}
    read_plan = _plan("get", {"description": None}, resource_id=23)
    tag_plan = _plan("tag", {"tag_list": ["smoke"]}, resource_id=23)
    snapshot = {
        "id": 23,
        "present": True,
        "runner_type": "project_type",
        "description": "owned-runner",
        "project_ids": [42],
    }
    delete_plan = replace(
        _plan("delete", {"description": None}, resource_id=23),
        runner_snapshot=snapshot,
    )
    deleted = {
        "action": "APPLIED",
        "id": 23,
        "verified": True,
        "mutated": True,
        "before": RUNNERS._record(_runner()),
        "after": {
            "verified": True,
            "scoped_absent": True,
            "global_get_status": 404,
            "write_error": None,
        },
    }
    delete_partial = {
        **deleted,
        "action": "UNVERIFIED",
        "verified": False,
        "mutated": None,
        "after": {
            "verified": False,
            "scoped_absent": False,
            "global_get_status": 200,
            "write_error": "request_timeout",
        },
        "errors": [{"project_id": 42, "reason": "runner_delete_readback_mismatch"}],
        "cleanup": {
            "status": "NOT_PERFORMED",
            "reason": "runner_delete_outcome_unverified",
        },
    }
    tag_partial = {
        "action": "UNVERIFIED",
        "id": 23,
        "verified": False,
        "mutated": None,
        "uncertain": True,
        "before_tags": ["existing"],
        "expected_tags": ["existing", "smoke"],
        "errors": [{"project_id": 42, "reason": "runner_tag_readback_mismatch"}],
        "cleanup": {
            "status": "NOT_PERFORMED",
            "reason": "post_write_readback_mismatch",
        },
    }
    tag_pass = json.loads(
        (ROOT / "tests/acceptance/runner-tag/runner-tag-applied.json").read_text()
    )
    cases = [
        (
            "read_dry_run",
            READ_VALIDATOR,
            ACTION._result(read_plan, "DRY_RUN", phase="OFFLINE_PLAN", mutated=False),
        ),
        (
            "read_pass",
            READ_VALIDATOR,
            ACTION._result(
                read_plan,
                "PASS",
                phase="READ",
                mutated=False,
                records=[RUNNERS._record(_runner())],
                related_job=None,
                readback={"verified": True, "record_count": 1, "truncated": False},
                **live,
            ),
        ),
        (
            "read_blocked",
            READ_VALIDATOR,
            ACTION._result(
                read_plan,
                "BLOCKED",
                phase="ACCESS",
                mutated=False,
                readback={"verified": False},
                cleanup=no_write,
                errors=failure,
            ),
        ),
        (
            "delete_dry_run",
            DELETE_VALIDATOR,
            ACTION._result(
                _plan("delete", {"description": None}, resource_id=23),
                "DRY_RUN",
                phase="OFFLINE_PLAN",
                mutated=False,
            ),
        ),
        (
            "delete_planned",
            DELETE_VALIDATOR,
            ACTION._result(
                delete_plan,
                "PLANNED",
                phase="LIVE_PLAN",
                mutated=False,
                readback={"verified": True, "runner_snapshot": snapshot},
                **live,
            ),
        ),
        (
            "delete_pass",
            DELETE_VALIDATOR,
            ACTION._result(
                delete_plan,
                "PASS",
                phase="APPLY",
                mutated=True,
                result_action="APPLIED",
                records=[deleted],
                readback=deleted,
                cleanup={"status": "NOT_APPLICABLE"},
                **live,
            ),
        ),
        (
            "delete_partial",
            DELETE_VALIDATOR,
            ACTION._result(
                delete_plan,
                "PARTIAL",
                phase="APPLY",
                mutated=None,
                result_action="UNVERIFIED",
                records=[delete_partial],
                readback=delete_partial,
                errors=[
                    {
                        "source": "project:42",
                        "reason": "runner_delete_readback_mismatch",
                    }
                ],
                cleanup=delete_partial["cleanup"],
                **live,
            ),
        ),
        (
            "delete_blocked",
            DELETE_VALIDATOR,
            ACTION._result(
                delete_plan,
                "BLOCKED",
                phase="ACCESS",
                mutated=False,
                readback={"verified": False},
                cleanup=no_write,
                errors=failure,
            ),
        ),
        (
            "tag_dry_run",
            TAG_VALIDATOR,
            ACTION._result(tag_plan, "DRY_RUN", phase="OFFLINE_PLAN", mutated=False),
        ),
        ("tag_pass", TAG_VALIDATOR, tag_pass),
        (
            "tag_partial",
            TAG_VALIDATOR,
            ACTION._result(
                tag_plan,
                "PARTIAL",
                phase="APPLY",
                mutated=None,
                result_action="UNVERIFIED",
                records=[tag_partial],
                readback=tag_partial,
                errors=[
                    {"source": "project:42", "reason": "runner_tag_readback_mismatch"}
                ],
                cleanup=tag_partial["cleanup"],
                **live,
            ),
        ),
        (
            "tag_blocked",
            TAG_VALIDATOR,
            ACTION._result(
                tag_plan,
                "BLOCKED",
                phase="ACCESS",
                mutated=False,
                readback={"verified": False},
                cleanup=no_write,
                errors=failure,
            ),
        ),
    ]
    for name, validator, result in cases:
        validator.validate(result)
        unknown = deepcopy(result)
        if "skill" in unknown:
            unknown["skill"]["unknown_field"] = True
        else:
            unknown["plan"]["unknown_field"] = True
        with pytest.raises(ValidationError, match="Additional properties"):
            validator.validate(unknown)
        missing = deepcopy(result)
        required = {
            "DRY_RUN": "plan",
            "PLANNED": "readback",
            "PASS": "readback",
            "PARTIAL": "cleanup",
            "BLOCKED": "errors",
        }[result["status"]]
        del missing[required]
        with pytest.raises(ValidationError):
            validator.validate(missing)
        wrong_type = deepcopy(result)
        wrong_type["summary"]["record_count"] = "unknown"
        with pytest.raises(ValidationError):
            validator.validate(wrong_type)
        if "readback" in result:
            unknown_readback = deepcopy(result)
            unknown_readback["readback"]["unknown_field"] = True
            with pytest.raises(ValidationError):
                validator.validate(unknown_readback)
        if "cleanup" in result:
            unknown_cleanup = deepcopy(result)
            unknown_cleanup["cleanup"]["unknown_field"] = True
            with pytest.raises(ValidationError):
                validator.validate(unknown_cleanup)
        if result["records"] and "errors" in result["records"][0]:
            unknown_record_error = deepcopy(result)
            unknown_record_error["records"][0]["errors"][0]["unknown_field"] = True
            with pytest.raises(ValidationError):
                validator.validate(unknown_record_error)
        with_report_files = deepcopy(result)
        with_report_files["report_files"] = {
            "json": "/receipt/result.json",
            "text": "/receipt/result.txt",
        }
        validator.validate(with_report_files)
        with_report_files["report_files"]["unknown_field"] = True
        with pytest.raises(ValidationError):
            validator.validate(with_report_files)
        if name == "tag_partial":
            missing_uncertainty = deepcopy(result)
            del missing_uncertainty["readback"]["uncertain"]
            with pytest.raises(ValidationError):
                validator.validate(missing_uncertainty)
