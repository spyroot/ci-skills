"""Focused workflow contracts for toolbox publication and runner composition.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
from argparse import Namespace
from collections.abc import Callable, Mapping, MutableMapping, MutableSequence, Sequence
from email.message import Message
from pathlib import Path
from types import ModuleType, TracebackType
from typing import TYPE_CHECKING, Optional, Self, Union
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest
from conftest import (
    REPO_ROOT,
    SCRIPT_ROOT,
    import_script_module,
    load_module,
)
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

HTTP = import_script_module("lib.python.protocol.http")
API = import_script_module("lib.python.core.cli_api")
SCHEMA = import_script_module("lib.python.core.schema")
STATUS = import_script_module("lib.python.sys.status")
TARGET = import_script_module("lib.python.core.target")
WORKFLOWS = import_script_module("lib.python.core.toolbox_actions")
CALLBACKS = import_script_module("lib.python.core.abstract.callback")
CALLBACK_COLLECTIONS = import_script_module("lib.python.core.abstract.callbacks")
SKILL_ACTION = import_script_module("lib.python.core.abstract.skill_action")
DELIVERY_STATE = import_script_module("lib.python.core.delivery_state")
EXECUTOR = import_script_module("lib.python.core.executor")
REGISTRY = import_script_module("lib.python.core.harbor.registry_mirror")
RUNTIME = import_script_module("lib.python.core.runtime")
TOOLBOX = import_script_module("lib.python.core.toolbox")


if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue


class _Response:
    """Minimal successful urllib response for retry-contract tests.

    .. attribute :: status
        :type: int

        HTTP status code exposed by urllib responses.
    """

    status: int = 200

    def __init__(self, payload: JSONValue):
        """Store one JSON-serializable response payload.

        :param payload: JSON value encoded into the fake provider response body.
        """
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> Self:
        """Enter the context manager used by ``urllib`` responses.

        :returns: The fake response instance.
        """
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Exit the response context without suppressing failures.

        :param exc_type: Exception class supplied by the context manager, if any.
        :param exc: Exception instance supplied by the context manager, if any.
        :param traceback: Traceback supplied by the context manager, if any.
        :returns: None.
        """
        return

    def read(self, _limit: int) -> bytes:
        """Return the bounded JSON bytes supplied by the test.

        :param _limit: Maximum byte count requested by the client.
        :returns: The encoded fake response body.
        """
        return self.payload


class _Opener:
    """Return the selected finite sequence of urllib outcomes."""

    def __init__(self, outcomes: Sequence[Union[_Response, BaseException]]):
        """Create a finite fake opener.

        :param outcomes: Ordered provider responses or exceptions.
        """
        self.outcomes = iter(outcomes)
        self.requests: MutableSequence[Request] = []

    def open(self, request: Request, *, timeout: int) -> _Response:
        """Record one request and return or raise its configured outcome.

        :param request: Prepared urllib request from the client.
        :param timeout: Per-request timeout selected by the client.
        :returns: The configured fake response.
        :raises BaseException: Configured transport failure supplied by the test.
        """  # noqa: DOC503 - The dynamic fixture exception is raised verbatim.
        assert timeout == 7
        self.requests.append(request)
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, _Response)
        return outcome


def _http_error(status: int, *, retry_after: Optional[str] = None) -> HTTPError:
    """Build one provider response error without a body.

    :param status: Provider HTTP status code.
    :param retry_after: Optional Retry-After header value.
    :returns: HTTPError shaped like a urllib provider failure.
    """
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError(
        "https://harbor.example.test/api", status, "failure", headers, None
    )


