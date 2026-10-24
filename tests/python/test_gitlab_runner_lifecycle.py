"""Runner relation reads and reversible record deletion contracts.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Optional, Union

import pytest
from conftest import authenticated_gitlab_session, import_script_module
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

ACTION = import_script_module("lib.python.core.gitlab.gitlab_actions")
GITLAB_ACTION = import_script_module("lib.python.core.gitlab.gitlab_action")
GITLAB_SESSION = import_script_module("lib.python.core.gitlab.gitlab_session")
API = import_script_module("lib.python.core.gitlab.gitlab_api")
RUNNERS = import_script_module("lib.python.core.gitlab.gitlab_runners")
REPORT = import_script_module("lib.python.core.report")
RUNNER = import_script_module("gitlab_runner")
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
    json.loads(
        (ROOT / "ci-skills/schemas/results/gitlab-runner-read.schema.json").read_text()
    )
)
DELETE_VALIDATOR = Draft202012Validator(
    json.loads(
        (
            ROOT / "ci-skills/schemas/results/gitlab-runner-delete.schema.json"
        ).read_text()
    )
)
TAG_VALIDATOR = Draft202012Validator(
    json.loads(
        (ROOT / "ci-skills/schemas/results/gitlab-runner-tag.schema.json").read_text()
    )
)


if TYPE_CHECKING:
    from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
    from lib.python.core.json_types import JSONObject, JSONValue


def _plan(
    operation: str,
    body: JSONObject,
    resource_id: Optional[int] = None,
    runner_snapshot: Optional[JSONObject] = None,
) -> ACTION.ActionPlan:
    """Build a project-scoped runner action plan for one operation.

    :param operation: Runner get, list, delete, or tag verb.
    :param body: Operation-specific runner filters or desired fields.
    :param resource_id: Exact runner ID for non-list operations.
    :param runner_snapshot: Verified live identity/assignment snapshot for delete.
    :returns: Action plan bound to the fixture project and revision.
    """
    return ACTION.ActionPlan(
        kind="gitlab_runner",
        operation=operation,
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/repo",
        target_file="/selected/target.toml",
        target_source="env:CI_SKILLS_TARGET;target:gitlab.project",
        body=body,
        resource_id=resource_id,
        runner_snapshot=runner_snapshot,
        revision="a" * 40,
    )


def _bound_session(
    plan: ACTION.ActionPlan,
    *,
    skill: JSONObject = VERIFIED_SKILL,
    credential_source: str = "env:GITLAB_TOKEN",
    credential_digest: str = "sha256:unit",
) -> BoundGitLabSession:
    """Create the concrete GitLab session required by workflow receipts.

    :param plan: Runner action plan whose target fields identify the session.
    :param skill: Installed-skill identity supplied to the workflow.
    :param credential_source: Nonsecret credential origin recorded in receipts.
    :param credential_digest: Credential continuity digest recorded in receipts.
    :returns: Bound GitLab session matching the production workflow shape.
    """
    return authenticated_gitlab_session(
        GITLAB_SESSION.BoundGitLabSession(
            origin=plan.origin,
            host="gitlab.example.test",
            target_kind=plan.target_kind,
            target_reference=plan.target_reference,
            target_source=plan.target_source,
            target_file=Path(plan.target_file),
            credential_source=credential_source,
            credential_digest=credential_digest,
            execution_host="unit-host",
            skill=skill,
            environment={},
        )
    )


class FakeAPI:
    """Replay runner read and delete responses for lifecycle proof tests."""

    def __init__(
        self,
        responses: Mapping[tuple[str, str], Sequence[Union[JSONValue, BaseException]]],
    ):
        """Copy scripted endpoint responses into mutable request queues.

        :param responses: Method/endpoint mapping to ordered results or failures.
        """
        self.responses = {key: list(value) for key, value in responses.items()}
        self.calls = []

    def _next(self, method: str, endpoint: str) -> JSONValue:
        """Record a request and consume its next scripted response.

        :param method: GET or DELETE verb selecting a response queue.
        :param endpoint: Relative runner API path selecting that queue.
        :returns: Next provider result for this request.
        :raises BaseException: Scripted provider failure supplied by the test.
        """  # noqa: DOC503 - The dynamic fixture exception is raised verbatim.
        self.calls.append((method, endpoint))
        value = self.responses[(method, endpoint)].pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def get_json(self, session: BoundGitLabSession, endpoint: str) -> JSONValue:
        """Consume a scripted runner GET response.

        :param session: Bound session accepted by the API double.
        :param endpoint: Relative runner read path.
        :returns: Next scripted GET result.
        """
        return self._next("GET", endpoint)

    def delete_json(self, session: BoundGitLabSession, endpoint: str) -> JSONValue:
        """Consume a scripted runner DELETE response.

        :param session: Bound session accepted by the API double.
        :param endpoint: Relative runner deletion path.
        :returns: Next scripted DELETE result.
        """
        return self._next("DELETE", endpoint)


def _list() -> str:
    """Name the selected project's first runner-list page.

    :returns: Relative GitLab endpoint for project 42 runners.
    """
    return "projects/42/runners?per_page=100&page=1"


def _runner(
    identifier: int = 23,
    **changes: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
) -> JSONObject:
    """Build an owned protected runner detail with selected changes.

    :param identifier: Runner ID used in read and deletion cases.
    :param **changes: Field changes applied to the default provider record.
    :returns: Provider runner mapping assigned to fixture project 42.

    """
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


def test_get_from_failed_job_resolves_runner_relation_without_raw_payload() -> None:
    """Resolve a failed job's runner and emit only schema-allowed fields.
    :returns: None; assertions in the body validate the regression.
    """
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
    result = RUNNERS.read(api, _bound_session(plan), plan, 42)
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


def test_get_from_unassigned_job_reports_relation_without_guessing_runner() -> None:
    """Keep an unassigned job relation without inventing a runner record.
    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("get", {"job_id": 9})
    api = FakeAPI(
        {
            ("GET", "projects/42/jobs/9"): [
                {"id": 9, "status": "pending", "runner": None}
            ]
        }
    )
    result = RUNNERS.read(api, _bound_session(plan), plan, 42)
    assert result.records == []
    assert result.related_job["runner_id"] is None
    assert api.calls == [("GET", "projects/42/jobs/9")]


