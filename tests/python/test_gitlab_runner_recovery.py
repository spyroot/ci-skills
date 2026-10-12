"""Runner plans and recovery use mocked GitLab responses only.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
    from lib.python.core.json_types import JSONValue

import pytest
from conftest import import_script_module

ACTION = import_script_module("lib.python.core.gitlab.gitlab_actions")
GITLAB_ACTION = import_script_module("lib.python.core.gitlab.gitlab_action")
GITLAB_SESSION = import_script_module("lib.python.core.gitlab.gitlab_session")
API = import_script_module("lib.python.core.gitlab.gitlab_api")
RUNNERS = import_script_module("lib.python.core.gitlab.gitlab_runners")
RUNNER = import_script_module("gitlab_runner")


def _list(endpoint: str) -> str:
    """Append the first bounded page query to a runner collection path.

    :param endpoint: Group or project runner endpoint.
    :returns: Endpoint requesting page one with 100 records.
    """
    return endpoint + ("&" if "?" in endpoint else "?") + "per_page=100&page=1"


def _plan(
    *,
    project_ids: Optional[Sequence[int]] = None,
    operation: str = "assign",
    token_out: Optional[str] = None,
) -> ACTION.ActionPlan:
    """Build a group runner assignment or create plan.

    :param project_ids: Confirmed group project IDs for assignment.
    :param operation: Runner assign or create verb.
    :param token_out: Private one-time token destination for create.
    :returns: Group-scoped runner action plan.
    """
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


def _bound_session(plan: ACTION.ActionPlan) -> BoundGitLabSession:
    """Create the concrete GitLab session used by CLI recovery tests.

    :param plan: Runner action plan whose target fields identify the session.
    :returns: Bound session carrying verified skill and credential provenance.
    """
    return GITLAB_SESSION.BoundGitLabSession(
        origin=plan.origin,
        host="gitlab.example.test",
        target_kind=plan.target_kind,
        target_reference=plan.target_reference,
        target_source=plan.target_source,
        target_file=Path(plan.target_file),
        credential_source="env:GITLAB_TOKEN",
        credential_digest="sha256:unit",
        execution_host="unit-host",
        skill={
            "digest": "unit",
            "revision": {"value": "a" * 40, "verified": True},
        },
        environment={},
    )


class FakeAPI:
    """Replay runner creation, deletion, and read-back for recovery tests."""

    def __init__(
        self,
        responses: Mapping[tuple[str, str], Sequence[Union[JSONValue, BaseException]]],
    ):
        """Copy scripted API response queues and start a call journal.

        :param responses: Method/endpoint mapping to ordered results or errors.
        """
        self.responses = {key: list(values) for key, values in responses.items()}
        self.calls = []

    def _next(
        self, method: str, endpoint: str, body: Optional[JSONValue] = None
    ) -> JSONValue:
        """Record a request and consume its next scripted response.

        :param method: GET, POST, or DELETE verb selecting the queue.
        :param endpoint: Relative API path selecting the queue.
        :param body: Create payload recorded with the request.
        :returns: Next provider response for the selected request.
        :raises BaseException: Scripted provider failure supplied by the test.
        """  # noqa: DOC503 - The dynamic fixture exception is raised verbatim.
        self.calls.append((method, endpoint, body))
        value = self.responses[(method, endpoint)].pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def get_json(self, session: BoundGitLabSession, endpoint: str) -> JSONValue:
        """Consume a scripted runner GET response.

        :param session: Bound session accepted by the fake API.
        :param endpoint: Relative runner read endpoint.
        :returns: Next scripted GET result.
        """
        return self._next("GET", endpoint)

    def post_json(
        self, session: BoundGitLabSession, endpoint: str, body: JSONValue
    ) -> JSONValue:
        """Consume a scripted runner POST response.

        :param session: Bound session accepted by the fake API.
        :param endpoint: Relative runner create endpoint.
        :param body: Create payload recorded in the call journal.
        :returns: Next scripted POST result.
        """
        return self._next("POST", endpoint, body)

    def delete_json(self, session: BoundGitLabSession, endpoint: str) -> JSONValue:
        """Consume a scripted runner DELETE response.

        :param session: Bound session accepted by the fake API.
        :param endpoint: Relative runner deletion endpoint.
        :returns: Next scripted DELETE result.
        """
        return self._next("DELETE", endpoint)


@pytest.mark.parametrize("confirmed", (None, (11,)))
def test_group_apply_refuses_missing_or_changed_project_set_before_post(
    confirmed: Optional[tuple[int, ...]],
) -> None:
    """Reject assignment without matching live group project snapshot.

    :param confirmed: Missing or stale project-ID set supplied to the plan.

    :returns: None; assertions in the body validate the regression.
    """
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


def test_group_repeat_returns_no_op_with_independent_membership_readback() -> None:
    """Return verified NO_OP for assignments already present without POST.
    :returns: None; assertions in the body validate the regression.
    """
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


def test_live_plan_cli_binds_group_ids_without_post(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Publish confirmed group IDs and digest from a read-only live plan.

    :param monkeypatch: Pytest fixture replacing target, session, API, and access.
    :param tmp_path: Unused pytest path fixture; no filesystem effect asserted.
    :param capsys: Pytest fixture capturing the planned report.

    :returns: None; assertions in the body validate the regression.
    """
    plan = _plan()
    session = _bound_session(plan)
    api = FakeAPI(
        {
            (
                "GET",
                _list("groups/42/projects?include_subgroups=true&with_shared=false"),
            ): [[{"id": 12}, {"id": 11}]],
        }
    )
    monkeypatch.setattr(
        GITLAB_ACTION,
        "resolve_gitlab_target",
        lambda *_args, **_kwargs: (object(), "test"),
    )
    monkeypatch.setattr(GITLAB_ACTION, "make_plan", lambda *_args: plan)
    monkeypatch.setattr(
        GITLAB_ACTION, "bind_gitlab_session", lambda *_args, **_kwargs: session
    )
    monkeypatch.setattr(GITLAB_ACTION, "GlabAPIClient", lambda *, timeout: api)
    monkeypatch.setattr(
        GITLAB_ACTION,
        "check_gitlab_operation_access",
        lambda _session, api_client: {
            "status": "PASS",
            "identity": {"username": "unit"},
            "target": {"kind": "group", "id": 42},
        },
    )

    assert (
        RUNNER.main(
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Delete a created runner if one-time token sink cannot be verified.

    :param monkeypatch: Pytest fixture failing token-file read-back.
    :param tmp_path: Private lock and token destination directory.

    :returns: None; assertions in the body validate the regression.
    """
    monkeypatch.setattr(API, "_lock_root", lambda: tmp_path / "locks")
    token_out = tmp_path / "one-time.token"
    scope = "groups/42/runners"
    api = FakeAPI(
        {
            ("GET", _list(scope)): [[], []],
            ("POST", "user/runners"): [{"id": 23, "token": "secret"}],
            ("GET", "runners/23"): [
                {"id": 23, "description": "fresh-runner"},
                API.GitLabAPIError("provider_404"),
            ],
            ("DELETE", "runners/23"): [{}],
        }
    )
    original_read = Path.read_text

    def fail_token_read(
        path: Path,
        *args: str,
        **kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> str:
        """Fail only the token sink read while forwarding other file reads.

        :param path: Path being read by the runner create workflow.
        :param *args: Positional arguments forwarded to the helper under test.
        :param **kwargs: Keyword arguments forwarded to the fake command boundary.
        :returns: Original reader's text for paths other than the token sink.
        :raises OSError: Created token file is read back.

        """
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


def test_membership_snapshot_reads_direct_associations_once() -> None:
    """Read direct runner project associations in one API request.
    :returns: None; assertions in the body validate the regression.
    """
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


def test_group_runner_is_not_accepted_for_project_assignment() -> None:
    """Reject a group-type runner before project assignment writes.
    :returns: None; assertions in the body validate the regression.
    """
    api = FakeAPI(
        {("GET", "runners/7"): [{"id": 7, "runner_type": "group_type", "projects": []}]}
    )
    with pytest.raises(
        ACTION.ActionError, match="runner_assignment_requires_project_type"
    ):
        RUNNERS.apply(api, object(), _plan(project_ids=(11,)), 42)
    assert api.calls == [("GET", "runners/7", None)]


def test_runner_create_requires_token_sink_during_offline_plan() -> None:
    """Reject a create plan lacking a private one-time token destination.
    :returns: None; assertions in the body validate the regression.
    """
    args = SimpleNamespace(
        action="create",
        description="fresh-runner",
        tag=[],
        runner_id=None,
        token_out=None,
        apply=False,
        limit=10,
        filter=[],
        job_id=None,
    )
    with pytest.raises(
        ACTION.ActionError, match="token_out_required_for_runner_create_plan"
    ):
        RUNNERS.prepare(args, object(), "group")


@pytest.mark.parametrize(
    "reason", ("authentication", "authorization", "command_failed")
)
def test_terminal_runner_post_failure_clears_pending_and_preserves_reason(
    reason: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep terminal create refusal and clear its pending marker.

    :param reason: Authentication, authorization, or command failure class.
    :param monkeypatch: Pytest fixture selecting scratch create-lock storage.
    :param tmp_path: Private lock and token destination directory.

    :returns: None; assertions in the body validate the regression.
    """
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep pending recovery marker when create response lacks runner ID.

    :param monkeypatch: Pytest fixture selecting scratch create-lock storage.
    :param tmp_path: Private lock and token destination directory.

    :returns: None; assertions in the body validate the regression.
    """
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Clear uncertain marker after absence but defer POST to next invocation.

    :param monkeypatch: Pytest fixture selecting scratch create-lock storage.
    :param tmp_path: Private lock and token destination directory.

    :returns: None; assertions in the body validate the regression.
    """
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Require manual recovery for existing same-description runner before retry.

    :param monkeypatch: Pytest fixture selecting scratch create-lock storage.
    :param tmp_path: Private lock and token destination directory.

    :returns: None; assertions in the body validate the regression.
    """
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