def _http_client(
    outcomes: Sequence[Union[_Response, BaseException]], sleeps: MutableSequence[float]
) -> tuple[HTTP.HTTPClient, _Opener]:
    """Return a direct HTTP client with a finite fake opener.

    :param outcomes: Ordered provider responses or exceptions.
    :param sleeps: Mutable capture for requested retry delays.
    :returns: Bound HTTP client and the fake opener it will use.
    """
    opener = _Opener(outcomes)
    client = HTTP.HTTPClient(
        "https://harbor.example.test",
        authorization="Basic hidden",
        timeout=7,
        sleep=sleeps.append,
    )
    client._opener = opener
    return client, opener


def test_http_get_retries_shared_transient_policy_and_retry_after() -> None:
    """Harbor reads use the same bounded safe-read retry policy as CLI reads.
    :returns: None; assertions in the body validate the regression.
    """
    sleeps: MutableSequence[float] = []
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
def test_http_get_retries_provider_timeout_and_transport(outcome: JSONValue) -> None:
    """Retry one safe GET after a provider timeout or transport error.

    :param outcome: Initial HTTP 408 response error or urllib transport error.

    :returns: None; assertions in the body validate the regression.
    """
    sleeps: MutableSequence[float] = []
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
def test_http_get_authentication_errors_are_terminal(status: int, reason: str) -> None:
    """Stop an authenticated GET after its first credential failure.

    :param status: HTTP 401 or 403 returned by the provider.
    :param reason: Expected authentication or authorization error category.

    :returns: None; assertions in the body validate the regression.
    """
    sleeps: MutableSequence[float] = []
    client, opener = _http_client([_http_error(status)], sleeps)

    with pytest.raises(HTTP.APIError, match=reason) as failure:
        client.request("GET", "artifacts/unit")

    assert failure.value.attempts == 1
    assert len(opener.requests) == 1
    assert sleeps == []


def test_http_get_retry_exhaustion_stops_at_the_shared_attempt_bound() -> None:
    """A persistently unavailable Harbor never retries beyond READ_ATTEMPTS.
    :returns: None; assertions in the body validate the regression.
    """
    sleeps: MutableSequence[float] = []
    client, opener = _http_client([_http_error(503)] * API.READ_ATTEMPTS, sleeps)

    with pytest.raises(HTTP.APIError, match="provider_5xx") as failure:
        client.request("GET", "artifacts/unit")

    assert failure.value.attempts == API.READ_ATTEMPTS
    assert len(opener.requests) == API.READ_ATTEMPTS
    assert len(sleeps) == API.READ_ATTEMPTS - 1


@pytest.mark.parametrize("method", ("POST", "PUT", "DELETE"))
def test_http_writes_are_never_replayed(method: str) -> None:
    """Attempt a failed provider mutation once without sleeping or retrying.

    :param method: POST, PUT, or DELETE method used for the failed request.

    :returns: None; assertions in the body validate the regression.
    """
    sleeps: MutableSequence[float] = []
    client, opener = _http_client([_http_error(503)], sleeps)

    with pytest.raises(HTTP.APIError, match="provider_5xx") as failure:
        client.request(method, "artifacts/unit", {"tag": "unit"})

    assert failure.value.attempts == 1
    assert len(opener.requests) == 1
    assert sleeps == []


def _toolbox_document(linux: JSONObject) -> JSONObject:
    """Embed one Linux installation route in a minimal ToolboxSpec document.

    :param linux: Selected dependency's Linux method and its supplied fields.
    :returns: ToolboxSpec mapping passed to specification validation.
    """
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
    linux: JSONObject, missing: str
) -> None:
    """Reject each installation route that omits a method-required field.

    :param linux: Incomplete archive, deb, installer, download, go, npm, or uv route.
    :param missing: Name of the field deliberately omitted from that route.

    :returns: None; assertions in the body validate the regression.
    """
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
def test_toolbox_schema_accepts_complete_selected_installation_routes(
    linux: JSONObject,
) -> None:
    """Accept each supported installation method with all required fields.

    :param linux: Complete archive, deb, installer, download, go, npm, or uv route.

    :returns: None; assertions in the body validate the regression.
    """
    SCHEMA.validate_specification(
        _toolbox_document(linux),
        "toolbox.schema.json",
    )


