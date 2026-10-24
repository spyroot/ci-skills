"""Offline operation contracts; no GitLab writes or cluster access.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import stat
from collections.abc import Mapping, MutableSequence, Sequence
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from typing import TYPE_CHECKING, Final, Optional, Union

import pytest
from conftest import authenticated_gitlab_session, import_script_module, load_module
from jsonschema import Draft202012Validator

ACTION = import_script_module("lib.python.core.gitlab.gitlab_actions")
GITLAB_ACTION = import_script_module("lib.python.core.gitlab.gitlab_action")
GITLAB_SESSION = import_script_module("lib.python.core.gitlab.gitlab_session")
CALLBACKS = import_script_module("lib.python.core.abstract.callback")
PORTABLE = import_script_module("lib.python.core.portable")
API = import_script_module("lib.python.core.gitlab.gitlab_api")
MILESTONES = import_script_module("lib.python.core.gitlab.gitlab_milestones")
ISSUES = import_script_module("lib.python.core.gitlab.gitlab_issues")
WIKIS = import_script_module("lib.python.core.gitlab.gitlab_wikis")
RUNNERS = import_script_module("lib.python.core.gitlab.gitlab_runners")
REPORT = import_script_module("lib.python.core.report")
ACCEPTANCE = load_module(
    "runner_tag_receipt_acceptance",
    Path(__file__).parents[2] / "tools/check_live_acceptance.py",
)
ISSUE = import_script_module("gitlab_issue")
MILESTONE = import_script_module("gitlab_milestone")
RUNNER = import_script_module("gitlab_runner")
WIKI = import_script_module("gitlab_wiki")

ENTRYPOINTS = {
    "gitlab_issue": ISSUE,
    "gitlab_milestone": MILESTONE,
    "gitlab_wiki": WIKI,
}

TAG_SCHEMA = json.loads(
    (
        Path(__file__).parents[2]
        / "ci-skills/schemas/results/gitlab-runner-tag.schema.json"
    ).read_text(encoding="utf-8")
)
TAG_VALIDATOR = Draft202012Validator(TAG_SCHEMA)
FACTORY_FAILURE: Final = "factory failed"


if TYPE_CHECKING:
    from lib.python.core.gitlab.gitlab_actions import ActionPlan
    from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
    from lib.python.core.json_types import JSONObject, JSONValue


def test_committed_runner_tag_receipts_match_live_contract() -> None:
    """Validate retained runner-tag receipts against their recorded source and schema.
    :returns: None; assertions in the body validate the regression.
    """
    root = Path(__file__).parents[2]
    receipts = root / "tests/acceptance/runner-tag"
    applied = json.loads((receipts / "runner-tag-applied.json").read_text())
    repeated = json.loads((receipts / "runner-tag-no_op.json").read_text())
    setup = json.loads(
        (root / "tests/acceptance/receipts/gitlab-access.json").read_text()
    )
    target = setup["target"]

    for result in (applied, repeated):
        TAG_VALIDATOR.validate(result)
        assert ACCEPTANCE.check_recorded_identity(result, root / "ci-skills") == []
        assert result["skill"]["revision"]["verified"] is True
        assert result["tested_revision"] == result["skill"]["revision"]["value"]
        assert {
            field: result["verified_target"][field]
            for field in ("kind", "id", "full_path")
        } == {field: target[field] for field in ("kind", "id", "full_path")}
        assert result["plan"]["target_kind"] == target["kind"]
        assert result["plan"]["target_reference"] == target["full_path"]
        assert result["readback"]["verified"] is True

    added = [
        tag
        for tag in applied["plan"]["requested_tags"]
        if tag not in applied["readback"]["before_tags"]
    ]
    assert applied["result_action"] == "APPLIED"
    assert applied["readback"]["added_tags"] == added
    assert set(applied["readback"]["before_tags"]).isdisjoint(added)
    assert set(applied["readback"]["before_tags"] + added) <= set(
        applied["readback"]["after_tags"]
    )
    assert repeated["result_action"] == "NO_OP"
    assert repeated["readback"]["mutated"] is False
    assert applied["plan_digest"] == repeated["plan_digest"]
    assert applied["readback"]["id"] == repeated["readback"]["id"]
    assert applied["readback"]["after_tags"] == repeated["readback"]["after_tags"]


def _bound_session(
    plan: ActionPlan,
    *,
    credential_source: str = "env:GITLAB_TOKEN",
    credential_digest: str = "sha256:unit",
    skill: Optional[JSONObject] = None,
    environment: Optional[Mapping[str, Optional[str]]] = None,
) -> BoundGitLabSession:
    """Create the concrete session shape used by GitLab workflow failures.

    :param plan: Action plan whose selected target seeds the session.
    :param credential_source: Nonsecret credential provenance for receipts.
    :param credential_digest: One-way credential continuity digest.
    :param skill: Optional installed-skill identity supplied by the test.
    :param environment: Explicit synthetic child credentials for transport tests.
    :returns: Bound GitLab session with nonsecret fixture identity.
    """
    selected_skill = skill or {
        "digest": "unit",
        "revision": {"value": "a" * 40, "verified": True},
    }
    return authenticated_gitlab_session(
        GITLAB_SESSION.BoundGitLabSession(
            origin=plan.origin,
            host="gitlab.example.test",
            target_kind=plan.target_kind,
            target_reference=plan.target_reference,
            target_source=plan.target_source,
            target_file=Path("/selected/target.toml"),
            credential_source=credential_source,
            credential_digest=credential_digest,
            execution_host="unit-host",
            skill=selected_skill,
            environment={} if environment is None else environment,
        )
    )


def _plan(
    kind: str,
    operation: str,
    body: JSONObject,
    *,
    target_kind: str = "project",
    resource_id: Optional[Union[int, str]] = None,
    token_out: Optional[Path] = None,
    project_ids: Optional[tuple[int, ...]] = None,
) -> ActionPlan:
    """Build a project- or group-scoped GitLab action plan.

    :param kind: Issue, milestone, wiki, or runner resource kind.
    :param operation: Mutation or read verb for that resource.
    :param body: Desired resource fields and options.
    :param target_kind: Project or group scope for the plan.
    :param resource_id: Existing resource identifier for update/tag actions.
    :param token_out: Private one-time runner token destination when creating.
    :param project_ids: Group assignment project IDs when supplied.
    :returns: Action plan with fixture origin, target, and revision.
    """
    return ACTION.ActionPlan(
        kind=kind,
        operation=operation,
        origin="https://gitlab.example.test",
        target_kind=target_kind,
        target_reference="team/repo" if target_kind == "project" else "team",
        target_file="/selected/target.toml",
        target_source="env:CI_SKILLS_TARGET;target:gitlab.project",
        body=body,
        resource_id=resource_id,
        token_out=str(token_out) if token_out else None,
        project_ids=project_ids,
        revision="a" * 40,
    )


def _live_action_dependencies(
    monkeypatch: pytest.MonkeyPatch, plan: ActionPlan
) -> tuple[BoundGitLabSession, JSONObject]:
    """Bind a selected plan to deterministic access doubles.

    :param monkeypatch: Pytest fixture replacing target, bind, API, and access.
    :param plan: Action plan whose origin and scope seed the session.
    :returns: Bound fixture session and passing access receipt.
    """
    session = _bound_session(
        plan, skill={"revision": {"value": None, "verified": False}}
    )
    access = {
        "status": "PASS",
        "identity": {"username": "unit"},
        "target": {"kind": plan.target_kind, "id": 42},
    }
    monkeypatch.setattr(
        GITLAB_ACTION,
        "resolve_gitlab_target",
        lambda _target, _binding, *, dry_run: (object(), "unit"),
    )
    monkeypatch.setattr(GITLAB_ACTION, "make_plan", lambda *_args: plan)
    monkeypatch.setattr(
        GITLAB_ACTION,
        "bind_gitlab_session",
        lambda *_args, **_kwargs: authenticated_gitlab_session(
            session, _kwargs.get("invocation")
        ),
    )
    monkeypatch.setattr(GITLAB_ACTION, "GlabAPIClient", lambda *, timeout: object())
    monkeypatch.setattr(
        GITLAB_ACTION, "check_gitlab_operation_access", lambda *_args, **_kwargs: access
    )
    return session, access


class FakeAPI:
    """Serve scripted GitLab reads and writes while recording request order."""

    def __init__(
        self,
        responses: Mapping[tuple[str, str], Sequence[Union[JSONValue, BaseException]]],
    ):
        """Copy ordered responses into a consumable API request script.

        :param responses: Method/endpoint mapping to successive results or errors.
        """
        self.responses = {key: list(value) for key, value in responses.items()}
        self.calls = []

    def _next(
        self, method: str, endpoint: str, body: Optional[JSONValue] = None
    ) -> JSONValue:
        """Record a request and consume the next scripted API response.

        :param method: HTTP verb used to find the response queue.
        :param endpoint: Relative GitLab API path used to find the queue.
        :param body: POST or PUT payload retained in the call journal.
        :returns: Next provider result for the selected request.
        :raises AssertionError: No scripted response remains for the request.
        :raises BaseException: Scripted provider failure supplied by the test.
        """  # noqa: DOC503 - The dynamic fixture exception is raised verbatim.
        self.calls.append((method, endpoint, body))
        values = self.responses[(method, endpoint)]
        if not values:
            raise AssertionError(f"unexpected repeated API call: {method} {endpoint}")
        value = values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def get_json(self, session: BoundGitLabSession, endpoint: str) -> JSONValue:
        """Consume a scripted GET response for the selected endpoint.

        :param session: Bound session accepted by the API double.
        :param endpoint: Relative GitLab read path.
        :returns: Next scripted GET result.
        """
        return self._next("GET", endpoint)

    def post_json(
        self, session: BoundGitLabSession, endpoint: str, body: JSONValue
    ) -> JSONValue:
        """Consume a scripted POST response for a create endpoint.

        :param session: Bound session accepted by the API double.
        :param endpoint: Relative GitLab create path.
        :param body: Create payload retained in the call journal.
        :returns: Next scripted POST result.
        """
        return self._next("POST", endpoint, body)

    def put_json(
        self, session: BoundGitLabSession, endpoint: str, body: JSONValue
    ) -> JSONValue:
        """Consume a scripted PUT response for an update endpoint.

        :param session: Bound session accepted by the API double.
        :param endpoint: Relative GitLab update path.
        :param body: Update payload retained in the call journal.
        :returns: Next scripted PUT result.
        """
        return self._next("PUT", endpoint, body)

    def delete_json(self, session: BoundGitLabSession, endpoint: str) -> JSONValue:
        """Consume a scripted DELETE response for a resource endpoint.

        :param session: Bound session accepted by the API double.
        :param endpoint: Relative GitLab delete path.
        :returns: Next scripted DELETE result.
        """
        return self._next("DELETE", endpoint)


@pytest.mark.parametrize(
    ("kind", "arguments", "operation"),
    (
        ("gitlab_issue", ["open-bug"], "open-bug"),
        ("gitlab_wiki", ["create"], "create"),
        ("gitlab_milestone", ["create"], "create"),
    ),
)
def test_nonrunner_apply_invokes_selected_operation_without_runner_live_plan(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    arguments: Sequence[str],
    operation: str,
) -> None:
    """Apply non-runner callbacks without a runner-only live-plan gate.

    :param monkeypatch: Pytest fixture replacing access and action execution.
    :param capsys: Pytest fixture capturing the structured action report.
    :param kind: Issue, wiki, or milestone command selected by the case.
    :param arguments: CLI subcommand arguments for that kind.
    :param operation: Operation expected in the final report.

    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan(kind, operation, {"title": "unit"})
    _live_action_dependencies(monkeypatch, plan)
    invoked: MutableSequence[str] = []

    def complete(*_args: str) -> JSONObject:
        """Record the selected operation and return a verified no-op.

        :param *_args: Ignored positional arguments accepted by the fake boundary.
        :returns: Verified NO_OP action record.

        """
        invoked.append(kind)
        return {"action": "NO_OP", "verified": True, "mutated": False}

    if kind == "gitlab_milestone":
        monkeypatch.setattr(MILESTONES.CreateMilestone, "run", lambda _self: complete())
    else:
        monkeypatch.setattr(GITLAB_ACTION, "apply_plan", complete)

    result = ENTRYPOINTS[kind].main(
        [*arguments, "--apply", "--confirm-plan", plan.digest, "--json"]
    )
    report = json.loads(capsys.readouterr().out)

    assert result == 0
    assert invoked == [kind]
    assert report["status"] == "PASS"
    assert report["operation"] == operation


