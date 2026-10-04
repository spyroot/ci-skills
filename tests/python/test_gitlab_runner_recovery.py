"""Runner plans and recovery use mocked GitLab responses only."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.python.conftest import import_script_module

ACTION = import_script_module("core.gitlab_actions")
API = import_script_module("core.gitlab_api")
RUNNERS = import_script_module("core.gitlab_runners")


def _list(endpoint):
    return endpoint + ("&" if "?" in endpoint else "?") + "per_page=100&page=1"


def _plan(*, project_ids=None, operation="assign", token_out=None):
    return ACTION.ActionPlan(
        kind="gitlab_runner",
        operation=operation,
        origin="https://gitlab.example.test",
        target_kind="group",
        target_reference="unit/team",
        target_file="/selected/target.toml",
        target_source="argv:--group",
        body={"runner_type": "group_type", "description": "fresh-runner"}
        if operation == "create"
        else {},
        resource_id=7 if operation == "assign" else None,
        token_out=str(token_out) if token_out else None,
        project_ids=project_ids,
        revision="a" * 40,
    )


class FakeAPI:
    def __init__(self, responses):
        self.responses = {key: list(values) for key, values in responses.items()}
        self.calls = []

    def _next(self, method, endpoint, body=None):
        self.calls.append((method, endpoint, body))
        value = self.responses[(method, endpoint)].pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def get_json(self, session, endpoint):
        return self._next("GET", endpoint)

    def post_json(self, session, endpoint, body):
        return self._next("POST", endpoint, body)

    def delete_json(self, session, endpoint):
        return self._next("DELETE", endpoint)


@pytest.mark.parametrize("confirmed", (None, (11,)))
def test_group_apply_refuses_missing_or_changed_project_set_before_post(confirmed):
    api = FakeAPI(
        {
            ("GET", "runners/7"): [
                {"id": 7, "runner_type": "project_type", "projects": []}
            ],
            (
                "GET",
                _list("groups/42/projects?include_subgroups=true&with_shared=false"),
            ): [[{"id": 11}, {"id": 12}]],
        }
    )
    with pytest.raises(
        ACTION.ActionError,
        match="group_assignment_requires_live_plan|group_projects_changed",
    ):
        RUNNERS.apply(api, object(), _plan(project_ids=confirmed), 42)
    assert all(method == "GET" for method, _, _ in api.calls)


def test_group_repeat_returns_no_op_with_independent_membership_readback():
    api = FakeAPI(
        {
            ("GET", "runners/7"): [
                {
                    "id": 7,
                    "runner_type": "project_type",
                    "projects": [{"id": 11}, {"id": 12}],
                },
                {
                    "id": 7,
                    "runner_type": "project_type",
                    "projects": [{"id": 11}, {"id": 12}],
                },
                {
                    "id": 7,
                    "runner_type": "project_type",
                    "projects": [{"id": 11}, {"id": 12}],
                },
            ],
            (
                "GET",
                _list("groups/42/projects?include_subgroups=true&with_shared=false"),
            ): [[{"id": 12}, {"id": 11}]],
        }
    )
    result = RUNNERS.apply(api, object(), _plan(project_ids=(11, 12)), 42)
    assert result["action"] == "NO_OP"
    assert result["verified"] is True
    assert result["cleanup"]["status"] == "NOT_APPLICABLE"
    assert result["mutated"] is False
    assert all(method == "GET" for method, _, _ in api.calls)


def test_live_plan_cli_binds_group_ids_without_post(monkeypatch, tmp_path, capsys):
    plan = _plan()
    session = SimpleNamespace(
        origin=plan.origin,
        target_reference=plan.target_reference,
        credential_source="env:GITLAB_TOKEN",
        credential_digest="sha256:unit",
        skill={"digest": "unit"},
    )
    api = FakeAPI(
        {
            (
                "GET",
                _list("groups/42/projects?include_subgroups=true&with_shared=false"),
            ): [[{"id": 12}, {"id": 11}]],
        }
    )
    monkeypatch.setattr(
        ACTION,
        "resolve_gitlab_target",
        lambda *_args, **_kwargs: (object(), "test"),
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
            "identity": {"username": "unit"},
            "target": {"kind": "group", "id": 42},
        },
    )

    assert (
        ACTION.run_action_cli(
            "gitlab_runner",
            [
                "assign",
                "--group",
                "unit/team",
                "--runner-id",
                "7",
                "--live-plan",
                "--json",
            ],
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "PLANNED"
    assert report["phase"] == "LIVE_PLAN"
    assert report["mutated"] is False
    assert report["plan"]["project_ids"] == [11, 12]
    assert report["plan"]["confirmation_ready"] is True
    assert all(method == "GET" for method, _, _ in api.calls)


def test_runner_token_write_readback_failure_deletes_created_runner(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    token_out = tmp_path / "one-time.token"
    scope = "groups/42/runners"
    api = FakeAPI(
        {
            ("GET", _list(scope)): [[], []],
            ("POST", "user/runners"): [{"id": 23, "token": "secret"}],
            ("GET", "runners/23"): [{"id": 23, "description": "fresh-runner"}],
            ("DELETE", "runners/23"): [{}],
        }
    )
    original_read = Path.read_text

    def fail_token_read(path, *args, **kwargs):
        if path == token_out:
            raise OSError("injected readback failure")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_token_read)
    result = RUNNERS.apply(
        api, object(), _plan(operation="create", token_out=token_out), 42
    )
    assert result["action"] == "PARTIAL"
    assert result["verified"] is False
    assert result["cleanup"]["status"] == "PASS"
    assert result["sink_persisted"] is False
    assert not token_out.exists()
    assert any(method == "DELETE" for method, _, _ in api.calls)


def test_membership_snapshot_reads_direct_associations_once():
    api = FakeAPI(
        {
            ("GET", "runners/7"): [
                {"id": 7, "runner_type": "project_type", "projects": [{"id": 11}]}
            ]
        }
    )
    observed = RUNNERS._membership_snapshot(api, object(), 7, (11, 12))
    assert observed == {11: True, 12: False}
    assert api.calls == [("GET", "runners/7", None)]


def test_group_runner_is_not_accepted_for_project_assignment():
    api = FakeAPI(
        {("GET", "runners/7"): [{"id": 7, "runner_type": "group_type", "projects": []}]}
    )
    with pytest.raises(
        ACTION.ActionError, match="runner_assignment_requires_project_type"
    ):
        RUNNERS.apply(api, object(), _plan(project_ids=(11,)), 42)
    assert api.calls == [("GET", "runners/7", None)]


def test_runner_create_requires_token_sink_during_offline_plan():
    args = SimpleNamespace(
        action="create",
        description="fresh-runner",
        tag=[],
        runner_id=None,
        token_out=None,
        apply=False,
    )
    with pytest.raises(
        ACTION.ActionError, match="token_out_required_for_runner_create_plan"
    ):
        RUNNERS.prepare(args, object(), "group")


@pytest.mark.parametrize(
    "reason", ("authentication", "authorization", "command_failed")
)
def test_terminal_runner_post_failure_clears_pending_and_preserves_reason(
    reason, monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    token_out = tmp_path / "one-time.token"
    api = FakeAPI(
        {
            ("GET", _list("groups/42/runners")): [[]],
            ("POST", "user/runners"): [API.GitLabAPIError(reason)],
        }
    )
    with pytest.raises(API.GitLabAPIError, match=reason):
        RUNNERS.apply(api, object(), _plan(operation="create", token_out=token_out), 42)
    assert not token_out.exists()
    assert [path.read_bytes() for path in (tmp_path / "locks").glob("*.lock")] == [b""]


def test_malformed_runner_create_response_keeps_uncertain_recovery_marker(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    token_out = tmp_path / "one-time.token"
    api = FakeAPI(
        {
            ("GET", _list("groups/42/runners")): [[]],
            ("POST", "user/runners"): [{}],
        }
    )
    result = RUNNERS.apply(
        api, object(), _plan(operation="create", token_out=token_out), 42
    )
    assert result["action"] == "PARTIAL"
    assert result["mutated"] is None
    assert result["cleanup"]["status"] == "BLOCKED"
    assert not token_out.exists()
    assert [path.read_bytes() for path in (tmp_path / "locks").glob("*.lock")] == [
        b"pending\n"
    ]


def test_absent_runner_after_uncertain_post_requires_separate_retry(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    token_out = tmp_path / "one-time.token"
    scope = "groups/42/runners"
    api = FakeAPI(
        {
            ("GET", _list(scope)): [[], [], []],
            ("POST", "user/runners"): [
                API.GitLabAPIError("timeout"),
                API.GitLabAPIError("authentication"),
            ],
        }
    )
    plan = _plan(operation="create", token_out=token_out)
    first = RUNNERS.apply(api, object(), plan, 42)
    assert first["action"] == "PARTIAL"
    lock_file = next((tmp_path / "locks").glob("*.lock"))
    assert lock_file.read_text() == "pending\n"

    with pytest.raises(API.GitLabAPIError, match="absent_after_readback_retry"):
        RUNNERS.apply(api, object(), plan, 42)
    assert lock_file.read_text() == ""
    assert [method for method, _, _ in api.calls].count("POST") == 1

    with pytest.raises(API.GitLabAPIError, match="authentication"):
        RUNNERS.apply(api, object(), plan, 42)
    assert [method for method, _, _ in api.calls].count("POST") == 2
    assert not token_out.exists()


def test_existing_runner_error_names_manual_recovery_not_missing_command(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    token_out = tmp_path / "one-time.token"
    scope = "groups/42/runners"
    api = FakeAPI(
        {
            ("GET", _list(scope)): [[{"id": 23, "description": "fresh-runner"}]],
        }
    )
    with pytest.raises(
        ACTION.ActionError, match="verify_and_remove_record_before_retry"
    ):
        RUNNERS.apply(api, object(), _plan(operation="create", token_out=token_out), 42)
    assert not token_out.exists()
    assert all(method == "GET" for method, _, _ in api.calls)