@pytest.mark.parametrize(
    "raw",
    (
        '{"$id":"https://example.test/unit","type":"object","type":"object"}',
        '{"$id":"https://example.test/unit","maximum":NaN}',
    ),
)
def test_load_schema_rejects_ambiguous_or_nonfinite_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: str
) -> None:
    """Reject a malformed installed schema before it can enter the registry.

    :param monkeypatch: Fixture selecting the isolated installed schema directory.
    :param tmp_path: Scratch directory receiving the schema fixture.
    :param raw: Duplicate-key or nonfinite JSON schema bytes.

    :returns: None; assertions in the body validate the regression.
    """
    schema_directory = tmp_path / "schemas"
    schema_directory.mkdir()
    (schema_directory / "unit.schema.json").write_text(raw, encoding="utf-8")
    monkeypatch.setattr(SCHEMA, "SCHEMA_DIRECTORY", schema_directory)

    with pytest.raises(ValueError, match="duplicate_json_field|nonfinite_json_number"):
        SCHEMA.load_schema("unit.schema.json")


@pytest.mark.parametrize(
    "raw",
    (
        (
            '{"apiVersion":"ci-skills/v1","apiVersion":"ci-skills/v1",'
            '"kind":"ToolboxSpec","metadata":{"name":"unit"},"spec":'
            '{"baseImage":"registry.example.test/base:unit","image":'
            '{"repository":"toolbox","tag":"unit"},"build":'
            '{"namespace":"unit","app":"unit"},"dependencies":'
            '[{"name":"tool","commands":["tool"],"linux":'
            '{"method":"go","module":"example.test/tool@v1.0.0"}}],'
            '"requiredCommands":["tool"]}}'
        ),
        (
            '{"apiVersion":"ci-skills/v1","kind":"ToolboxSpec",'
            '"metadata":{"name":"unit","unused":NaN},"spec":{}}'
        ),
    ),
)
def test_toolbox_spec_rejects_ambiguous_or_nonfinite_json(
    tmp_path: Path, raw: str
) -> None:
    """Reject invalid JSON before toolbox schema or local input processing.

    :param tmp_path: Scratch directory receiving the selected toolbox specification.
    :param raw: Duplicate-key or nonfinite JSON specification bytes.

    :returns: None; assertions in the body validate the regression.
    """
    source = tmp_path / "toolbox.json"
    source.write_text(raw, encoding="utf-8")

    with pytest.raises(SCHEMA.SpecificationError, match="invalid_specification:/:json"):
        TOOLBOX.ToolboxSpec.from_path(source)


def test_workflow_records_accept_only_shared_action_outcomes() -> None:
    """Callback records use the one shared applied/no-op/verified vocabulary.
    :returns: None; assertions in the body validate the regression.
    """
    schema = json.loads(
        (
            REPO_ROOT
            / "ci-skills"
            / "schemas"
            / "results"
            / "toolbox-workflow.schema.json"
        ).read_text(encoding="utf-8")
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
    *, revision: Optional[str], skill: Optional[JSONObject]
) -> TARGET.Target:
    """Create the selected Harbor target used by workflow plan fingerprints.

    :param revision: Optional installed code revision bound into the plan.
    :param skill: Optional installed skill identity bound into the plan.
    :returns: Target with Harbor configuration and supplied provenance fields.
    """
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


def _workflow_action(
    *, target: TARGET.Target, source: str, apply: bool = False
) -> WORKFLOWS.WorkflowAction:
    """Prepare a Harbor push action with a selected target and source image.

    :param target: Target supplying Harbor and installed identity fields.
    :param source: Source image reference assigned to the action.
    :param apply: Whether the action requests publication instead of planning.
    :returns: Workflow action ready for fingerprint or callback execution.
    """
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


