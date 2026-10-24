"""Offline create serialization and uncertain-write read-back contracts.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from lib.python.core.gitlab.gitlab_actions import ActionPlan
    from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
    from lib.python.core.json_types import JSONObject, JSONValue

import pytest
from conftest import import_script_module
from test_gitlab_actions import _bound_session

API = import_script_module("lib.python.core.gitlab.gitlab_api")
ACTION = import_script_module("lib.python.core.gitlab.gitlab_actions")
ISSUES = import_script_module("lib.python.core.gitlab.gitlab_issues")
MILESTONES = import_script_module("lib.python.core.gitlab.gitlab_milestones")
WIKIS = import_script_module("lib.python.core.gitlab.gitlab_wikis")


class ScriptedAPI:
    """Replay bounded GitLab create responses and record competing requests."""

    def __init__(
        self,
        responses: Mapping[tuple[str, str], Sequence[Union[JSONValue, BaseException]]],
    ):
        """Copy ordered responses into a consumable endpoint script.

        :param responses: Mapping from method/endpoint to successive results or errors.
        """
        self.responses = {key: list(values) for key, values in responses.items()}
        self.calls = []

    def _next(
        self, method: str, endpoint: str, body: Optional[JSONValue] = None
    ) -> JSONValue:
        """Consume and record the next response for one API request.

        :param method: HTTP verb used as a script lookup key.
        :param endpoint: Relative GitLab API path used as a lookup key.
        :param body: Write body accepted for POST or PUT calls.
        :returns: Next scripted provider response.
        :raises AssertionError: The request is not present in the fixture script.
        :raises BaseException: Scripted provider failure supplied by the test.
        """  # noqa: DOC503 - The dynamic fixture exception is raised verbatim.
        self.calls.append((method, endpoint))
        values = self.responses.get((method, endpoint))
        if not values:
            raise AssertionError(f"unexpected {method} {endpoint}")
        value = values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def get_json(self, session: BoundGitLabSession, endpoint: str) -> JSONValue:
        """Consume the scripted GET response for a selected endpoint.

        :param session: Bound session accepted by the API double.
        :param endpoint: Relative GET path.
        :returns: Next scripted GET result.
        """
        return self._next("GET", endpoint)

    def post_json(
        self, session: BoundGitLabSession, endpoint: str, body: JSONValue
    ) -> JSONValue:
        """Consume the scripted POST response for a selected endpoint.

        :param session: Bound session accepted by the API double.
        :param endpoint: Relative create path.
        :param body: Caller-supplied create payload accepted by the double.
        :returns: Next scripted POST result.
        """
        return self._next("POST", endpoint, body)

    def put_json(
        self, session: BoundGitLabSession, endpoint: str, body: JSONValue
    ) -> JSONValue:
        """Consume the scripted PUT response for a selected endpoint.

        :param session: Bound session accepted by the API double.
        :param endpoint: Relative update path.
        :param body: Caller-supplied update payload accepted by the double.
        :returns: Next scripted PUT result.
        """
        return self._next("PUT", endpoint, body)


def _plan(kind: str, title: str, *, content: Optional[str] = None) -> ActionPlan:
    """Build a project-scoped issue, milestone, or wiki create plan.

    :param kind: GitLab resource kind selecting the create operation.
    :param title: Resource title recorded in the plan body.
    :param content: Wiki content recorded when supplied.
    :returns: Exact-target action plan used by uncertain-write tests.
    """
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
        target_source="env:CI_SKILLS_TARGET;target:gitlab.project",
        body=body,
        resource_id=None,
        token_out=None,
        revision="a" * 40,
    )


def test_same_host_lock_excludes_a_second_invocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Serialize concurrent creates for the same host and resource key.

    :param monkeypatch: Pytest fixture pointing lock storage at scratch space.
    :param tmp_path: Private directory holding the create lock.

    :returns: None; assertions in the body validate the regression.
    """
    tmp_path.chmod(0o700)
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path)
    started = threading.Event()
    acquired = threading.Event()

    def second_invocation() -> None:
        """Attempt the same create key from a second thread and mark acquisition.
        :returns: None; the helper records behavior through its side effects.
        """
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module: ModuleType,
    kind: str,
    title: str,
    content: Optional[str],
    base: str,
    query: str,
    listed: JSONObject,
    detail: JSONObject,
) -> None:
    """Hold uncertain create markers until a later matching read-back.

    :param monkeypatch: Pytest fixture selecting scratch lock storage.
    :param tmp_path: Private directory holding pending marker state.
    :param module: Issue, milestone, or wiki create implementation.
    :param kind: Resource kind used by the action plan.
    :param title: Requested resource title.
    :param content: Requested wiki body, when relevant.
    :param base: Create endpoint for the selected resource.
    :param query: Collection endpoint used for independent lookup.
    :param listed: Matching collection record returned on a later attempt.
    :param detail: Matching exact-resource read-back record.

    :returns: None; assertions in the body validate the regression.
    """
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
    session = _bound_session(plan)
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mark an issue create applied after timeout and matching read-back.

    :param monkeypatch: Pytest fixture selecting scratch lock storage.
    :param tmp_path: Private directory holding the create marker.

    :returns: None; assertions in the body validate the regression.
    """
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
    result = ISSUES.apply(
        api,
        _bound_session(_plan("gitlab_issue", "broken")),
        _plan("gitlab_issue", "broken"),
        42,
    )
    assert result["action"] == "APPLIED"
    assert result["reconciled"] is True
    assert [method for method, _ in api.calls].count("POST") == 1
    assert next(tmp_path.glob("*.lock")).read_text() == ""


@pytest.mark.parametrize(
    ("module", "kind", "title", "content", "base", "query", "created", "detail"),
    (
        (
            ISSUES,
            "gitlab_issue",
            "broken",
            None,
            "projects/42/issues",
            "projects/42/issues?state=all&search=broken&per_page=100&page=1",
            {"iid": 9},
            {"iid": 9, "title": "broken", "state": "opened"},
        ),
        (
            MILESTONES,
            "gitlab_milestone",
            "release",
            None,
            "projects/42/milestones",
            "projects/42/milestones?title=release&per_page=100&page=1",
            {"id": 9},
            {"id": 9, "title": "release"},
        ),
        (
            WIKIS,
            "gitlab_wiki",
            "Guide",
            "body",
            "projects/42/wikis",
            "projects/42/wikis?per_page=100&page=1",
            {"slug": "Guide"},
            {"slug": "Guide", "title": "Guide", "content": "body"},
        ),
    ),
)
def test_absent_uncertain_create_clears_marker_but_never_reposts_in_same_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module: ModuleType,
    kind: str,
    title: str,
    content: Optional[str],
    base: str,
    query: str,
    created: JSONObject,
    detail: JSONObject,
) -> None:
    """Require a separate invocation before retrying an absent uncertain create.

    :param monkeypatch: Pytest fixture selecting scratch lock storage.
    :param tmp_path: Private directory holding pending marker state.
    :param module: Issue, milestone, or wiki create implementation.
    :param kind: Resource kind used by the action plan.
    :param title: Requested resource title.
    :param content: Requested wiki body, when relevant.
    :param base: Create endpoint for the selected resource.
    :param query: Collection endpoint used for reconciliation.
    :param created: Resource identity returned by the later successful POST.
    :param detail: Exact-resource read-back after that later POST.

    :returns: None; assertions in the body validate the regression.
    """
    tmp_path.chmod(0o700)
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path)
    resource = f"{base}/{created.get('iid', created.get('id', created.get('slug')))}"
    api = ScriptedAPI(
        {
            ("GET", query): [[], [], [], []],
            ("POST", base): [API.GitLabAPIError("timeout"), created],
            ("GET", resource): [detail],
        }
    )
    plan = _plan(kind, title, content=content)
    session = _bound_session(plan)

    with pytest.raises(ACTION.ActionError, match="outcome_uncertain"):
        module.apply(api, session, plan, 42)
    lock_file = next(tmp_path.glob("*.lock"))
    assert lock_file.read_text() == "pending\n"

    with pytest.raises(API.GitLabAPIError, match="absent_after_readback_retry"):
        module.apply(api, session, plan, 42)
    assert lock_file.read_text() == ""
    assert [method for method, _ in api.calls].count("POST") == 1

    assert module.apply(api, session, plan, 42)["action"] == "APPLIED"
    assert [method for method, _ in api.calls].count("POST") == 2


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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: str,
    module: ModuleType,
    kind: str,
    title: str,
    content: Optional[str],
    base: str,
    query: str,
) -> None:
    """Keep 401/403 create refusals terminal without reconciliation GETs.

    :param monkeypatch: Pytest fixture selecting scratch lock storage.
    :param tmp_path: Private directory holding the create marker.
    :param reason: Authentication or authorization failure class.
    :param module: Issue, milestone, or wiki create implementation.
    :param kind: Resource kind used by the action plan.
    :param title: Requested resource title.
    :param content: Requested wiki body, when relevant.
    :param base: Create endpoint refusing the POST.
    :param query: Initial collection lookup endpoint.

    :returns: None; assertions in the body validate the regression.
    """
    tmp_path.chmod(0o700)
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path)
    api = ScriptedAPI(
        {
            ("GET", query): [[]],
            ("POST", base): [API.GitLabAPIError(reason)],
        }
    )
    with pytest.raises(API.GitLabAPIError) as failure:
        module.apply(
            api,
            _bound_session(_plan(kind, title, content=content)),
            _plan(kind, title, content=content),
            42,
        )
    assert failure.value.reason == reason
    assert [method for method, _ in api.calls] == ["GET", "POST"]
    assert next(tmp_path.glob("*.lock")).read_text() == ""


@pytest.mark.parametrize(
    (
        "module",
        "kind",
        "title",
        "content",
        "base",
        "query",
        "created",
        "resource",
        "readback",
        "resource_key",
        "resource_value",
    ),
    (
        (
            ISSUES,
            "gitlab_issue",
            "broken",
            None,
            "projects/42/issues",
            "projects/42/issues?state=all&search=broken&per_page=100&page=1",
            {"iid": 9},
            "projects/42/issues/9",
            {"iid": 9, "title": "broken", "state": "closed"},
            "iid",
            9,
        ),
        (
            MILESTONES,
            "gitlab_milestone",
            "release",
            None,
            "projects/42/milestones",
            "projects/42/milestones?title=release&per_page=100&page=1",
            {"id": 9},
            "projects/42/milestones/9",
            {"id": 10, "title": "release"},
            "id",
            9,
        ),
        (
            WIKIS,
            "gitlab_wiki",
            "Guide",
            "body",
            "projects/42/wikis",
            "projects/42/wikis?per_page=100&page=1",
            {"slug": "Guide"},
            "projects/42/wikis/Guide",
            {"slug": "Other", "title": "Guide", "content": "body"},
            "slug",
            "Guide",
        ),
    ),
)
def test_successful_create_with_mismatched_readback_keeps_mutation_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module: ModuleType,
    kind: str,
    title: str,
    content: Optional[str],
    base: str,
    query: str,
    created: JSONObject,
    resource: str,
    readback: JSONObject,
    resource_key: str,
    resource_value: Union[int, str],
) -> None:
    """Keep an unverified mutation record when POST succeeds but read-back drifts.

    :param monkeypatch: Pytest fixture selecting scratch lock storage.
    :param tmp_path: Private directory holding the create marker.
    :param module: Issue, milestone, or wiki create implementation.
    :param kind: Resource kind used by the action plan and error reason.
    :param title: Requested resource title.
    :param content: Requested wiki body, when relevant.
    :param base: Create endpoint for the selected resource.
    :param query: Initial collection lookup endpoint.
    :param created: Identity returned by the successful POST.
    :param resource: Exact-resource endpoint read after creation.
    :param readback: Stale or mismatched detail record.
    :param resource_key: Identity field retained in the mutation result.
    :param resource_value: Expected value of that retained identity.

    :returns: None; assertions in the body validate the regression.
    """
    tmp_path.chmod(0o700)
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path)
    api = ScriptedAPI(
        {
            ("GET", query): [[]],
            ("POST", base): [created],
            ("GET", resource): [readback],
        }
    )
    record = module.apply(
        api,
        _bound_session(_plan(kind, title, content=content)),
        _plan(kind, title, content=content),
        42,
    )
    assert record["action"] == "APPLIED"
    assert record["verified"] is False
    assert record["mutated"] is True
    assert record["uncertain"] is True
    assert record[resource_key] == resource_value
    assert record["errors"] == [
        {
            "project_id": 42,
            "reason": f"{kind.removeprefix('gitlab_')}_readback_mismatch",
        }
    ]
    assert record["cleanup"] == {
        "status": "NOT_PERFORMED",
        "reason": "post_write_readback_unverified",
    }
    assert [method for method, _ in api.calls].count("POST") == 1


def test_milestone_put_timeout_reconciles_by_id_without_reposting() -> None:
    """Reconcile a timed-out milestone update by exact ID without replaying PUT.
    :returns: None; assertions in the body validate the regression.
    """
    endpoint = "projects/42/milestones/9"
    plan = ACTION.ActionPlan(
        kind="gitlab_milestone",
        operation="adjust-time",
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/repo",
        target_file="/selected/target.toml",
        target_source="env:CI_SKILLS_TARGET;target:gitlab.project",
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
    result = MILESTONES.apply(api, _bound_session(plan), plan, 42)
    assert result["action"] == "APPLIED"
    assert result["reconciled"] is True
    assert [method for method, _ in api.calls].count("PUT") == 1


def test_wiki_rename_put_timeout_reconciles_by_new_title_without_reposting() -> None:
    """Reconcile a timed-out wiki rename under its new title without replay.
    :returns: None; assertions in the body validate the regression.
    """
    old = "projects/42/wikis/Old"
    base = "projects/42/wikis"
    plan = ACTION.ActionPlan(
        kind="gitlab_wiki",
        operation="update",
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/repo",
        target_file="/selected/target.toml",
        target_source="env:CI_SKILLS_TARGET;target:gitlab.project",
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
    result = WIKIS.apply(api, _bound_session(plan), plan, 42)
    assert result["action"] == "APPLIED"
    assert result["reconciled"] is True
    assert [method for method, _ in api.calls].count("PUT") == 1


def test_successful_milestone_put_with_stale_readback_returns_partial_record() -> None:
    """Retain milestone write evidence when the due date read-back is stale.
    :returns: None; assertions in the body validate the regression.
    """
    endpoint = "projects/42/milestones/9"
    plan = ACTION.ActionPlan(
        kind="gitlab_milestone",
        operation="adjust-time",
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/repo",
        target_file="/selected/target.toml",
        target_source="env:CI_SKILLS_TARGET;target:gitlab.project",
        body={"due_date": "2026-10-31"},
        resource_id=9,
        token_out=None,
        revision="a" * 40,
    )
    api = ScriptedAPI(
        {
            ("GET", endpoint): [
                {"id": 9, "title": "release", "due_date": "2026-10-01"},
                {"id": 9, "title": "release", "due_date": "2026-10-01"},
            ],
            ("PUT", endpoint): [{"id": 9}],
        }
    )
    record = MILESTONES.apply(api, _bound_session(plan), plan, 42)
    assert record["verified"] is False
    assert record["mutated"] is True
    assert record["id"] == 9
    assert record["errors"] == [
        {"project_id": 42, "reason": "milestone_readback_mismatch"}
    ]
    assert [method for method, _ in api.calls].count("PUT") == 1


def test_successful_wiki_put_with_stale_readback_returns_partial_record() -> None:
    """Retain wiki write evidence when the content read-back is stale.
    :returns: None; assertions in the body validate the regression.
    """
    endpoint = "projects/42/wikis/Guide"
    plan = ACTION.ActionPlan(
        kind="gitlab_wiki",
        operation="update",
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/repo",
        target_file="/selected/target.toml",
        target_source="env:CI_SKILLS_TARGET;target:gitlab.project",
        body={"content": "new"},
        resource_id="Guide",
        token_out=None,
        revision="a" * 40,
    )
    api = ScriptedAPI(
        {
            ("GET", endpoint): [
                {"slug": "Guide", "content": "old"},
                {"slug": "Guide", "content": "old"},
            ],
            ("PUT", endpoint): [{"slug": "Guide"}],
        }
    )
    record = WIKIS.apply(api, _bound_session(plan), plan, 42)
    assert record["verified"] is False
    assert record["mutated"] is True
    assert record["slug"] == "Guide"
    assert record["errors"] == [{"project_id": 42, "reason": "wiki_readback_mismatch"}]
    assert [method for method, _ in api.calls].count("PUT") == 1
