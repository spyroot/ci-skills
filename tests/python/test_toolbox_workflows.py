"""Focused workflow contracts for toolbox publication and runner composition."""

from __future__ import annotations

import json
from argparse import Namespace
from email.message import Message
from pathlib import Path
from typing import Self
from urllib.error import HTTPError, URLError

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from tests.python.conftest import (
    REPO_ROOT,
    SCRIPT_ROOT,
    import_script_module,
    load_module,
)

HTTP = import_script_module("lib.python.protocol.http")
SCHEMA = import_script_module("lib.python.core.schema")
STATUS = import_script_module("lib.python.sys.status")
TARGET = import_script_module("lib.python.core.target")
WORKFLOWS = import_script_module("lib.python.core.toolbox_actions")
CALLBACKS = import_script_module("core.action")


class _Response:
    """Minimal successful urllib response for retry-contract tests."""

    status = 200

    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_unused: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        """Return the bounded JSON bytes supplied by the test."""
        return self.payload


class _Opener:
    """Return the selected finite sequence of urllib outcomes."""

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = iter(outcomes)
        self.requests: list[object] = []

    def open(self, request: object, *, timeout: int) -> _Response:
        """Record one request and return or raise its configured outcome."""
        assert timeout == 7
        self.requests.append(request)
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, _Response)
        return outcome


def _http_error(status: int, *, retry_after: str | None = None) -> HTTPError:
    """Build one provider response error without a body."""
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError(
        "https://harbor.example.test/api", status, "failure", headers, None
    )


def _http_client(outcomes: list[object], sleeps: list[float]) -> tuple[object, _Opener]:
    """Return a direct HTTP client with a finite fake opener."""
    opener = _Opener(outcomes)
    client = HTTP.HTTPClient(
        "https://harbor.example.test",
        authorization="Basic hidden",
        timeout=7,
        sleep=sleeps.append,
    )
    client._opener = opener
    return client, opener


def test_http_get_retries_shared_transient_policy_and_retry_after():
    """Harbor reads use the same bounded safe-read retry policy as CLI reads."""
    sleeps: list[float] = []
    client, opener = _http_client(
        [
            _http_error(503),
            _http_error(429, retry_after="1"),
            _Response({"digest": "sha256:unit"}),
        ],
        sleeps,
    )

    assert client.request("GET", "artifacts/unit") == {"digest": "sha256:unit"}
    assert len(opener.requests) == 3
    assert [request.get_method() for request in opener.requests] == ["GET"] * 3
    assert [request.full_url for request in opener.requests] == [
        "https://harbor.example.test/artifacts/unit"
    ] * 3
    assert 0.2 <= sleeps[0] <= 0.24
    assert sleeps[1] == 1


@pytest.mark.parametrize("outcome", (_http_error(408), URLError("offline")))
def test_http_get_retries_provider_timeout_and_transport(outcome):
    """Safe Harbor reads retry both provider timeout and transport failure once."""
    sleeps: list[float] = []
    client, opener = _http_client(
        [outcome, _Response({"digest": "sha256:unit"})], sleeps
    )

    assert client.request("GET", "artifacts/unit") == {"digest": "sha256:unit"}
    assert len(opener.requests) == 2
    assert len(sleeps) == 1


@pytest.mark.parametrize(
    ("status", "reason"),
    ((401, "authentication"), (403, "authorization")),
)
def test_http_get_authentication_errors_are_terminal(status, reason):
    """Credential failures do not replay an authenticated request."""
    sleeps: list[float] = []
    client, opener = _http_client([_http_error(status)], sleeps)

    with pytest.raises(HTTP.APIError, match=reason) as failure:
        client.request("GET", "artifacts/unit")

    assert failure.value.attempts == 1
    assert len(opener.requests) == 1
    assert sleeps == []


def test_http_get_retry_exhaustion_stops_at_the_shared_attempt_bound():
    """A persistently unavailable Harbor never retries beyond READ_ATTEMPTS."""
    sleeps: list[float] = []
    client, opener = _http_client([_http_error(503)] * HTTP.READ_ATTEMPTS, sleeps)

    with pytest.raises(HTTP.APIError, match="provider_5xx") as failure:
        client.request("GET", "artifacts/unit")

    assert failure.value.attempts == HTTP.READ_ATTEMPTS
    assert len(opener.requests) == HTTP.READ_ATTEMPTS
    assert len(sleeps) == HTTP.READ_ATTEMPTS - 1


