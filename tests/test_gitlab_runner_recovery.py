"""Runner plans and recovery use mocked GitLab responses only."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import import_script_module

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
            ("GET", "runners/7"): [{"id": 7}],
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
            ("GET", "runners/7"): [{"id": 7}],
            (
                "GET",
                _list("groups/42/projects?include_subgroups=true&with_shared=false"),
            ): [[{"id": 12}, {"id": 11}]],
            ("GET", _list("projects/11/runners")): [[{"id": 7}], [{"id": 7}]],
            ("GET", _list("projects/12/runners")): [[{"id": 7}], [{"id": 7}]],
        }
    )
    result = RUNNERS.apply(api, object(), _plan(project_ids=(11, 12)), 42)
    assert result["action"] == "NO_OP"
    assert result["verified"] is True
    assert result["cleanup"]["status"] == "PASS"
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
        ACTION, "resolve_target", lambda _path: (tmp_path / "target.toml", "test")
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
    assert result["token_saved"] is False
    assert not token_out.exists()
    assert any(method == "DELETE" for method, _, _ in api.calls)


def test_membership_snapshot_uses_bounded_parallel_reads():
    state = {"active": 0, "peak": 0}
    lock = threading.Lock()

    class ConcurrentAPI:
        def get_json(self, session, endpoint):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.01)
            with lock:
                state["active"] -= 1
            return []

    observed = RUNNERS._membership_snapshot(
        ConcurrentAPI(), object(), 7, tuple(range(1, 9))
    )
    assert observed == dict.fromkeys(range(1, 9), False)
    assert 1 < state["peak"] <= RUNNERS.MAX_MEMBERSHIP_WORKERS
