"""Offline operation contracts; no GitLab writes or cluster access."""

from __future__ import annotations

import json
import stat
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from tests.python.conftest import import_script_module

ACTION = import_script_module("core.gitlab_actions")
PORTABLE = import_script_module("core.portable")
API = import_script_module("core.gitlab_api")
MILESTONES = import_script_module("core.gitlab_milestones")
ISSUES = import_script_module("core.gitlab_issues")
WIKIS = import_script_module("core.gitlab_wikis")
RUNNERS = import_script_module("core.gitlab_runners")
REPORT = import_script_module("core.report")

TAG_SCHEMA = json.loads(
    (
        Path(__file__).parents[2] / "schemas/results/gitlab-runner-tag.schema.json"
    ).read_text(encoding="utf-8")
)
TAG_VALIDATOR = Draft202012Validator(TAG_SCHEMA)


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


def _tag_output(plan, record):
    """Render one real runner-action record through the shared result adapter."""
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
        identity={"id": 1, "username": "test"},
        verified_target={"id": 42, "kind": "project"},
        credential_source="unit",
        credential_digest="sha256:unit",
        skill={},
    )
    return json.loads(REPORT.emit(data, "json"))


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
    assert '"one"' not in json.dumps(first.public())


def test_action_uses_project_target_before_user_target_without_selector(
    tmp_path: Path, monkeypatch, capsys
):
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

    result = ACTION.run_action_cli(
        "gitlab_issue", ["open-bug", "--title", "selected", "--json"]
    )
    project_plan = json.loads(capsys.readouterr().out)
    assert result == 0
    assert project_plan["target"]["reference"] == "project/repo"
    assert project_plan["target_source"] == "project;target:gitlab.project"

    project_target.unlink()
    result = ACTION.run_action_cli(
        "gitlab_issue", ["open-bug", "--title", "selected", "--json"]
    )
    user_plan = json.loads(capsys.readouterr().out)
    assert result == 0
    assert user_plan["target"]["reference"] == "user/repo"
    assert user_plan["target_source"] == "user;target:gitlab.project"
    assert user_plan["plan_digest"] != project_plan["plan_digest"]


def test_group_action_uses_selected_target_without_group_flag(
    tmp_path: Path, monkeypatch, capsys
):
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

    result = ACTION.run_action_cli(
        "gitlab_runner", ["assign", "--runner-id", "9", "--json"]
    )
    plan = json.loads(capsys.readouterr().out)

    assert result == 0
    assert plan["target"] == {"kind": "group", "reference": "platform/team"}
    assert plan["target_source"] == "user;target:gitlab.group"


def test_declared_project_override_replaces_selected_target_for_one_action(
    tmp_path: Path, capsys
):
    target = tmp_path / "selected.toml"
    target.write_text(
        '[gitlab]\nurl = "https://gitlab.example.test"\nproject = "stored/repo"\n',
        encoding="utf-8",
    )

    result = ACTION.run_action_cli(
        "gitlab_issue",
        [
            "open-bug",
            "--title",
            "selected",
            "--target",
            str(target),
            "--project",
            "override/repo",
            "--json",
        ],
    )
    plan = json.loads(capsys.readouterr().out)

    assert result == 0
    assert plan["target"]["reference"] == "override/repo"
    assert plan["target_source"] == "argv:--target;argv:--project"


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


def test_wiki_create_accepts_provider_normalized_title_and_reuses_page(
    tmp_path: Path, monkeypatch
):
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
    first = WIKIS.apply(api, object(), plan, 42)
    second = WIKIS.apply(api, object(), plan, 42)
    assert first["action"] == "APPLIED" and first["verified"] is True
    assert second["action"] == "NO_OP" and second["verified"] is True
    assert [call[0] for call in api.calls].count("POST") == 1


def test_wiki_update_accepts_provider_normalized_title():
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
    result = WIKIS.apply(api, object(), plan, 42)
    assert result["action"] == "APPLIED" and result["verified"] is True


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
    assert result["after_tags"] == ["first", "second"]


def test_runner_tag_adds_to_selected_runner_and_reads_back():
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
    result = RUNNERS.apply(api, object(), plan, 42)
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
    TAG_VALIDATOR.validate(_tag_output(plan, result))


def test_runner_tag_is_no_op_when_tag_already_exists():
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
    result = RUNNERS.apply(api, object(), plan, 42)
    assert result["action"] == "NO_OP"
    assert result["mutated"] is False
    assert all(call[0] != "PUT" for call in api.calls)
    TAG_VALIDATOR.validate(_tag_output(plan, result))


def test_runner_tag_refuses_runner_outside_selected_project():
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
        RUNNERS.apply(api, object(), plan, 42)
    assert all(call[0] != "PUT" for call in api.calls)