@pytest.mark.parametrize("method", ("POST", "PUT", "DELETE"))
def test_http_writes_are_never_replayed(method):
    """A failed write remains one observable provider attempt."""
    sleeps: list[float] = []
    client, opener = _http_client([_http_error(503)], sleeps)

    with pytest.raises(HTTP.APIError, match="provider_5xx") as failure:
        client.request(method, "artifacts/unit", {"tag": "unit"})

    assert failure.value.attempts == 1
    assert len(opener.requests) == 1
    assert sleeps == []


def _toolbox_document(linux: dict[str, object]) -> dict[str, object]:
    """Return a minimal schema-valid toolbox document around one install route."""
    return {
        "apiVersion": "ci-skills/v1",
        "kind": "ToolboxSpec",
        "metadata": {"name": "unit"},
        "spec": {
            "baseImage": "registry.example.test/base:unit",
            "image": {"repository": "toolbox", "tag": "unit"},
            "build": {"namespace": "unit", "app": "unit"},
            "dependencies": [{"name": "tool", "commands": ["tool"], "linux": linux}],
            "requiredCommands": ["tool"],
        },
    }


@pytest.mark.parametrize(
    ("linux", "missing"),
    (
        ({"method": "archive", "sha256": "a" * 64, "destination": "/opt/tool"}, "url"),
        (
            {
                "method": "archive",
                "url": "https://example.test/tool.tar",
                "destination": "/opt/tool",
            },
            "sha256",
        ),
        (
            {
                "method": "archive",
                "url": "https://example.test/tool.tar",
                "sha256": "a" * 64,
            },
            "destination",
        ),
        ({"method": "deb", "url": "https://example.test/tool.deb"}, "sha256"),
        (
            {
                "method": "installer",
                "url": "https://example.test/tool.sh",
                "sha256": "a" * 64,
                "args": [],
            },
            "interpreter",
        ),
        (
            {
                "method": "download",
                "url": "https://example.test/tool",
                "sha256": "a" * 64,
            },
            "destination",
        ),
        ({"method": "go"}, "module"),
        ({"method": "npm"}, "package"),
        ({"method": "uv"}, "package"),
    ),
)
def test_toolbox_schema_requires_each_selected_installation_method_input(
    linux, missing
):
    """The installed schema rejects incomplete selected installation routes."""
    with pytest.raises(SCHEMA.SpecificationError, match="invalid_specification"):
        SCHEMA.validate_specification(_toolbox_document(linux), "toolbox.schema.json")


@pytest.mark.parametrize(
    "linux",
    (
        {
            "method": "archive",
            "url": "https://example.test/tool.tar",
            "sha256": "a" * 64,
            "destination": "/opt/tool",
        },
        {"method": "deb", "url": "https://example.test/tool.deb", "sha256": "a" * 64},
        {
            "method": "installer",
            "url": "https://example.test/tool.sh",
            "sha256": "a" * 64,
            "interpreter": "bash",
            "args": ["--batch"],
        },
        {
            "method": "download",
            "url": "https://example.test/tool",
            "sha256": "a" * 64,
            "destination": "/usr/local/bin/tool",
        },
        {"method": "go", "module": "example.test/tool@v1.0.0"},
        {"method": "npm", "package": "tool@1.0.0"},
        {"method": "uv", "package": "tool==1.0.0"},
    ),
)
def test_toolbox_schema_accepts_complete_selected_installation_routes(linux):
    """Every supported route admits its complete declared input fields."""
    SCHEMA.validate_specification(
        _toolbox_document(linux),
        "toolbox.schema.json",
    )


def test_workflow_records_accept_only_shared_action_outcomes():
    """Callback records use the one shared applied/no-op/verified vocabulary."""
    schema = json.loads(
        (REPO_ROOT / "schemas" / "results" / "toolbox-workflow.schema.json").read_text(
            encoding="utf-8"
        )
    )["properties"]["records"]["items"]
    validator = Draft202012Validator(schema)

    assert {STATUS.APPLIED, STATUS.NO_OP, STATUS.VERIFIED} == {
        "APPLIED",
        "NO_OP",
        "VERIFIED",
    }
    for action in (STATUS.APPLIED, STATUS.NO_OP, STATUS.VERIFIED):
        validator.validate({"callback": "Unit", "verified": True, "action": action})
    with pytest.raises(ValidationError):
        validator.validate({"callback": "Unit", "verified": True, "action": "INVENTED"})