class _RecordingWorkflowExecutor:
    """Retain entrypoint callback composition without binding providers."""

    def __init__(
        self,
        context: DELIVERY_STATE.DeliveryState,
        callbacks: CALLBACK_COLLECTIONS.Callbacks,
    ) -> None:
        """Store the context and declared callbacks supplied by an entrypoint.

        :param context: State selected by the workflow action.
        :param callbacks: Declared callbacks before lifecycle binding.
        """
        self.context = context
        self.callbacks = callbacks


def _record_workflow_executors(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
) -> MutableSequence[_RecordingWorkflowExecutor]:
    """Replace one entrypoint executor with a composition-recording factory.

    :param monkeypatch: Pytest fixture replacing the entrypoint executor class.
    :param module: Entrypoint module whose composition factory is under test.
    :returns: Executors built by the entrypoint factory in call order.
    """
    recorded: MutableSequence[_RecordingWorkflowExecutor] = []

    def build(
        *,
        context: DELIVERY_STATE.DeliveryState,
        callbacks: CALLBACK_COLLECTIONS.Callbacks,
    ) -> _RecordingWorkflowExecutor:
        """Record one entrypoint composition without provider configuration.

        :param context: State selected by the workflow action.
        :param callbacks: Declared callbacks before lifecycle binding.
        :returns: Executor double retaining the supplied composition.
        """
        executor = _RecordingWorkflowExecutor(context, callbacks)
        recorded.append(executor)
        return executor

    monkeypatch.setattr(module, "WorkflowExecutor", build)
    return recorded


def test_workflow_fingerprint_binds_installed_identity_revision_and_resolved_source() -> (
    None
):
    """A plan cannot be reused after installed code or its source image changes.
    :returns: None; assertions in the body validate the regression.
    """
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
    confirmation: Optional[str],
) -> None:
    """Resolve a mutable image tag before requiring exact plan confirmation.

    :param confirmation: Missing or incorrect plan digest rejected on apply.

    :returns: None; assertions in the body validate the regression.
    """
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
        """Resolve the selected mutable source image to one fixed digest."""

        def inspect(self, image: str) -> str:
            """Resolve the selected source tag to the test digest.

            :param image: Source image reference passed to Harbor inspection.
            :returns: Immutable digest used to bind the workflow plan.
            """
            assert image == source
            return digest

    callback = WORKFLOWS.ConfirmWorkflow(action=action)
    state = DELIVERY_STATE.DeliveryState()
    state.harbor = Harbor()
    callback.bind(state)
    result = callback.run()

    assert result["image"] == "harbor.example.test/unit/source@" + digest
    assert result["plan_digest"] == action.fingerprint()
    action.args.apply = True
    action.args.confirm_plan = confirmation
    with pytest.raises(ValueError, match="confirm_plan_mismatch_rerun_plan"):
        callback.run()