def test_list_filters_protected_runner_and_limits_public_fields() -> None:
    """Filter the selected project list by online and protected runner state.
    :returns: None; assertions in the body validate the regression.
    """
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
    result = RUNNERS.read(api, _bound_session(plan), plan, 42)
    assert [item["id"] for item in result.records] == [23]
    assert result.truncated is False


def test_delete_requires_scope_and_global_404_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Delete only after scoped snapshot and verify global 404 plus scoped absence.

    :param monkeypatch: Pytest fixture selecting scratch create-lock storage.
    :param tmp_path: Private directory for the runner mutation lock.

    :returns: None; assertions in the body validate the regression.
    """
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
    planned = RUNNERS.delete_snapshot(api, _bound_session(plan), plan, 42)
    assert planned == snapshot
    plan = replace(plan, runner_snapshot=planned)
    record = RUNNERS.apply(api, _bound_session(plan), plan, 42)
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


def test_repeat_delete_is_verified_no_op_without_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Classify a globally and locally absent runner as read-only NO_OP.

    :param monkeypatch: Pytest fixture selecting scratch lock storage.
    :param tmp_path: Private directory for the runner mutation lock.

    :returns: None; assertions in the body validate the regression.
    """
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
    record = RUNNERS.apply(api, _bound_session(plan), plan, 42)
    assert record["action"] == "NO_OP"
    assert record["mutated"] is False
    assert all(method == "GET" for method, _ in api.calls)