def _workflow_target(
    *, revision: str | None, skill: dict[str, object] | None
) -> object:
    """Return the minimum selected target needed for a workflow fingerprint."""
    return TARGET.Target(
        github=None,
        gitlab=None,
        kubernetes=None,
        harbor=TARGET.HarborTarget(
            url="https://harbor.example.test",
            host="harbor.example.test",
            project="unit",
        ),
        source_file=Path("/unit/target.toml"),
        source_kind="project",
        tested_revision=revision,
        skill=skill,
    )


def _workflow_action(*, target: object, source: str, apply: bool = False) -> object:
    """Build one prepared source-image workflow without provider access."""
    action = WORKFLOWS.WorkflowAction(
        Namespace(
            kind="harbor_push",
            action="create",
            source_image=source,
            repository="toolbox",
            tag="unit",
            image=None,
            apply=apply,
            confirm_plan=None,
        )
    )
    action.target = target
    return action


def test_workflow_fingerprint_binds_installed_identity_revision_and_resolved_source():
    """A plan cannot be reused after installed code or its source image changes."""
    skill = {"algorithm": "sha256-tree-v1", "digest": "a" * 64}
    source = "harbor.example.test/unit/source@sha256:" + "b" * 64
    action = _workflow_action(
        target=_workflow_target(revision="a" * 40, skill=skill), source=source
    )
    action.resolved_source = source
    baseline = action.fingerprint()

    changed_revision = _workflow_action(
        target=_workflow_target(revision="c" * 40, skill=skill), source=source
    )
    changed_revision.resolved_source = source
    changed_tree = _workflow_action(
        target=_workflow_target(
            revision="a" * 40,
            skill={"algorithm": "sha256-tree-v1", "digest": "d" * 64},
        ),
        source=source,
    )
    changed_tree.resolved_source = source
    changed_source = _workflow_action(
        target=_workflow_target(revision="a" * 40, skill=skill), source=source
    )
    changed_source.resolved_source = (
        "harbor.example.test/unit/source@sha256:" + "e" * 64
    )

    assert baseline != changed_revision.fingerprint()
    assert baseline != changed_tree.fingerprint()
    assert baseline != changed_source.fingerprint()


@pytest.mark.parametrize("confirmation", (None, "0" * 64))
def test_confirm_workflow_resolves_source_digest_and_requires_its_exact_plan(
    confirmation,
):
    """A mutable source tag becomes immutable before confirmation and publication."""
    source = "harbor.example.test/unit/source:latest"
    digest = "sha256:" + "f" * 64
    action = _workflow_action(
        target=_workflow_target(
            revision=None,
            skill=None,
        ),
        source=source,
    )

    class Harbor:
        def inspect(self, image: str) -> str:
            assert image == source
            return digest

    callback = WORKFLOWS.ConfirmWorkflow(action=action)
    callback.bind(WORKFLOWS.ExecutionContext(services={"harbor": Harbor()}))
    result = callback.run()

    assert result["image"] == source + "@" + digest
    assert result["plan_digest"] == action.fingerprint()
    action.args.apply = True
    action.args.confirm_plan = confirmation
    with pytest.raises(ValueError, match="confirm_plan_mismatch_rerun_plan"):
        callback.run()


@pytest.mark.parametrize(
    ("script", "argv", "expected_callbacks"),
    (
        (
            "toolbox.py",
            ["create", "--spec", "unit.json"],
            (
                "K8SAuth",
                "HarborAuth",
                "ConfirmWorkflow",
                "EnsureNamespace",
                "HarborBuild",
            ),
        ),
        (
            "harbor_push.py",
            [
                "push",
                "--source-image",
                "source.example.test/unit:latest",
                "--repository",
                "toolbox",
                "--tag",
                "unit",
            ],
            ("HarborAuth", "ConfirmWorkflow", "HarborPush"),
        ),
        (
            "harbor_pull.py",
            [
                "pull",
                "--image",
                "harbor.example.test/unit/toolbox@sha256:" + "a" * 64,
                "--namespace",
                "unit",
                "--name",
                "pull",
            ],
            ("K8SAuth", "HarborAuth", "ConfirmWorkflow", "PullImage"),
        ),
        (
            "create_selfhosted_runner.py",
            [
                "create",
                "--spec",
                "unit.json",
                "--name",
                "runner",
                "--runner-root",
                "/ci-skills/runner",
                "--label",
                "self-hosted",
            ],
            (
                "K8SAuth",
                "GitHubAuth",
                "HarborAuth",
                "ConfirmWorkflow",
                "EnsureNamespace",
                "HarborBuild",
                "HarborPush",
                "PullImage",
                "GitHubRunnerRuntime",
                "DeployRunner",
                "CreateGitHubRunner",
            ),
        ),
    ),
)
def test_entrypoint_main_composes_declared_callbacks_without_provider_access(
    monkeypatch, script, argv, expected_callbacks
):
    """Each public workflow main builds its declared callback graph before execution."""
    module = load_module(f"workflow_entrypoint_{script[:-3]}", SCRIPT_ROOT / script)
    captured: list[object] = []

    class FakeWorkflowAction:
        def __init__(self, args: object) -> None:
            self.args = args
            self.target = object()
            self.spec = Namespace(image_repository="toolbox", image_tag="unit")
            self.runner = object()
            self.repository = "toolbox"
            self.context = CALLBACKS.ExecutionContext()

        def execute(self, factory: object) -> int:
            captured.append(self)
            executor = factory()
            self.callbacks = executor.callbacks
            return 0

    monkeypatch.setattr(module, "WorkflowAction", FakeWorkflowAction)

    assert module.main(argv) == 0
    action = captured[0]
    assert [type(callback).__name__ for callback in action.callbacks] == list(
        expected_callbacks
    )