def test_runner_tag_partial_output_requires_readback_and_cleanup():
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
    result = RUNNERS.apply(api, object(), plan, 42)
    assert result["verified"] is False
    assert result["errors"][0]["reason"] == "runner_tag_readback_mismatch"
    output = _tag_output(plan, result)
    TAG_VALIDATOR.validate(output)
    for field in ("readback", "mutated", "cleanup"):
        invalid = deepcopy(output)
        del invalid[field]
        assert not TAG_VALIDATOR.is_valid(invalid)


def test_concurrent_runner_tag_additions_preserve_both_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    first_write_started = Event()
    release_first_write = Event()
    second_runner_read = Event()

    class SharedRunnerAPI:
        def __init__(self):
            self.tags = ["existing"]
            self.read_count = 0
            self.write_count = 0
            self.lock = Lock()

        def get_json(self, _session, endpoint):
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

        def put_json(self, _session, _endpoint, body):
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
        alpha = pool.submit(RUNNERS.apply, api, object(), first, 42)
        assert first_write_started.wait(2)
        beta = pool.submit(RUNNERS.apply, api, object(), second, 42)
        if second_runner_read.wait(0.1):
            beta.result(timeout=2)
        release_first_write.set()
        results = [alpha.result(timeout=2), beta.result(timeout=2)]
    assert all(result["verified"] for result in results)
    assert set(api.tags) == {"existing", "alpha", "beta"}


def test_runner_tag_dry_run_and_refusal_match_schema(tmp_path, capsys):
    target = tmp_path / "target.toml"
    target.write_text(
        '[gitlab]\nurl = "https://gitlab.example.test"\nproject = "team/repo"\n',
        encoding="utf-8",
    )
    common = ["tag", "--target", str(target), "--runner-id", "23", "--json"]
    assert ACTION.run_action_cli("gitlab_runner", [*common, "--tag", "new"]) == 0
    dry_run = json.loads(capsys.readouterr().out)
    TAG_VALIDATOR.validate(dry_run)
    assert dry_run["status"] == "DRY_RUN"

    assert ACTION.run_action_cli("gitlab_runner", common) != 0
    blocked = json.loads(capsys.readouterr().out)
    TAG_VALIDATOR.validate(blocked)
    assert blocked["errors"][0]["reason"] == "tag_requires_tag"


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
        ACTION,
        "resolve_gitlab_target",
        lambda _target, _binding, *, dry_run: (object(), "test"),
    )
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


@pytest.mark.parametrize(
    ("subcheck", "reason"),
    (("user", "authentication"), ("target", "target_path_mismatch")),
)
def test_action_access_failure_preserves_the_failed_subcheck(
    monkeypatch, tmp_path: Path, capsys, subcheck, reason
):
    plan = _plan("gitlab_issue", "open-bug", {"title": "broken"})
    monkeypatch.setattr(
        ACTION,
        "resolve_gitlab_target",
        lambda _target, _binding, *, dry_run: (object(), "test"),
    )
    monkeypatch.setattr(ACTION, "make_plan", lambda *_args: plan)
    monkeypatch.setattr(
        ACTION, "bind_gitlab_session", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(ACTION, "GlabAPIClient", lambda *, timeout: object())
    monkeypatch.setattr(
        ACTION,
        "check_gitlab_operation_access",
        lambda _session, api_client: {
            "status": "BLOCKED",
            "errors": [{"source": subcheck, "reason": reason}],
        },
    )
    monkeypatch.setattr(
        ACTION,
        "apply_plan",
        lambda *_args: pytest.fail("apply ran after access failure"),
    )

    result = ACTION.run_action_cli(
        "gitlab_issue",
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
    monkeypatch, tmp_path: Path, capsys
):
    plan = _plan("gitlab_issue", "open-bug", {"title": "broken"})
    receipt_out = tmp_path / "blocked-receipt.json"
    session = SimpleNamespace(
        origin=plan.origin,
        target_reference=plan.target_reference,
        credential_source="env:GITLAB_TOKEN",
        credential_digest="sha256:unit",
        skill={"digest": "unit"},
    )
    access = {
        "status": "PASS",
        "identity": {"username": "operator"},
        "target": {"kind": "project", "id": 42, "full_path": "team/repo"},
    }
    monkeypatch.setattr(
        ACTION,
        "resolve_gitlab_target",
        lambda _target, _binding, *, dry_run: (object(), "test"),
    )
    monkeypatch.setattr(ACTION, "make_plan", lambda *_args: plan)
    monkeypatch.setattr(
        ACTION, "bind_gitlab_session", lambda *_args, **_kwargs: session
    )
    monkeypatch.setattr(ACTION, "GlabAPIClient", lambda *, timeout: object())
    monkeypatch.setattr(
        ACTION,
        "check_gitlab_operation_access",
        lambda _session, api_client: access,
    )

    def fail_after_write(*_args):
        raise ACTION.ActionError(
            "issue_create_outcome_uncertain_check_title_before_retry"
        )

    monkeypatch.setattr(ACTION, "apply_plan", fail_after_write)

    result = ACTION.run_action_cli(
        "gitlab_issue",
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