def test_registry_copy_uses_digest_only_native_references_with_registry_ports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Render portable Skopeo references for tagged digest-pinned images.

    :param monkeypatch: Pytest fixture replacing the bounded native transport.
    :param tmp_path: Scratch directory supplying an authfile path to the service.
    :returns: None; assertions prove inspect and copy remove tags but retain ports.
    """
    source = "registry.example.test:5443/unit/source:latest@sha256:" + "a" * 64
    destination = "registry.example.test:5443/unit/destination:unit@sha256:" + "a" * 64
    native_source = "docker://registry.example.test:5443/unit/source@sha256:" + "a" * 64
    native_destination = (
        "docker://registry.example.test:5443/unit/destination@sha256:" + "a" * 64
    )
    service = REGISTRY.HarborService(
        target=TARGET.HarborTarget(
            url="https://registry.example.test:5443",
            host="registry.example.test:5443",
            project="unit",
        ),
        credential=REGISTRY.RegistryCredential("unit", "secret", "fixture"),
        authfile=tmp_path / "auth.json",
        timeout=30,
    )
    raw_manifest = '{"schemaVersion":2}'
    expected_digest = "sha256:" + hashlib.sha256(raw_manifest.encode()).hexdigest()
    calls: MutableSequence[tuple[str, ...]] = []

    def fake_transport(argv: Sequence[str], **_kwargs: object) -> RUNTIME.CommandResult:
        """Record native registry argv and return the matching success fixture.

        :param argv: Complete native command vector built by the registry service.
        :param _kwargs: Bounded transport options accepted by the fake.
        :returns: Successful inspect output or successful copy completion.
        """
        command = tuple(argv)
        calls.append(command)
        stdout = raw_manifest if command[1] == "inspect" else ""
        return RUNTIME.CommandResult(command, 0, stdout, "")

    monkeypatch.setattr(REGISTRY, "run_command_bounded", fake_transport)

    assert service.copy(source, destination) == expected_digest
    assert calls == [
        (
            "skopeo",
            "inspect",
            "--raw",
            "--authfile",
            str(service.authfile),
            native_source,
        ),
        (
            "skopeo",
            "copy",
            "--all",
            "--preserve-digests",
            "--authfile",
            str(service.authfile),
            native_source,
            native_destination,
        ),
        (
            "skopeo",
            "inspect",
            "--raw",
            "--authfile",
            str(service.authfile),
            native_destination,
        ),
    ]


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
            ("HarborAuth", "ConfirmWorkflow"),
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
            ("K8SAuth", "HarborAuth", "ConfirmWorkflow"),
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
            ),
        ),
    ),
)
def test_entrypoint_main_composes_declared_callbacks_without_provider_access(
    monkeypatch: pytest.MonkeyPatch,
    script: Path,
    argv: Sequence[str],
    expected_callbacks: Sequence[str],
) -> None:
    """Build each public entrypoint's declared callback sequence before execution.

    :param monkeypatch: Pytest fixture replacing the workflow action adapter.
    :param script: Public entrypoint module loaded for this workflow case.
    :param argv: Command arguments selecting the entrypoint operation.
    :param expected_callbacks: Ordered callback class names required by the route.

    :returns: None; assertions in the body validate the regression.
    """
    module = load_module(f"workflow_entrypoint_{script[:-3]}", SCRIPT_ROOT / script)
    captured: MutableSequence[SKILL_ACTION.SkillAction] = []
    executors = _record_workflow_executors(monkeypatch, module)

    class FakeWorkflowAction(SKILL_ACTION.SkillAction):
        """Capture callbacks composed by each public workflow entrypoint."""

        def __init__(self, args: Namespace):
            """Provide the fields each entrypoint needs to compose its callbacks.

            :param args: Parsed command arguments supplied by the public entrypoint.
            """
            self.args = args
            self.target = object()
            self.spec = Namespace(image_repository="toolbox", image_tag="unit")
            self.runner = object()
            self.repository = "toolbox"
            self.context = DELIVERY_STATE.DeliveryState()

        def execute(self, factory: Callable[[], EXECUTOR.WorkflowExecutor]) -> int:
            """Capture callbacks built by the entrypoint's executor factory.

            :param factory: Entrypoint callback-executor factory under test.
            :returns: Successful command exit code after capturing the callback list.
            """
            captured.append(self)
            executor = factory()
            self.callbacks = executor.callbacks
            return 0

    monkeypatch.setattr(module, "WorkflowAction", FakeWorkflowAction)

    assert module.main(argv) == 0
    action = captured[0]
    assert executors[0].context is action.context
    assert [type(callback).__name__ for callback in action.callbacks] == list(
        expected_callbacks
    )


def _captured_report(
    monkeypatch: pytest.MonkeyPatch,
) -> MutableMapping[str, JSONValue]:
    """Capture a workflow report at the command result emission boundary.

    :param monkeypatch: Pytest fixture replacing the workflow result emitter.
    :returns: Mutable mapping filled with the emitted report fields.
    """
    captured: MutableMapping[str, JSONValue] = {}

    def emit(result: Mapping[str, JSONValue], _args: Namespace) -> int:
        """Copy the emitted action report into the test capture.

        :param result: Workflow report mapping emitted by the action adapter.
        :param _args: Command arguments accepted but unused by this fake emitter.
        :returns: Successful command exit code.
        """
        if not isinstance(result, Mapping):
            pytest.fail("workflow emitter received a non-mapping result")
        captured.update(result)
        return 0

    monkeypatch.setattr(WORKFLOWS, "emit_result", emit)
    return captured


def test_workflow_dry_run_emits_schema_valid_plan_without_constructing_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit a schema-valid dry-run plan without constructing callbacks.

    :param monkeypatch: Pytest fixture replacing preparation and report emission.

    :returns: None; assertions in the body validate the regression.
    """
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
                REPO_ROOT
                / "ci-skills"
                / "schemas"
                / "results"
                / "toolbox-workflow.schema.json"
            ).read_text(encoding="utf-8")
        )
    ).validate(captured)
    assert captured["status"] == STATUS.DRY_RUN
    assert captured["errors"] == []
    assert captured["records"] == []