def _captured_report(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Replace report emission with a capture at the command result boundary."""
    captured: dict[str, object] = {}

    def emit(result: object, _args: object) -> int:
        captured.update(result.report)
        return 0

    monkeypatch.setattr(WORKFLOWS, "emit_result", emit)
    return captured


def test_workflow_dry_run_emits_schema_valid_plan_without_constructing_mutations(
    monkeypatch,
):
    """Dry-run computes a bound plan without constructing the callback workflow."""
    action = _workflow_action(
        target=_workflow_target(
            revision=None,
            skill=None,
        ),
        source="harbor.example.test/unit/source:latest",
    )
    action.args.describe = False
    action.args.dry_run = True
    action.args.log_level = "info"
    action.args.log_file = None
    captured = _captured_report(monkeypatch)
    monkeypatch.setattr(action, "prepare", lambda: None)

    assert (
        action.execute(lambda: pytest.fail("dry-run must not construct callbacks")) == 0
    )
    Draft202012Validator(
        json.loads(
            (
                REPO_ROOT / "schemas" / "results" / "toolbox-workflow.schema.json"
            ).read_text(encoding="utf-8")
        )
    ).validate(captured)
    assert captured["status"] == STATUS.DRY_RUN
    assert captured["errors"] == []
    assert captured["records"] == []


def test_plan_callback_stops_before_a_dependent_mutation():
    """An unconfirmed plan retains its digest and never enters downstream work."""
    action = _workflow_action(
        target=_workflow_target(
            revision="a" * 40,
            skill={"algorithm": "sha256-tree-v1", "digest": "b" * 64},
        ),
        source="",
    )
    action.args.source_image = None
    mutations: list[str] = []

    class Mutation(CALLBACKS.Callback[object, object]):
        def run(self) -> object:
            mutations.append("run")
            return None

    confirm = WORKFLOWS.ConfirmWorkflow(action=action)
    CALLBACKS.Executor(callbacks=[confirm, Mutation()]).run()

    assert isinstance(confirm.result, dict)
    assert confirm.result["plan_digest"] == action.fingerprint()
    assert mutations == []


def test_workflow_failure_emits_schema_valid_blocked_result(monkeypatch):
    """Input failure returns the shared blocked result instead of an exception leak."""
    action = _workflow_action(
        target=_workflow_target(
            revision=None,
            skill=None,
        ),
        source="harbor.example.test/unit/source:latest",
    )
    action.args.describe = False
    action.args.dry_run = False
    action.args.log_level = "info"
    action.args.log_file = None
    captured = _captured_report(monkeypatch)

    def fail_prepare() -> None:
        raise ValueError("unit_input_failure")

    monkeypatch.setattr(action, "prepare", fail_prepare)
    assert (
        action.execute(
            lambda: pytest.fail("invalid input must not construct callbacks")
        )
        == 0
    )
    Draft202012Validator(
        json.loads(
            (
                REPO_ROOT / "schemas" / "results" / "toolbox-workflow.schema.json"
            ).read_text(encoding="utf-8")
        )
    ).validate(captured)
    assert captured["status"] == STATUS.BLOCKED
    assert captured["errors"] == [
        {"source": "harbor_push", "reason": "unit_input_failure"}
    ]