def test_cli_failure_preserves_primary_and_every_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep primary action error and both reverse-cleanup failures.

    :param monkeypatch: Pytest fixture replacing action and auth callbacks.
    :param capsys: Pytest fixture capturing the blocked result.

    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("gitlab_issue", "open-bug", {"title": "unit"})
    _live_action_dependencies(monkeypatch, plan)
    primary = ACTION.ActionError("operation failed")

    class FailingOperation(GITLAB_ACTION.GitLabOperation):
        """Raise the primary action error and a later cleanup error."""

        def run(self) -> JSONObject:
            """Raise the primary action error selected by the test.


            :returns: JSON-compatible fixture value consumed by the assertion.
            :raises ActionError: Always raises the configured primary error.
            """  # noqa: DOC503 - The dynamic fixture exception is raised verbatim.
            raise primary

        def cleanup(self) -> JSONObject:
            """Raise a cleanup error after the primary operation failure.

            :returns: JSON-compatible fixture value consumed by the assertion.
            :raises RuntimeError: Operation cleanup fails.

            """
            raise RuntimeError("operation cleanup")

    def fail_auth_cleanup(_self: GITLAB_ACTION.GitLabAuth) -> None:
        """Raise a second cleanup error from the auth callback.

        :param _self: Auth callback instance accepted by the patched method.
        :returns: None; the helper records behavior through its side effects.
        :raises RuntimeError: Authentication cleanup fails.

        """
        raise RuntimeError("authentication cleanup")

    monkeypatch.setattr(
        GITLAB_ACTION, "operation_callback", lambda action: FailingOperation(action)
    )
    monkeypatch.setattr(GITLAB_ACTION.GitLabAuth, "cleanup", fail_auth_cleanup)

    result = ISSUE.main(
        ["open-bug", "--apply", "--confirm-plan", plan.digest, "--json"]
    )
    report = json.loads(capsys.readouterr().out)

    assert result == 2
    assert report["status"] == "BLOCKED"
    assert report["cleanup"]["status"] == "BLOCKED"
    assert report["errors"] == [
        {"source": "gitlab_action", "reason": "operation failed"},
        {"source": "cleanup.FailingOperation", "reason": "operation cleanup"},
        {"source": "cleanup.GitLabAuth", "reason": "authentication cleanup"},
    ]


