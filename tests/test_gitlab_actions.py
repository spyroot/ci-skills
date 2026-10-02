"""Offline operation contracts; no GitLab writes or cluster access."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import import_script_module

ACTION = import_script_module("core.gitlab_actions")
PORTABLE = import_script_module("core.portable")
API = import_script_module("core.gitlab_api")
MILESTONES = import_script_module("core.gitlab_milestones")
ISSUES = import_script_module("core.gitlab_issues")
WIKIS = import_script_module("core.gitlab_wikis")
RUNNERS = import_script_module("core.gitlab_runners")


def _plan(
    kind,
    operation,
    body,
    *,
    target_kind="project",
    resource_id=None,
    token_out=None,
    project_ids=None,
):
    return ACTION.ActionPlan(
        kind=kind,
        operation=operation,
        origin="https://gitlab.example.test",
        target_kind=target_kind,
        target_reference="team/repo" if target_kind == "project" else "team",
        target_file="/selected/target.toml",
        target_source="argv:--target;target:gitlab.project",
        body=body,
        resource_id=resource_id,
        token_out=str(token_out) if token_out else None,
        project_ids=project_ids,
        revision="a" * 40,
    )


class FakeAPI:
    def __init__(self, responses):
        self.responses = {key: list(value) for key, value in responses.items()}
        self.calls = []

    def _next(self, method, endpoint, body=None):
        self.calls.append((method, endpoint, body))
        values = self.responses[(method, endpoint)]
        if not values:
            raise AssertionError(f"unexpected repeated API call: {method} {endpoint}")
        value = values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def get_json(self, session, endpoint):
        return self._next("GET", endpoint)

    def post_json(self, session, endpoint, body):
        return self._next("POST", endpoint, body)

    def put_json(self, session, endpoint, body):
        return self._next("PUT", endpoint, body)

    def delete_json(self, session, endpoint):
        return self._next("DELETE", endpoint)


def _list(endpoint):
    return endpoint + ("&" if "?" in endpoint else "?") + "per_page=100&page=1"


def test_plan_fingerprint_binds_body_target_and_source():
    first = _plan("gitlab_issue", "open-bug", {"title": "one"})
    assert first.digest == _plan("gitlab_issue", "open-bug", {"title": "one"}).digest
    assert first.digest != _plan("gitlab_issue", "open-bug", {"title": "two"}).digest
    assert (
        first.digest
        != _plan(
            "gitlab_issue", "open-bug", {"title": "one"}, target_kind="group"
        ).digest
    )
    assert "one" not in json.dumps(first.public())


def test_milestone_create_reads_back_numeric_id():
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
    result = MILESTONES.apply(api, object(), plan, 42)
    assert result == {
        "action": "APPLIED",
        "id": 5,
        "verified": True,
        "title": "release",
    }
    assert [call[0] for call in api.calls] == ["GET", "POST", "GET"]


def test_milestone_update_blocks_failed_readback():
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
    result = MILESTONES.apply(api, object(), plan, 42)
    assert result["verified"] is False
    assert result["mutated"] is True
    assert result["id"] == 5
    assert result["errors"][0]["reason"] == "milestone_readback_mismatch"


def test_issue_reuses_exact_open_title_only_after_independent_get():
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
    result = ISSUES.apply(api, object(), plan, 42)
    assert result == {"action": "NO_OP", "iid": 8, "verified": True}
    assert all(method == "GET" for method, _, _ in api.calls)


def test_issue_create_refuses_closed_exact_title_instead_of_duplicating():
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
        ISSUES.apply(api, object(), plan, 42)
    assert [call[0] for call in api.calls] == ["GET"]


def test_issue_create_blocks_when_independent_readback_is_not_open():
    plan = _plan("gitlab_issue", "open-bug", {"title": "broken"})
    base = "projects/42/issues"
    api = FakeAPI(
        {
            ("GET", _list(base + "?state=all&search=broken")): [[]],
            ("POST", base): [{"iid": 9}],
            ("GET", base + "/9"): [{"iid": 9, "title": "broken", "state": "closed"}],
        }
    )
    result = ISSUES.apply(api, object(), plan, 42)
    assert result["verified"] is False
    assert result["mutated"] is True
    assert result["iid"] == 9
    assert result["errors"][0]["reason"] == "issue_readback_mismatch"
    assert [call[0] for call in api.calls] == ["GET", "POST", "GET"]


def test_wiki_update_reads_back_new_slug():
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
    assert WIKIS.apply(api, object(), plan, 42)["slug"] == "new"


def test_runner_group_assign_verifies_each_project_and_marks_partial():
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
    result = RUNNERS.apply(api, object(), plan, 42)
    assert result["verified"] is False
    assert result["action"] == "PARTIAL"
    assert [item["project_id"] for item in result["projects"]] == [11]
    assert result["errors"][0]["project_id"] == 12
    assert result["cleanup"]["status"] == "PASS"
    assert result["projects"][0]["action"] == "ROLLED_BACK"
    assert result["mutated"] is False


def test_runner_create_saves_token_0600_without_reporting_it(
    tmp_path: Path, monkeypatch
):
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
            ("GET", _list(scope)): [[], [{"id": 23, "description": "unique-runner"}]],
            ("POST", "user/runners"): [{"id": 23, "token": "private-one-time-value"}],
            ("GET", "runners/23"): [{"id": 23, "description": "unique-runner"}],
        }
    )
    result = RUNNERS.apply(api, object(), plan, 42)
    assert result["action"] == "APPLIED"
    assert result["id"] == 23
    assert result["verified"] is True
    assert result["sink_persisted"] is True
    assert result["cleanup"]["status"] == "NOT_APPLICABLE"
    assert destination.read_text() == "private-one-time-value\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert "private-one-time-value" not in json.dumps(result)
    assert str(destination) not in json.dumps(result)


def test_runner_create_uncertain_provider_result_leaves_no_token_file(
    tmp_path: Path, monkeypatch
):
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
    result = RUNNERS.apply(api, object(), plan, 42)
    assert result["action"] == "PARTIAL"
    assert result["mutated"] is None
    assert result["cleanup"]["status"] == "BLOCKED"
    assert not destination.exists()
    assert "provider_5xx" not in str(api.calls)
    assert [call[0] for call in api.calls] == ["GET", "POST"]


def test_receipt_is_portable_and_has_no_token_out_path(tmp_path: Path):
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


def test_transport_pins_host_and_sends_body_through_private_file():
    captured = {}

    def command(argv, *, timeout, env):
        captured["argv"] = argv
        captured["env"] = env
        captured["body"] = json.loads(Path(argv[argv.index("--input") + 1]).read_text())
        assert (
            stat.S_IMODE(Path(argv[argv.index("--input") + 1]).stat().st_mode) == 0o600
        )
        return SimpleNamespace(returncode=0, stdout='{"id":23}', stderr="")

    session = SimpleNamespace(
        host="gitlab.example.test", environment={"GITLAB_TOKEN": "selected"}
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
    monkeypatch, tmp_path: Path, capsys
):
    plan = _plan("gitlab_issue", "open-bug", {"title": "broken"})
    api = FakeAPI(
        {
            (
                "GET",
                _list("projects/42/issues?state=all&search=broken"),
            ): [{}],
        }
    )
    session = SimpleNamespace(
        origin=plan.origin,
        target_reference=plan.target_reference,
    )
    monkeypatch.setattr(
        ACTION, "resolve_target", lambda _explicit: (tmp_path / "target.toml", "test")
    )
    monkeypatch.setattr(ACTION, "load_gitlab_target", lambda _path: object())
    monkeypatch.setattr(ACTION, "make_plan", lambda *_args: plan)
    monkeypatch.setattr(
        ACTION, "bind_gitlab_session", lambda *_args, **_kwargs: session
    )
    monkeypatch.setattr(ACTION, "GlabAPIClient", lambda *, timeout: api)
    monkeypatch.setattr(
        ACTION,
        "check_gitlab_operation_access",
        lambda _session, api_client: {
            "status": "PASS",
            "identity": {"username": "operator"},
            "target": {"kind": "project", "id": 42},
        },
    )
    result = ACTION.run_action_cli(
        "gitlab_issue",
        [
            "open-bug",
            "--title",
            "broken",
            "--target",
            str(tmp_path / "target.toml"),
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


@pytest.mark.parametrize("dry_run_flag", ([], ["--dry-run"]))
def test_action_plan_rejects_output_paths_without_creating_files(
    tmp_path: Path, capsys, dry_run_flag
):
    output = tmp_path / "reports"
    result = ACTION.run_action_cli(
        "gitlab_issue",
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


def test_action_plan_rejects_log_file_without_creating_it(tmp_path: Path, capsys):
    output = tmp_path / "audit.jsonl"
    result = ACTION.run_action_cli(
        "gitlab_issue",
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
    assert not output.exists()


def test_action_plan_json_diagnostic_is_separate_from_machine_result(
    tmp_path: Path, capsys
):
    target = tmp_path / "target.toml"
    target.write_text(
        '[gitlab]\nurl = "https://gitlab.example.test"\nproject = "unit/repo"\n',
        encoding="utf-8",
    )
    result = ACTION.run_action_cli(
        "gitlab_issue",
        [
            "open-bug",
            "--title",
            "planned",
            "--target",
            str(target),
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