def test_delete_refuses_runner_outside_selected_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject deletion when exact runner detail is outside the selected project.

    :param monkeypatch: Pytest fixture selecting scratch lock storage.
    :param tmp_path: Private directory for the runner mutation lock.

    :returns: None; assertions in the body validate the regression.
    """
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
        RUNNERS.apply(api, _bound_session(plan), plan, 42)
    assert all(method == "GET" for method, _ in api.calls)


@pytest.mark.parametrize(
    ("observed", "reason"),
    [
        (_runner(description="replacement"), "runner_changed_rerun_live_plan"),
        (_runner(projects=[{"id": 42}, {"id": 99}]), "runner_changed_rerun_live_plan"),
    ],
)
def test_delete_refuses_identity_or_assignment_drift_before_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, observed: JSONObject, reason: str
) -> None:
    """Reject runner deletion if detail or project assignments changed.

    :param monkeypatch: Pytest fixture selecting scratch lock storage.
    :param tmp_path: Private directory for the runner mutation lock.
    :param observed: Runner detail with changed description or assignments.
    :param reason: Expected fail-closed drift reason.

    :returns: None; assertions in the body validate the regression.
    """
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
        RUNNERS.apply(api, _bound_session(plan), plan, 42)
    assert all(method == "GET" for method, _ in api.calls)


def test_delete_plan_requires_numeric_id_and_live_snapshot() -> None:
    """Keep offline deletion unconfirmable until numeric ID and live snapshot.
    :returns: None; assertions in the body validate the regression.
    """
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
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], action: str
) -> None:
    """Block get, list, and delete before API use for unverified skill bytes.

    :param monkeypatch: Pytest fixture replacing target, bind, and API factories.
    :param capsys: Pytest fixture capturing the blocked machine report.
    :param action: Runner operation selected for the unverified-skill check.

    :returns: None; assertions in the body validate the regression.
    """
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
    session = _bound_session(
        plan,
        skill={"revision": {"value": "a" * 40, "verified": False}},
        credential_source="unit",
    )
    monkeypatch.setattr(
        GITLAB_ACTION, "resolve_gitlab_target", lambda *a, **kw: (object(), "unit")
    )
    monkeypatch.setattr(GITLAB_ACTION, "make_plan", lambda *a, **kw: plan)
    monkeypatch.setattr(
        GITLAB_ACTION,
        "bind_gitlab_session",
        lambda *a, **kw: authenticated_gitlab_session(session, kw.get("invocation")),
    )
    monkeypatch.setattr(
        GITLAB_ACTION,
        "GlabAPIClient",
        lambda **kw: pytest.fail("API client must not run"),
    )
    assert RUNNER.main(arguments) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "BLOCKED"
    assert output["errors"][0]["reason"] == "skill_revision_unverified"


def test_runner_read_cli_writes_portable_live_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Redact local target and credential paths in a runner read receipt.

    :param monkeypatch: Pytest fixture replacing selected provider operations.
    :param tmp_path: Scratch destination for the written receipt.
    :param capsys: Pytest fixture capturing the live command output.

    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("get", {"description": None}, resource_id=23)
    session = _bound_session(
        plan, skill=VERIFIED_SKILL, credential_source="file:/selected/credential"
    )
    monkeypatch.setattr(
        GITLAB_ACTION, "resolve_gitlab_target", lambda *a, **kw: (object(), "unit")
    )
    monkeypatch.setattr(GITLAB_ACTION, "make_plan", lambda *a, **kw: plan)
    monkeypatch.setattr(
        GITLAB_ACTION,
        "bind_gitlab_session",
        lambda *a, **kw: authenticated_gitlab_session(session, kw.get("invocation")),
    )
    monkeypatch.setattr(GITLAB_ACTION, "GlabAPIClient", lambda **kw: object())
    monkeypatch.setattr(
        GITLAB_ACTION,
        "check_gitlab_operation_access",
        lambda *a, **kw: {
            "status": "PASS",
            "identity": VERIFIED_IDENTITY,
            "target": VERIFIED_TARGET,
        },
    )

    def read_runner(_self: JSONValue) -> JSONObject:
        """Return the public mapping while retaining the typed read observation.

        :param _self: Patched runner callback supplied by the workflow.
        :returns: Public runner read mapping used by the shared executor.
        """
        observation = RUNNERS.RunnerReadResult(
            [RUNNERS._record(_runner())], False, None
        )
        _self.state.runner_read = observation
        return {
            "records": observation.records,
            "truncated": observation.truncated,
            "related_job": observation.related_job,
        }

    monkeypatch.setattr(RUNNERS.ReadRunner, "run", read_runner)
    receipt_path = tmp_path / "runner-get.json"
    assert (
        RUNNER.main(
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
    """Reject live runner records without verified skill or identity evidence.
    :returns: None; assertions in the body validate the regression.
    """
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
    """Change deletion digest when live project assignments change.
    :returns: None; assertions in the body validate the regression.
    """
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
    """Require verified context around uncertain runner deletion evidence.
    :returns: None; assertions in the body validate the regression.
    """
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
    """Validate all runner status/verb shapes and reject extra or missing fields.
    :returns: None; assertions in the body validate the regression.
    """
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