def test_plan_callback_stops_before_a_dependent_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construct only auth and confirmation callbacks for an unconfirmed plan.
    :returns: None; assertions in the body validate the regression.
    """
    module = load_module("workflow_plan_toolbox", SCRIPT_ROOT / "toolbox.py")
    executors = _record_workflow_executors(monkeypatch, module)
    action = _workflow_action(
        target=_workflow_target(
            revision="a" * 40,
            skill={"algorithm": "sha256-tree-v1", "digest": "b" * 64},
        ),
        source="",
    )
    action.args.kind = "toolbox"
    action.args.timeout = 1800
    action.spec = Namespace(
        image_repository="toolbox",
        image_tag="unit",
        namespace="unit",
        app="runner",
    )

    executor = module.workflow(action)
    callbacks = [type(callback).__name__ for callback in executor.callbacks]

    assert executors == [executor]
    assert executor.context is action.context
    assert callbacks == ["K8SAuth", "HarborAuth", "ConfirmWorkflow"]
    assert "EnsureNamespace" not in callbacks
    assert "HarborBuild" not in callbacks


def test_workflow_failure_emits_schema_valid_blocked_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit a schema-valid blocked report when input preparation fails.

    :param monkeypatch: Pytest fixture replacing preparation and report emission.

    :returns: None; assertions in the body validate the regression.
    """
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
        """Simulate an invalid workflow input before callback construction.

        :returns: None; the helper records behavior through its side effects.
        :raises ValueError: Always, to trigger the blocked result path.

        """
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
                REPO_ROOT
                / "ci-skills"
                / "schemas"
                / "results"
                / "toolbox-workflow.schema.json"
            ).read_text(encoding="utf-8")
        )
    ).validate(captured)
    assert captured["status"] == STATUS.BLOCKED
    assert captured["errors"] == [
        {"source": "harbor_push", "reason": "unit_input_failure"}
    ]


def test_generated_workflow_report_resolves_nested_result_schema_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate a generated report through its installed nested result schema.

    :param monkeypatch: Pytest fixture replacing preparation and report emission.

    :returns: None; assertions require offline sibling ``$ref`` resolution.
    """
    revision = "a" * 40
    action = _workflow_action(
        target=_workflow_target(
            revision=revision,
            skill={
                "algorithm": "sha256-tree-v1",
                "digest": "b" * 64,
                "file_count": 1,
                "revision": {
                    "value": revision,
                    "source": "unit",
                    "verified": True,
                },
                "consuming_project": {"commit": None, "source": None},
            },
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
    assert captured["tested_revision"] == revision
    SCHEMA.validate_specification(captured, "results/toolbox-workflow.schema.json")