def test_entrypoint_reports_operation_factory_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Render callback-construction failure as a structured usage error.

    :param monkeypatch: Pytest fixture replacing the callback factory.
    :param capsys: Pytest fixture capturing the blocked result.

    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("gitlab_issue", "open-bug", {"title": "unit"})
    _live_action_dependencies(monkeypatch, plan)
    monkeypatch.setattr(
        GITLAB_ACTION,
        "operation_callback",
        lambda _action: (_ for _ in ()).throw(ACTION.ActionError(FACTORY_FAILURE)),
    )

    result = ISSUE.main(
        [
            "open-bug",
            "--title",
            "unit",
            "--apply",
            "--confirm-plan",
            plan.digest,
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert result == 2
    assert report["status"] == "BLOCKED"
    assert report["errors"] == [{"source": "gitlab_action", "reason": FACTORY_FAILURE}]


def test_cli_cleanup_failure_blocks_a_completed_report_without_losing_records(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep completed records while a later cleanup failure blocks status.

    :param monkeypatch: Pytest fixture replacing the operation callback.
    :param capsys: Pytest fixture capturing the final blocked report.

    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("gitlab_issue", "open-bug", {"title": "unit"})
    _live_action_dependencies(monkeypatch, plan)

    class CompletedOperation(GITLAB_ACTION.GitLabOperation):
        """Complete an action and report before a failing cleanup hook."""

        def run(self) -> JSONObject:
            """Return a verified no-op before constructing the report.

            :returns: NO_OP action record.
            """
            return {"id": 42, "action": "NO_OP", "verified": True}

        def cleanup(self) -> JSONObject:
            """Fail after the passing report has already been attached.

            :returns: JSON-compatible fixture value consumed by the assertion.
            :raises RuntimeError: Post-success cleanup fails.

            """
            raise RuntimeError("post-success cleanup")

    monkeypatch.setattr(
        GITLAB_ACTION, "operation_callback", lambda action: CompletedOperation(action)
    )

    result = ISSUE.main(
        ["open-bug", "--apply", "--confirm-plan", plan.digest, "--json"]
    )
    report = json.loads(capsys.readouterr().out)

    assert result == 2
    assert report["status"] == "BLOCKED"
    assert report["records"] == [{"id": 42, "action": "NO_OP", "verified": True}]
    assert report["readback"] == {"id": 42, "action": "NO_OP", "verified": True}
    assert report["cleanup"]["status"] == "BLOCKED"
    assert report["errors"][-1] == {
        "source": "cleanup.CompletedOperation",
        "reason": "post-success cleanup",
    }


def _list(endpoint: str) -> str:
    """Append the first bounded page query to a GitLab collection path.

    :param endpoint: Collection endpoint with or without existing query text.
    :returns: Endpoint requesting page one with 100 records per page.
    """
    return endpoint + ("&" if "?" in endpoint else "?") + "per_page=100&page=1"


def _tag_output(plan: ActionPlan, record: JSONObject) -> JSONObject:
    """Render one runner-action record through the shared result adapter.

    :param plan: Confirmed runner action plan.
    :param record: Result returned by the runner action.
    :returns: JSON-compatible result for schema validation.
    """
    errors = [
        {"source": f"project:{item['project_id']}", "reason": item["reason"]}
        for item in record.get("errors", [])
    ]
    data = ACTION._result(
        plan,
        "PASS" if record["verified"] else "PARTIAL",
        phase="APPLY",
        mutated=record["mutated"],
        result_action=record["action"],
        records=[record],
        errors=errors,
        readback=record,
        cleanup=record.get("cleanup", {"status": "NOT_APPLICABLE"}),
        identity={
            "id": 1,
            "username": "test",
            "web_url": "https://gitlab.example.test/test",
        },
        verified_target={
            "id": 42,
            "kind": "project",
            "full_path": "team/repo",
            "web_url": "https://gitlab.example.test/team/repo",
        },
        credential_source="unit",
        credential_digest="sha256:unit",
        skill={
            "algorithm": "sha256-tree-v1",
            "digest": "b" * 64,
            "file_count": 1,
            "revision": {
                "verified": True,
                "value": plan.revision,
                "source": "git_head",
            },
            "consuming_project": {"commit": None, "source": None},
        },
    )
    return json.loads(REPORT.emit(data, "json"))


def test_plan_fingerprint_binds_body_target_and_source() -> None:
    """Change plan digest for different body or scope while hiding body text.
    :returns: None; assertions in the body validate the regression.
    """
    first = _plan("gitlab_issue", "open-bug", {"title": "one"})
    assert first.digest == _plan("gitlab_issue", "open-bug", {"title": "one"}).digest
    assert first.digest != _plan("gitlab_issue", "open-bug", {"title": "two"}).digest
    assert (
        first.digest
        != _plan(
            "gitlab_issue", "open-bug", {"title": "one"}, target_kind="group"
        ).digest
    )
    assert '"one"' not in json.dumps(first.public())


def test_action_uses_project_target_before_user_target_without_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Select project target before home default and bind digest to source.

    :param tmp_path: Scratch project and home target files.
    :param monkeypatch: Pytest fixture setting cwd, HOME, and selector state.
    :param capsys: Pytest fixture capturing both offline plans.

    :returns: None; assertions in the body validate the regression.
    """
    home = tmp_path / "home"
    project = tmp_path / "project"
    user_target = home / ".ci-skills" / "target.toml"
    project_target = project / ".ci-skills" / "target.toml"
    for path, reference in (
        (user_target, "user/repo"),
        (project_target, "project/repo"),
    ):
        path.parent.mkdir(parents=True)
        path.write_text(
            f'[gitlab]\nurl = "https://gitlab.example.test"\nproject = "{reference}"\n',
            encoding="utf-8",
        )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CI_SKILLS_TARGET", raising=False)
    monkeypatch.chdir(project)

    result = ISSUE.main(["open-bug", "--title", "selected", "--json"])
    project_plan = json.loads(capsys.readouterr().out)
    assert result == 0
    assert project_plan["target"]["reference"] == "project/repo"
    assert project_plan["target_source"] == "project;target:gitlab.project"

    project_target.unlink()
    result = ISSUE.main(["open-bug", "--title", "selected", "--json"])
    user_plan = json.loads(capsys.readouterr().out)
    assert result == 0
    assert user_plan["target"]["reference"] == "user/repo"
    assert user_plan["target_source"] == "user;target:gitlab.project"
    assert user_plan["plan_digest"] != project_plan["plan_digest"]


def test_group_action_uses_selected_target_without_group_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Use the selected home group without repeating it on CLI.

    :param tmp_path: Scratch home group target and unrelated cwd.
    :param monkeypatch: Pytest fixture setting HOME and cwd.
    :param capsys: Pytest fixture capturing the runner plan.

    :returns: None; assertions in the body validate the regression.
    """
    home = tmp_path / "home"
    target = home / ".ci-skills" / "target.toml"
    target.parent.mkdir(parents=True)
    target.write_text(
        '[gitlab]\nurl = "https://gitlab.example.test"\ngroup = "platform/team"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CI_SKILLS_TARGET", raising=False)
    monkeypatch.chdir(tmp_path)

    result = RUNNER.main(["assign", "--runner-id", "9", "--json"])
    plan = json.loads(capsys.readouterr().out)

    assert result == 0
    assert plan["target"] == {"kind": "group", "reference": "platform/team"}
    assert plan["target_source"] == "user;target:gitlab.group"


def test_declared_project_override_replaces_selected_target_for_one_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Use explicit --project to override the target's stored project.

    :param tmp_path: Directory receiving the selected target file.
    :param monkeypatch: Pytest monkeypatch fixture used to isolate process state.
    :param capsys: Pytest fixture capturing the overridden plan.
    :returns: None; assertions in the body validate the regression.
    """
    target = tmp_path / "selected.toml"
    target.write_text(
        '[gitlab]\nurl = "https://gitlab.example.test"\nproject = "stored/repo"\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("CI_SKILLS_TARGET", str(target))

    result = ISSUE.main(
        [
            "open-bug",
            "--title",
            "selected",
            "--project",
            "override/repo",
            "--json",
        ],
    )
    plan = json.loads(capsys.readouterr().out)

    assert result == 0
    assert plan["target"]["reference"] == "override/repo"
    assert plan["target_source"] == "env:CI_SKILLS_TARGET;argv:--project"


def test_milestone_create_reads_back_numeric_id() -> None:
    """Verify milestone creation by independent numeric-ID read-back.
    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan(
        "gitlab_milestone", "create", {"title": "release", "due_date": "2026-10-31"}
    )
    base = "projects/42/milestones"
    api = FakeAPI(
        {
            ("GET", _list(base + "?title=release")): [[]],
            ("POST", base): [{"id": 5}],
            ("GET", base + "/5"): [
                {"id": 5, "title": "release", "due_date": "2026-10-31"}
            ],
        }
    )
    result = MILESTONES.apply(api, _bound_session(plan), plan, 42)
    assert result == {
        "action": "APPLIED",
        "id": 5,
        "verified": True,
        "title": "release",
    }
    assert [call[0] for call in api.calls] == ["GET", "POST", "GET"]


def test_milestone_update_blocks_failed_readback() -> None:
    """Retain mutation evidence when milestone due-date read-back is stale.
    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan(
        "gitlab_milestone", "adjust-time", {"due_date": "2026-10-31"}, resource_id=5
    )
    base = "projects/42/milestones/5"
    api = FakeAPI(
        {
            ("GET", base): [
                {"id": 5, "title": "release", "due_date": "2026-10-01"},
                {"id": 5, "title": "release", "due_date": "2026-10-01"},
            ],
            ("PUT", base): [{"id": 5}],
        }
    )
    result = MILESTONES.apply(api, _bound_session(plan), plan, 42)
    assert result["verified"] is False
    assert result["mutated"] is True
    assert result["id"] == 5
    assert result["errors"][0]["reason"] == "milestone_readback_mismatch"


def test_issue_reuses_exact_open_title_only_after_independent_get() -> None:
    """Return issue NO_OP only after exact open-title detail read-back.
    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("gitlab_issue", "open-bug", {"title": "broken", "labels": "bug"})
    base = "projects/42/issues"
    query = _list(base + "?state=all&search=broken")
    api = FakeAPI(
        {
            ("GET", query): [[{"iid": 8, "title": "broken", "state": "opened"}]],
            ("GET", base + "/8"): [
                {"iid": 8, "title": "broken", "state": "opened", "labels": ["bug"]}
            ],
        }
    )
    result = ISSUES.apply(api, _bound_session(plan), plan, 42)
    assert result == {"action": "NO_OP", "iid": 8, "verified": True}
    assert all(method == "GET" for method, _, _ in api.calls)


def test_issue_create_accepts_provider_trimmed_terminal_description_newline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify creation and reuse when GitLab removes only a terminal newline.

    :param monkeypatch: Pytest fixture isolating the issue create lock directory.
    :param tmp_path: Scratch directory receiving the create lock.
    :returns: None; assertions prove creation and the next idempotent read.
    """
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    description = "first line\nsecond line\n"
    plan = _plan(
        "gitlab_issue",
        "open-bug",
        {"title": "terminal-newline", "description": description},
    )
    base = "projects/42/issues"
    query = _list(base + "?state=all&search=terminal-newline")
    observed = {
        "iid": 8,
        "title": "terminal-newline",
        "description": description.rstrip("\n"),
        "state": "opened",
    }
    api = FakeAPI(
        {
            ("GET", query): [
                [],
                [{"iid": 8, "title": "terminal-newline", "state": "opened"}],
            ],
            ("POST", base): [{"iid": 8}],
            ("GET", base + "/8"): [observed, observed],
        }
    )

    applied = ISSUES.apply(api, _bound_session(plan), plan, 42)
    repeated = ISSUES.apply(api, _bound_session(plan), plan, 42)

    assert applied == {
        "action": "APPLIED",
        "iid": 8,
        "verified": True,
        "web_url": None,
    }
    assert repeated == {"action": "NO_OP", "iid": 8, "verified": True}
    assert [method for method, _endpoint, _body in api.calls].count("POST") == 1


@pytest.mark.parametrize(
    ("body", "observed"),
    (
        (
            {"title": "description-change", "description": "first\nsecond\n"},
            {
                "iid": 8,
                "title": "description-change",
                "description": "first\nchanged",
                "state": "opened",
            },
        ),
        (
            {"title": "internal-space", "description": "first  second\n"},
            {
                "iid": 8,
                "title": "internal-space",
                "description": "first second",
                "state": "opened",
            },
        ),
        (
            {
                "title": "label-change",
                "description": "unchanged\n",
                "labels": "expected",
            },
            {
                "iid": 8,
                "title": "label-change",
                "description": "unchanged",
                "labels": ["other"],
                "state": "opened",
            },
        ),
    ),
)
def test_issue_create_rejects_changes_beyond_terminal_description_newline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    body: JSONObject,
    observed: JSONObject,
) -> None:
    """Keep description content and non-description fields exact after a write.

    :param monkeypatch: Pytest fixture isolating the issue create lock directory.
    :param tmp_path: Scratch directory receiving the create lock.
    :param body: Planned issue fields, including the exact description when present.
    :param observed: Provider read-back that differs beyond a terminal newline.
    :returns: None; assertions prove the write remains explicitly unverified.
    """
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    plan = _plan("gitlab_issue", "open-bug", body)
    base = "projects/42/issues"
    title = body["title"]
    assert isinstance(title, str)
    api = FakeAPI(
        {
            ("GET", _list(base + f"?state=all&search={title}")): [[]],
            ("POST", base): [{"iid": 8}],
            ("GET", base + "/8"): [observed],
        }
    )

    result = ISSUES.apply(api, _bound_session(plan), plan, 42)

    assert result["action"] == "APPLIED"
    assert result["verified"] is False
    assert result["errors"] == [{"project_id": 42, "reason": "issue_readback_mismatch"}]


def test_issue_create_refuses_closed_exact_title_instead_of_duplicating() -> None:
    """Refuse to create a duplicate of a closed exact-title issue.
    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("gitlab_issue", "open-bug", {"title": "broken"})
    base = "projects/42/issues"
    api = FakeAPI(
        {
            ("GET", _list(base + "?state=all&search=broken")): [
                [{"iid": 8, "title": "broken", "state": "closed"}]
            ],
        }
    )
    with pytest.raises(ACTION.ActionError, match="existing_issue_closed"):
        ISSUES.apply(api, _bound_session(plan), plan, 42)
    assert [call[0] for call in api.calls] == ["GET"]


def test_issue_create_blocks_when_independent_readback_is_not_open() -> None:
    """Mark created issue unverified if independent detail is closed.
    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("gitlab_issue", "open-bug", {"title": "broken"})
    base = "projects/42/issues"
    api = FakeAPI(
        {
            ("GET", _list(base + "?state=all&search=broken")): [[]],
            ("POST", base): [{"iid": 9}],
            ("GET", base + "/9"): [{"iid": 9, "title": "broken", "state": "closed"}],
        }
    )
    result = ISSUES.apply(api, _bound_session(plan), plan, 42)
    assert result["verified"] is False
    assert result["mutated"] is True
    assert result["iid"] == 9
    assert result["errors"][0]["reason"] == "issue_readback_mismatch"
    assert [call[0] for call in api.calls] == ["GET", "POST", "GET"]


def test_wiki_update_reads_back_new_slug() -> None:
    """Read the new wiki slug after a rename update.
    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan(
        "gitlab_wiki", "update", {"content": "new", "title": "New"}, resource_id="old"
    )
    base = "projects/42/wikis"
    api = FakeAPI(
        {
            ("GET", base + "/old"): [{"slug": "old", "content": "old", "title": "Old"}],
            ("PUT", base + "/old"): [{"slug": "new"}],
            ("GET", base + "/new"): [{"slug": "new", "content": "new", "title": "New"}],
        }
    )
    assert WIKIS.apply(api, _bound_session(plan), plan, 42)["slug"] == "new"


def test_wiki_create_accepts_provider_normalized_title_and_reuses_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accept GitLab title normalization and reuse the created page.

    :param tmp_path: Private directory holding the create guard lock.
    :param monkeypatch: Pytest fixture selecting that lock directory.

    :returns: None; assertions in the body validate the regression.
    """
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    plan = _plan(
        "gitlab_wiki",
        "create",
        {"title": "ci-skills acceptance", "content": "accepted\n"},
    )
    base = "projects/42/wikis"
    page = {
        "slug": "ci-skills-acceptance",
        "title": "ci skills acceptance",
        "content": "accepted\n",
    }
    api = FakeAPI(
        {
            ("GET", _list(base)): [[], [page]],
            ("POST", base): [{"slug": page["slug"]}],
            ("GET", base + "/ci-skills-acceptance"): [page, page],
        }
    )
    first = WIKIS.apply(api, _bound_session(plan), plan, 42)
    second = WIKIS.apply(api, _bound_session(plan), plan, 42)
    assert first["action"] == "APPLIED" and first["verified"] is True
    assert second["action"] == "NO_OP" and second["verified"] is True
    assert [call[0] for call in api.calls].count("POST") == 1


def test_wiki_update_accepts_provider_normalized_title() -> None:
    """Verify a wiki update despite provider-normalized title text.
    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan(
        "gitlab_wiki",
        "update",
        {"title": "ci-skills acceptance", "content": "new\n"},
        resource_id="old",
    )
    base = "projects/42/wikis"
    api = FakeAPI(
        {
            ("GET", base + "/old"): [
                {"slug": "old", "title": "old", "content": "old\n"}
            ],
            ("PUT", base + "/old"): [{"slug": "ci-skills-acceptance"}],
            ("GET", base + "/ci-skills-acceptance"): [
                {
                    "slug": "ci-skills-acceptance",
                    "title": "ci skills acceptance",
                    "content": "new\n",
                }
            ],
        }
    )
    result = WIKIS.apply(api, _bound_session(plan), plan, 42)
    assert result["action"] == "APPLIED" and result["verified"] is True


def test_runner_group_assign_verifies_each_project_and_marks_partial() -> None:
    """Rollback a partial group assignment after one project fails read-back.
    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan(
        "gitlab_runner",
        "assign",
        {},
        target_kind="group",
        resource_id=7,
        project_ids=(11, 12),
    )
    projects = _list("groups/42/projects?include_subgroups=true&with_shared=false")
    api = FakeAPI(
        {
            ("GET", "runners/7"): [
                {"id": 7, "runner_type": "project_type", "projects": []},
                {"id": 7, "runner_type": "project_type", "projects": []},
                {"id": 7, "runner_type": "project_type", "projects": [{"id": 11}]},
                {"id": 7, "runner_type": "project_type", "projects": [{"id": 11}]},
                {"id": 7, "runner_type": "project_type", "projects": [{"id": 11}]},
                {"id": 7, "runner_type": "project_type", "projects": [{"id": 11}]},
                {"id": 7, "runner_type": "project_type", "projects": [{"id": 11}]},
                {"id": 7, "runner_type": "project_type", "projects": []},
                {"id": 7, "runner_type": "project_type", "projects": []},
            ],
            ("GET", projects): [[{"id": 11}, {"id": 12}]],
            ("POST", "projects/11/runners"): [{"id": 7}],
            ("DELETE", "projects/11/runners/7"): [{}],
            ("POST", "projects/12/runners"): [{"id": 7}],
        }
    )
    result = RUNNERS.apply(api, _bound_session(plan), plan, 42)
    assert result["verified"] is False
    assert result["action"] == "PARTIAL"
    assert [item["project_id"] for item in result["projects"]] == [11]
    assert result["errors"][0]["project_id"] == 12
    assert result["cleanup"]["status"] == "PASS"
    assert result["projects"][0]["action"] == "ROLLED_BACK"
    assert result["mutated"] is False


def test_runner_create_saves_token_0600_without_reporting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persist one-time runner token privately without serializing it.

    :param tmp_path: Private lock and token destination directory.
    :param monkeypatch: Pytest fixture selecting the lock directory.

    :returns: None; assertions in the body validate the regression.
    """
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    destination = tmp_path / "one-time.token"
    plan = _plan(
        "gitlab_runner",
        "create",
        {
            "runner_type": "project_type",
            "description": "unique-runner",
            "tag_list": "first,second",
        },
        token_out=destination,
    )
    scope = "projects/42/runners"
    api = FakeAPI(
        {
            ("GET", _list(scope)): [[], [{"id": 23, "description": "unique-runner"}]],
            ("POST", "user/runners"): [{"id": 23, "token": "private-one-time-value"}],
            ("GET", "runners/23"): [
                {
                    "id": 23,
                    "description": "unique-runner",
                    "tag_list": ["first", "second"],
                }
            ],
        }
    )
    result = RUNNERS.apply(api, _bound_session(plan), plan, 42)
    assert result["action"] == "APPLIED"
    assert result["id"] == 23
    assert result["verified"] is True
    assert result["sink_persisted"] is True
    assert result["cleanup"]["status"] == "NOT_APPLICABLE"
    assert destination.read_text() == "private-one-time-value\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert "private-one-time-value" not in json.dumps(result)
    assert str(destination) not in json.dumps(result)
    assert result["after_tags"] == ["first", "second"]


def test_runner_tag_adds_to_selected_runner_and_reads_back() -> None:
    """Verify additive tag mutation on a runner in the selected project.
    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("gitlab_runner", "tag", {"tag_list": ["new"]}, resource_id=23)
    scope = "projects/42/runners"
    runner = {
        "id": 23,
        "description": "owned-runner",
        "runner_type": "project_type",
        "tag_list": ["existing"],
    }
    api = FakeAPI(
        {
            ("GET", "runners/23"): [
                runner,
                {**runner, "tag_list": ["existing", "new"]},
            ],
            ("GET", _list(scope)): [[{"id": 23}], [{"id": 23}]],
            ("PUT", "runners/23"): [{"id": 23}],
        }
    )
    result = RUNNERS.apply(api, _bound_session(plan), plan, 42)
    assert result == {
        "action": "APPLIED",
        "id": 23,
        "verified": True,
        "mutated": True,
        "before_tags": ["existing"],
        "after_tags": ["existing", "new"],
        "added_tags": ["new"],
    }
    assert ("PUT", "runners/23", {"tag_list": ["existing", "new"]}) in api.calls
    output = _tag_output(plan, result)
    TAG_VALIDATOR.validate(output)
    for broken in (
        {"tested_revision": None},
        {"identity": {}},
        {"verified_target": {}},
        {"skill": {"revision": {"verified": False, "value": plan.revision}}},
    ):
        assert not TAG_VALIDATOR.is_valid({**output, **broken})
    for field in ("digest", "algorithm", "file_count", "consuming_project"):
        invalid = deepcopy(output)
        del invalid["skill"][field]
        assert not TAG_VALIDATOR.is_valid(invalid)
    for field in ("web_url", "username"):
        invalid = deepcopy(output)
        del invalid["identity"][field]
        assert not TAG_VALIDATOR.is_valid(invalid)
    for field in ("web_url", "full_path"):
        invalid = deepcopy(output)
        del invalid["verified_target"][field]
        assert not TAG_VALIDATOR.is_valid(invalid)
    for field in ("identity", "verified_target", "skill"):
        invalid = deepcopy(output)
        invalid[field]["unexpected"] = True
        assert not TAG_VALIDATOR.is_valid(invalid)


def test_runner_tag_uses_only_verified_bound_revision() -> None:
    """Ignore a revision claim unless its bound skill evidence is verified.
    :returns: None; assertions in the body validate the regression.
    """
    verified = SimpleNamespace(
        skill={"revision": {"verified": True, "value": "a" * 40}}
    )
    unverified = SimpleNamespace(
        skill={"revision": {"verified": False, "value": "a" * 40}}
    )
    assert ACTION._verified_skill_revision(verified) == "a" * 40
    assert ACTION._verified_skill_revision(unverified) is None


def test_runner_tag_is_no_op_when_tag_already_exists() -> None:
    """Avoid PUT when the selected runner already carries the tag.
    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("gitlab_runner", "tag", {"tag_list": ["existing"]}, resource_id=23)
    api = FakeAPI(
        {
            ("GET", "runners/23"): [
                {
                    "id": 23,
                    "description": "owned-runner",
                    "runner_type": "project_type",
                    "tag_list": ["existing"],
                }
            ],
            ("GET", _list("projects/42/runners")): [[{"id": 23}]],
        }
    )
    result = RUNNERS.apply(api, _bound_session(plan), plan, 42)
    assert result["action"] == "NO_OP"
    assert result["mutated"] is False
    assert all(call[0] != "PUT" for call in api.calls)
    TAG_VALIDATOR.validate(_tag_output(plan, result))


def test_runner_tag_refuses_runner_outside_selected_project() -> None:
    """Refuse tag mutation for a runner outside the selected project.
    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("gitlab_runner", "tag", {"tag_list": ["new"]}, resource_id=23)
    api = FakeAPI(
        {
            ("GET", "runners/23"): [
                {
                    "id": 23,
                    "description": "elsewhere",
                    "runner_type": "project_type",
                    "tag_list": [],
                }
            ],
            ("GET", _list("projects/42/runners")): [[]],
        }
    )
    with pytest.raises(ACTION.ActionError, match="runner_scope_readback_mismatch"):
        RUNNERS.apply(api, _bound_session(plan), plan, 42)
    assert all(call[0] != "PUT" for call in api.calls)


def test_runner_tag_partial_output_requires_readback_and_cleanup() -> None:
    """Require partial tag records to include read-back and cleanup state.
    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("gitlab_runner", "tag", {"tag_list": ["new"]}, resource_id=23)
    current = {
        "id": 23,
        "description": "owned-runner",
        "runner_type": "project_type",
        "tag_list": ["existing"],
    }
    api = FakeAPI(
        {
            ("GET", "runners/23"): [current, current, current],
            ("GET", _list("projects/42/runners")): [
                [{"id": 23}],
                [{"id": 23}],
                [{"id": 23}],
            ],
            ("PUT", "runners/23"): [{"id": 23}, {"id": 23}],
        }
    )
    result = RUNNERS.apply(api, _bound_session(plan), plan, 42)
    assert result["verified"] is False
    assert result["action"] == "UNVERIFIED"
    assert result["mutated"] is None
    assert result["after_tags"] == ["existing"]
    assert result["errors"][0]["reason"] == "runner_tag_readback_mismatch"
    output = _tag_output(plan, result)
    TAG_VALIDATOR.validate(output)
    for field in ("readback", "mutated", "cleanup"):
        invalid = deepcopy(output)
        del invalid[field]
        assert not TAG_VALIDATOR.is_valid(invalid)


def test_runner_tag_uncertain_put_and_failed_readback_has_unknown_mutation() -> None:
    """Keep mutation unknown when neither PUT nor GET confirms the outcome.
    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("gitlab_runner", "tag", {"tag_list": ["new"]}, resource_id=23)
    current = {
        "id": 23,
        "description": "owned-runner",
        "runner_type": "project_type",
        "tag_list": ["existing"],
    }
    api = FakeAPI(
        {
            ("GET", "runners/23"): [current, API.GitLabAPIError("timeout")],
            ("GET", _list("projects/42/runners")): [[{"id": 23}]],
            ("PUT", "runners/23"): [API.GitLabAPIError("timeout")],
        }
    )
    result = RUNNERS.apply(api, _bound_session(plan), plan, 42)
    assert result["action"] == "UNVERIFIED"
    assert result["mutated"] is None
    assert result["verified"] is False
    assert result["uncertain"] is True
    assert result["errors"][0]["reason"] == "runner_tag_readback_unavailable"
    TAG_VALIDATOR.validate(_tag_output(plan, result))


def test_concurrent_runner_tag_additions_preserve_both_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serialize concurrent tag updates so neither requested tag is lost.

    :param tmp_path: Private lock directory for both update workers.
    :param monkeypatch: Pytest fixture selecting that lock directory.

    :returns: None; assertions in the body validate the regression.
    """
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    first_write_started = Event()
    release_first_write = Event()
    second_runner_read = Event()

    class SharedRunnerAPI:
        """Synchronize competing runner-tag updates against one mutable tag list."""

        def __init__(self):
            """Seed existing tags, request counters, and a synchronization lock."""
            self.tags = ["existing"]
            self.read_count = 0
            self.write_count = 0
            self.lock = Lock()

        def get_json(
            self, _session: BoundGitLabSession, endpoint: str
        ) -> Union[JSONObject, Sequence[JSONValue]]:
            """Return current runner tags or selected-project assignment.

            :param _session: Bound session accepted by the shared API double.
            :param endpoint: Runner detail or project runner-list path.
            :returns: Current runner detail or project assignment list.
            """
            if endpoint != "runners/23":
                return [{"id": 23}]
            with self.lock:
                self.read_count += 1
                if self.read_count == 2:
                    second_runner_read.set()
                tags = list(self.tags)
            return {
                "id": 23,
                "description": "owned-runner",
                "runner_type": "project_type",
                "tag_list": tags,
            }

        def put_json(
            self, _session: BoundGitLabSession, _endpoint: str, body: JSONValue
        ) -> JSONObject:
            """Hold first write until the second worker contests the same lock.

            :param _session: Bound session accepted by the shared API double.
            :param _endpoint: Runner update path accepted by the double.
            :param body: New tag list to publish after the first-write barrier.
            :returns: Updated runner identity.
            """
            with self.lock:
                self.write_count += 1
                first = self.write_count == 1
            if first:
                first_write_started.set()
                assert release_first_write.wait(2)
            with self.lock:
                self.tags = list(body["tag_list"])
            return {"id": 23}

    api = SharedRunnerAPI()
    first = _plan("gitlab_runner", "tag", {"tag_list": ["alpha"]}, resource_id=23)
    second = _plan("gitlab_runner", "tag", {"tag_list": ["beta"]}, resource_id=23)
    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha = pool.submit(RUNNERS.apply, api, _bound_session(first), first, 42)
        assert first_write_started.wait(2)
        beta = pool.submit(RUNNERS.apply, api, _bound_session(second), second, 42)
        if second_runner_read.wait(0.1):
            beta.result(timeout=2)
        release_first_write.set()
        results = [alpha.result(timeout=2), beta.result(timeout=2)]
    assert all(result["verified"] for result in results)
    assert set(api.tags) == {"existing", "alpha", "beta"}


def test_runner_tag_dry_run_and_refusal_match_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Validate runner tag dry-run and missing-tag refusal against schema.

    :param tmp_path: Directory receiving the selected project target.
    :param monkeypatch: Pytest fixture selecting the target by environment.
    :param capsys: Pytest fixture capturing plan and refusal reports.

    :returns: None; assertions in the body validate the regression.
    """
    target = tmp_path / "target.toml"
    target.write_text(
        '[gitlab]\nurl = "https://gitlab.example.test"\nproject = "team/repo"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CI_SKILLS_TARGET", str(target))
    common = ["tag", "--runner-id", "23", "--json"]
    assert RUNNER.main([*common, "--tag", "new"]) == 0
    dry_run = json.loads(capsys.readouterr().out)
    TAG_VALIDATOR.validate(dry_run)
    assert dry_run["status"] == "DRY_RUN"

    assert RUNNER.main(common) != 0
    blocked = json.loads(capsys.readouterr().out)
    TAG_VALIDATOR.validate(blocked)
    assert blocked["errors"][0]["reason"] == "tag_requires_tag"


def test_runner_create_uncertain_provider_result_leaves_no_token_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep uncertain runner create from writing a one-time token file.

    :param tmp_path: Private lock and token destination directory.
    :param monkeypatch: Pytest fixture selecting the lock directory.

    :returns: None; assertions in the body validate the regression.
    """
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    destination = tmp_path / "one-time.token"
    plan = _plan(
        "gitlab_runner",
        "create",
        {"runner_type": "project_type", "description": "unique-runner"},
        token_out=destination,
    )
    scope = "projects/42/runners"
    api = FakeAPI(
        {
            ("GET", _list(scope)): [[]],
            ("POST", "user/runners"): [API.GitLabAPIError("provider_5xx")],
        }
    )
    result = RUNNERS.apply(api, _bound_session(plan), plan, 42)
    assert result["action"] == "PARTIAL"
    assert result["mutated"] is None
    assert result["cleanup"]["status"] == "BLOCKED"
    assert not destination.exists()
    assert "provider_5xx" not in str(api.calls)
    assert [call[0] for call in api.calls] == ["GET", "POST"]


def test_receipt_is_portable_and_has_no_token_out_path(tmp_path: Path) -> None:
    """Redact token destination and credential source in portable receipt.

    :param tmp_path: Scratch token path and receipt destination.

    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan(
        "gitlab_runner",
        "create",
        {"description": "runner"},
        token_out=tmp_path / "secret-token",
    )
    data = ACTION._result(
        plan,
        "PASS",
        records=[{"id": 23, "action": "APPLIED", "verified": True}],
        credential_source="file:/private/operator/token",
    )
    output = tmp_path / "receipt.json"
    PORTABLE.write_portable_receipt(data, str(output))
    saved = output.read_text()
    assert "/private/operator/token" not in saved
    assert str(tmp_path / "secret-token") not in saved
    assert "file:path:" in saved


def test_transport_pins_host_and_sends_body_through_private_file() -> None:
    """Keep write body out of argv and use a mode-0600 input file.
    :returns: None; assertions in the body validate the regression.
    """
    captured = {}

    def command(
        argv: Sequence[str], *, timeout: int, env: Optional[Mapping[str, Optional[str]]]
    ) -> SimpleNamespace:
        """Inspect selected host, child token, and private JSON input file.

        :param argv: Native glab POST command with ``--input`` path.
        :param timeout: Bounded request timeout accepted by the double.
        :param env: Selected child token environment captured for assertions.
        :returns: Successful provider response containing runner ID 23.
        """
        captured["argv"] = argv
        captured["env"] = env
        captured["body"] = json.loads(Path(argv[argv.index("--input") + 1]).read_text())
        assert (
            stat.S_IMODE(Path(argv[argv.index("--input") + 1]).stat().st_mode) == 0o600
        )
        return SimpleNamespace(returncode=0, stdout='{"id":23}', stderr="")

    session = _bound_session(
        _plan("gitlab_runner", "create", {}),
        environment={"GITLAB_TOKEN": "selected"},
    )
    result = API.GlabAPIClient(command=command).post_json(
        session, "user/runners", {"description": "runner"}
    )
    assert result == {"id": 23}
    assert (
        captured["argv"][captured["argv"].index("--hostname") + 1]
        == "gitlab.example.test"
    )
    assert "runner" not in captured["argv"]
    assert captured["env"]["GITLAB_TOKEN"] == "selected"
    assert captured["body"] == {"description": "runner"}


def test_apply_cli_malformed_provider_response_emits_structured_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Render malformed provider list as a blocked issue action report.

    :param monkeypatch: Pytest fixture replacing target, session, and API.
    :param tmp_path: Unused pytest path fixture; no filesystem effect is asserted.
    :param capsys: Pytest fixture capturing the structured result.

    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("gitlab_issue", "open-bug", {"title": "broken"})
    api = FakeAPI(
        {
            (
                "GET",
                _list("projects/42/issues?state=all&search=broken"),
            ): [{}],
        }
    )
    session = _bound_session(plan)
    monkeypatch.setattr(
        GITLAB_ACTION,
        "resolve_gitlab_target",
        lambda _target, _binding, *, dry_run: (object(), "test"),
    )
    monkeypatch.setattr(GITLAB_ACTION, "make_plan", lambda *_args: plan)
    monkeypatch.setattr(
        GITLAB_ACTION,
        "bind_gitlab_session",
        lambda *_args, **_kwargs: authenticated_gitlab_session(
            session, _kwargs.get("invocation")
        ),
    )
    monkeypatch.setattr(GITLAB_ACTION, "GlabAPIClient", lambda *, timeout: api)
    monkeypatch.setattr(
        GITLAB_ACTION,
        "check_gitlab_operation_access",
        lambda _session, api_client: {
            "status": "PASS",
            "identity": {"username": "operator"},
            "target": {"kind": "project", "id": 42},
        },
    )
    result = ISSUE.main(
        [
            "open-bug",
            "--title",
            "broken",
            "--apply",
            "--confirm-plan",
            plan.digest,
            "--json",
        ],
    )
    data = json.loads(capsys.readouterr().out)
    assert result == 2
    assert data["status"] == "BLOCKED"
    assert data["kind"] == "gitlab_issue"
    assert data["errors"] == [
        {"source": "gitlab_action", "reason": "list_response_invalid_shape"}
    ]


@pytest.mark.parametrize(
    ("subcheck", "reason"),
    (("user", "authentication"), ("target", "target_path_mismatch")),
)
def test_action_access_failure_preserves_the_failed_subcheck(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    subcheck: str,
    reason: str,
) -> None:
    """Keep user or target access refusal and plan digest without applying.

    :param monkeypatch: Pytest fixture replacing access and rejecting apply.
    :param tmp_path: Unused pytest path fixture; no filesystem effect is asserted.
    :param capsys: Pytest fixture capturing the blocked report.
    :param subcheck: User or target access check that fails.
    :param reason: Structured failure reason from that subcheck.

    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("gitlab_issue", "open-bug", {"title": "broken"})
    monkeypatch.setattr(
        GITLAB_ACTION,
        "resolve_gitlab_target",
        lambda _target, _binding, *, dry_run: (object(), "test"),
    )
    monkeypatch.setattr(GITLAB_ACTION, "make_plan", lambda *_args: plan)
    session = _bound_session(plan)
    monkeypatch.setattr(
        GITLAB_ACTION,
        "bind_gitlab_session",
        lambda *_args, **_kwargs: authenticated_gitlab_session(
            session, _kwargs.get("invocation")
        ),
    )
    monkeypatch.setattr(GITLAB_ACTION, "GlabAPIClient", lambda *, timeout: object())
    monkeypatch.setattr(
        GITLAB_ACTION,
        "check_gitlab_operation_access",
        lambda _session, api_client: {
            "status": "BLOCKED",
            "errors": [{"source": subcheck, "reason": reason}],
        },
    )
    monkeypatch.setattr(
        GITLAB_ACTION,
        "apply_plan",
        lambda *_args: pytest.fail("apply ran after access failure"),
    )

    result = ISSUE.main(
        [
            "open-bug",
            "--title",
            "broken",
            "--apply",
            "--confirm-plan",
            plan.digest,
            "--json",
        ],
    )
    data = json.loads(capsys.readouterr().out)

    assert result == 2
    assert data["status"] == "BLOCKED"
    assert data["errors"] == [{"source": f"gitlab_access.{subcheck}", "reason": reason}]
    assert data["plan_digest"] == plan.digest
    assert data["phase"] == "ACCESS"
    assert data["mutated"] is False


def test_failed_apply_writes_plan_bound_receipt_with_unknown_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Write a portable blocked receipt retaining unknown mutation evidence.

    :param monkeypatch: Pytest fixture replacing access and post-write action.
    :param tmp_path: Directory receiving the blocked receipt.
    :param capsys: Pytest fixture capturing the displayed report.

    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan("gitlab_issue", "open-bug", {"title": "broken"})
    receipt_out = tmp_path / "blocked-receipt.json"
    session = _bound_session(plan, skill={"digest": "unit"})
    access = {
        "status": "PASS",
        "identity": {"username": "operator"},
        "target": {"kind": "project", "id": 42, "full_path": "team/repo"},
    }
    monkeypatch.setattr(
        GITLAB_ACTION,
        "resolve_gitlab_target",
        lambda _target, _binding, *, dry_run: (object(), "test"),
    )
    monkeypatch.setattr(GITLAB_ACTION, "make_plan", lambda *_args: plan)
    monkeypatch.setattr(
        GITLAB_ACTION,
        "bind_gitlab_session",
        lambda *_args, **_kwargs: authenticated_gitlab_session(
            session, _kwargs.get("invocation")
        ),
    )
    monkeypatch.setattr(GITLAB_ACTION, "GlabAPIClient", lambda *, timeout: object())
    monkeypatch.setattr(
        GITLAB_ACTION,
        "check_gitlab_operation_access",
        lambda _session, api_client: access,
    )

    def fail_after_write(*_args: str) -> None:
        """Raise an uncertain create error after the selected write attempt.

        :param *_args: Ignored positional arguments accepted by the fake boundary.
        :returns: None; the helper records behavior through its side effects.
        :raises ACTION.ActionError: Write outcome needs independent title read-back.

        """
        raise ACTION.ActionError(
            "issue_create_outcome_uncertain_check_title_before_retry"
        )

    monkeypatch.setattr(GITLAB_ACTION, "apply_plan", fail_after_write)

    result = ISSUE.main(
        [
            "open-bug",
            "--title",
            "broken",
            "--apply",
            "--confirm-plan",
            plan.digest,
            "--receipt-out",
            str(receipt_out),
            "--json",
        ],
    )
    report = json.loads(capsys.readouterr().out)
    receipt = json.loads(receipt_out.read_text(encoding="utf-8"))

    assert result == 2
    assert report["status"] == receipt["status"] == "BLOCKED"
    assert report["phase"] == receipt["phase"] == "APPLY"
    assert report["plan_digest"] == receipt["plan_digest"] == plan.digest
    assert report["verified_target"] == receipt["verified_target"] == access["target"]
    assert report["credential_source"] == "env:GITLAB_TOKEN"
    assert report["identity"] == access["identity"]
    assert report["mutated"] is receipt["mutated"] is None
    assert report["readback"]["verified"] is False
    assert report["cleanup"]["status"] == "BLOCKED"
    assert receipt["target_file"].startswith("path:")
    assert report["errors"] == [
        {
            "source": "gitlab_action",
            "reason": "issue_create_outcome_uncertain_check_title_before_retry",
        }
    ]


@pytest.mark.parametrize("dry_run_flag", ([], ["--dry-run"]))
def test_action_plan_rejects_output_paths_without_creating_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], dry_run_flag: Sequence[str]
) -> None:
    """Reject output directory writes in offline plan or explicit dry-run.

    :param tmp_path: Scratch report directory that must remain absent.
    :param capsys: Pytest fixture capturing the usage failure.
    :param dry_run_flag: Optional explicit dry-run flag for this case.

    :returns: None; assertions in the body validate the regression.
    """
    output = tmp_path / "reports"
    result = ISSUE.main(
        [
            "open-bug",
            "--project",
            "unit/repo",
            "--title",
            "planned",
            "--output-dir",
            str(output),
            "--json",
            *dry_run_flag,
        ],
    )
    report = json.loads(capsys.readouterr().out)
    assert result == 2
    assert report["status"] == "BLOCKED"
    assert report["errors"][0]["reason"] == "dry_run_cannot_write_output"
    assert not output.exists()


def test_action_plan_rejects_log_file_without_creating_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reject a log file in offline plan mode before creating it.

    :param tmp_path: Scratch log destination that must remain absent.
    :param capsys: Pytest fixture capturing the blocked report.

    :returns: None; assertions in the body validate the regression.
    """
    output = tmp_path / "audit.jsonl"
    result = ISSUE.main(
        [
            "open-bug",
            "--project",
            "unit/repo",
            "--title",
            "planned",
            "--log-file",
            str(output),
            "--log-format",
            "json",
            "--json",
        ],
    )
    report = json.loads(capsys.readouterr().out)
    assert result == 2
    assert report["status"] == "BLOCKED"
    assert report["errors"][0]["reason"] == "dry_run_cannot_write_output"
    assert not output.exists()


@pytest.mark.parametrize(
    ("arguments", "reason"),
    (
        ([], "action_required"),
        (["open-bug", "--apply", "--dry-run"], "apply_and_dry_run_conflict"),
        (["open-bug", "--confirm-plan", "unit"], "confirm_plan_requires_apply"),
        (["open-bug", "--receipt-out", "unit.json"], "receipt_out_requires_live_run"),
        (["open-bug", "--timeout", "301"], "timeout_out_of_range"),
    ),
)
def test_issue_mode_errors_remain_stable_after_shared_validation(
    arguments: Sequence[str], reason: str
) -> None:
    """Preserve existing GitLab failure reasons through the shared validator.

    :param arguments: Issue arguments selecting one invalid mode combination.
    :param reason: Stable classified error expected by existing consumers.
    :returns: None; the exact error reason is asserted.
    """
    args = ISSUE.build_parser().parse_args(arguments)

    with pytest.raises(ACTION.ActionError, match=f"^{reason}$"):
        GITLAB_ACTION._workflow_mode(args)


@pytest.mark.parametrize(
    ("arguments", "reason"),
    (
        (["get", "--runner-id", "1", "--apply"], "runner_read_rejects_apply_options"),
        (["list", "--live-plan"], "runner_read_rejects_apply_options"),
        (["tag", "--live-plan", "--dry-run"], "live_plan_mode_conflict"),
    ),
)
def test_runner_mode_errors_remain_stable_after_shared_validation(
    arguments: Sequence[str],
    reason: str,
) -> None:
    """Keep runner-specific precedence and shared planning conflict behavior.

    :param arguments: Runner arguments selecting one invalid mode combination.
    :param reason: Stable classified error expected by existing consumers.
    :returns: None; runner-specific precedence and shared errors are asserted.
    """
    args = RUNNER.build_parser().parse_args(arguments)

    with pytest.raises(
        ACTION.ActionError,
        match=f"^{reason}$",
    ):
        GITLAB_ACTION._workflow_mode(args)


def test_action_plan_json_diagnostic_is_separate_from_machine_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep JSON diagnostics on stderr separate from plan JSON on stdout.

    :param monkeypatch: Pytest fixture selecting the target by environment.
    :param tmp_path: Directory receiving the selected project target.
    :param capsys: Pytest fixture capturing both output streams.

    :returns: None; assertions in the body validate the regression.
    """
    target = tmp_path / "target.toml"
    target.write_text(
        '[gitlab]\nurl = "https://gitlab.example.test"\nproject = "unit/repo"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CI_SKILLS_TARGET", str(target))
    result = ISSUE.main(
        [
            "open-bug",
            "--title",
            "planned",
            "--log-format",
            "json",
            "--run-id",
            "unit-run",
            "--json",
        ],
    )
    emitted = capsys.readouterr()
    report = json.loads(emitted.out)
    diagnostic = json.loads(emitted.err)
    assert result == 0
    assert report["status"] == "DRY_RUN"
    assert diagnostic["run_id"] == "unit-run"
    assert diagnostic["mode"] == "dry_run"
    assert diagnostic["result"] == "DRY_RUN"
