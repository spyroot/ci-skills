"""Offline create serialization and uncertain-write read-back contracts."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from conftest import import_script_module

API = import_script_module("core.gitlab_api")
ACTION = import_script_module("core.gitlab_actions")
ISSUES = import_script_module("core.gitlab_issues")
MILESTONES = import_script_module("core.gitlab_milestones")
WIKIS = import_script_module("core.gitlab_wikis")


class ScriptedAPI:
    def __init__(self, responses):
        self.responses = {key: list(values) for key, values in responses.items()}
        self.calls = []

    def _next(self, method, endpoint, body=None):
        self.calls.append((method, endpoint))
        values = self.responses.get((method, endpoint))
        if not values:
            raise AssertionError(f"unexpected {method} {endpoint}")
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


def _plan(kind, title, *, content=None):
    body = {"title": title}
    if content is not None:
        body["content"] = content
    return ACTION.ActionPlan(
        kind=kind,
        operation="open-bug" if kind == "gitlab_issue" else "create",
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/repo",
        target_file="/selected/target.toml",
        target_source="argv:--target;target:gitlab.project",
        body=body,
        resource_id=None,
        token_out=None,
        revision="a" * 40,
    )


def test_same_host_lock_excludes_a_second_invocation(monkeypatch, tmp_path):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path)
    started = threading.Event()
    acquired = threading.Event()

    def second_invocation():
        started.set()
        with API.create_guard(
            "https://gitlab.example.test", "project", 42, "issue", "broken"
        ):
            acquired.set()

    with API.create_guard(
        "https://gitlab.example.test", "project", 42, "issue", "broken"
    ):
        worker = threading.Thread(target=second_invocation)
        worker.start()
        assert started.wait(2)
        assert not acquired.wait(0.1)
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert acquired.is_set()


@pytest.mark.parametrize(
    ("module", "kind", "title", "content", "base", "query", "listed", "detail"),
    (
        (
            ISSUES,
            "gitlab_issue",
            "broken",
            None,
            "projects/42/issues",
            "projects/42/issues?state=all&search=broken&per_page=100&page=1",
            {"iid": 9, "title": "broken", "state": "opened"},
            {"iid": 9, "title": "broken", "state": "opened"},
        ),
        (
            MILESTONES,
            "gitlab_milestone",
            "release",
            None,
            "projects/42/milestones",
            "projects/42/milestones?title=release&per_page=100&page=1",
            {"id": 9, "title": "release"},
            {"id": 9, "title": "release"},
        ),
        (
            WIKIS,
            "gitlab_wiki",
            "Guide",
            "body",
            "projects/42/wikis",
            "projects/42/wikis?per_page=100&page=1",
            {"slug": "Guide", "title": "Guide"},
            {"slug": "Guide", "title": "Guide", "content": "body"},
        ),
    ),
)
def test_timed_out_create_blocks_replay_until_independent_readback(
    monkeypatch, tmp_path, module, kind, title, content, base, query, listed, detail
):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path)
    plan = _plan(kind, title, content=content)
    resource = (
        f"{base}/{listed['slug']}"
        if kind == "gitlab_wiki"
        else f"{base}/{listed.get('iid', listed.get('id'))}"
    )
    api = ScriptedAPI(
        {
            ("GET", query): [[], [], [], [listed]],
            ("POST", base): [API.GitLabAPIError("timeout")],
            ("GET", resource): [detail],
        }
    )
    session = SimpleNamespace()
    with pytest.raises(ACTION.ActionError, match="outcome_uncertain"):
        module.apply(api, session, plan, 42)
    lock_file = next(tmp_path.glob("*.lock"))
    assert lock_file.read_text() == "pending\n"
    with pytest.raises(API.GitLabAPIError, match="outcome_uncertain"):
        module.apply(api, session, plan, 42)
    assert [method for method, _ in api.calls].count("POST") == 1
    assert module.apply(api, session, plan, 42)["action"] == "NO_OP"
    assert lock_file.read_text() == ""


def test_create_reconciles_a_lost_post_response_without_reposting(
    monkeypatch, tmp_path
):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path)
    base = "projects/42/issues"
    query = base + "?state=all&search=broken&per_page=100&page=1"
    api = ScriptedAPI(
        {
            ("GET", query): [[], [{"iid": 9, "title": "broken", "state": "opened"}]],
            ("POST", base): [API.GitLabAPIError("timeout")],
            ("GET", base + "/9"): [{"iid": 9, "title": "broken", "state": "opened"}],
        }
    )
    result = ISSUES.apply(api, SimpleNamespace(), _plan("gitlab_issue", "broken"), 42)
    assert result["action"] == "APPLIED"
    assert result["reconciled"] is True
    assert [method for method, _ in api.calls].count("POST") == 1
    assert next(tmp_path.glob("*.lock")).read_text() == ""


@pytest.mark.parametrize("reason", ("authentication", "authorization"))
@pytest.mark.parametrize(
    ("module", "kind", "title", "content", "base", "query"),
    (
        (
            ISSUES,
            "gitlab_issue",
            "broken",
            None,
            "projects/42/issues",
            "projects/42/issues?state=all&search=broken&per_page=100&page=1",
        ),
        (
            MILESTONES,
            "gitlab_milestone",
            "release",
            None,
            "projects/42/milestones",
            "projects/42/milestones?title=release&per_page=100&page=1",
        ),
        (
            WIKIS,
            "gitlab_wiki",
            "Guide",
            "body",
            "projects/42/wikis",
            "projects/42/wikis?per_page=100&page=1",
        ),
    ),
)
def test_terminal_create_refusal_keeps_its_class_and_does_not_reconcile(
    monkeypatch, tmp_path, reason, module, kind, title, content, base, query
):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path)
    api = ScriptedAPI(
        {
            ("GET", query): [[]],
            ("POST", base): [API.GitLabAPIError(reason)],
        }
    )
    with pytest.raises(API.GitLabAPIError) as failure:
        module.apply(api, SimpleNamespace(), _plan(kind, title, content=content), 42)
    assert failure.value.reason == reason
    assert [method for method, _ in api.calls] == ["GET", "POST"]
    assert next(tmp_path.glob("*.lock")).read_text() == ""


def test_milestone_put_timeout_reconciles_by_id_without_reposting():
    endpoint = "projects/42/milestones/9"
    plan = ACTION.ActionPlan(
        kind="gitlab_milestone",
        operation="adjust-time",
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/repo",
        target_file="/selected/target.toml",
        target_source="argv:--target;target:gitlab.project",
        body={"due_date": "2026-10-31"},
        resource_id=9,
        token_out=None,
        revision="a" * 40,
    )
    api = ScriptedAPI(
        {
            ("GET", endpoint): [
                {"id": 9, "title": "release", "due_date": "2026-10-01"},
                {"id": 9, "title": "release", "due_date": "2026-10-31"},
            ],
            ("PUT", endpoint): [API.GitLabAPIError("timeout")],
        }
    )
    result = MILESTONES.apply(api, SimpleNamespace(), plan, 42)
    assert result["action"] == "APPLIED"
    assert result["reconciled"] is True
    assert [method for method, _ in api.calls].count("PUT") == 1


def test_wiki_rename_put_timeout_reconciles_by_new_title_without_reposting():
    old = "projects/42/wikis/Old"
    base = "projects/42/wikis"
    plan = ACTION.ActionPlan(
        kind="gitlab_wiki",
        operation="update",
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/repo",
        target_file="/selected/target.toml",
        target_source="argv:--target;target:gitlab.project",
        body={"title": "New", "content": "new body"},
        resource_id="Old",
        token_out=None,
        revision="a" * 40,
    )
    api = ScriptedAPI(
        {
            ("GET", old): [{"slug": "Old", "title": "Old", "content": "old body"}],
            ("PUT", old): [API.GitLabAPIError("timeout")],
            ("GET", base + "?per_page=100&page=1"): [[{"slug": "New", "title": "New"}]],
            ("GET", base + "/New"): [
                {"slug": "New", "title": "New", "content": "new body"}
            ],
        }
    )
    result = WIKIS.apply(api, SimpleNamespace(), plan, 42)
    assert result["action"] == "APPLIED"
    assert result["reconciled"] is True
    assert [method for method, _ in api.calls].count("PUT") == 1
