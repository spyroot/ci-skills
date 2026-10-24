"""Focused boundary regressions for shared provider contracts.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
from collections import UserList
from collections.abc import Callable, MutableMapping, Sequence
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import TYPE_CHECKING, NoReturn, Optional, TypeAlias, Union

import pytest
from conftest import authenticated_kubernetes_service, import_script_module

ACCESS_SOURCES: ModuleType = import_script_module("lib.python.access.sources")
ACTION: ModuleType = import_script_module("lib.python.core.gitlab.gitlab_actions")
CLI: ModuleType = import_script_module("lib.python.core.cli")
LOGGING: ModuleType = import_script_module("lib.python.core.logging")
CLI_API: ModuleType = import_script_module("lib.python.core.cli_api")
JSON_TYPES: ModuleType = import_script_module("lib.python.core.json_types")
KUBERNETES: ModuleType = import_script_module("lib.python.core.kubernetes.kubernetes")
KUBERNETES_OBJECTS: ModuleType = import_script_module(
    "lib.python.core.kubernetes.objects"
)
READ_CLI: ModuleType = import_script_module("lib.python.core.read_cli")
READ_TOOLS: ModuleType = import_script_module("lib.python.core.read_tools")
REPORT: ModuleType = import_script_module("lib.python.core.report")
RUNTIME: ModuleType = import_script_module("lib.python.core.runtime")
STATUS: ModuleType = import_script_module("lib.python.sys.status")
TARGET: ModuleType = import_script_module("lib.python.core.target")

SECRET = "boundary-secret-value"


if TYPE_CHECKING:
    from lib.python.core.cli_api import APIError
    from lib.python.core.json_types import JSONObject
    from lib.python.core.read_tools import ReadToolError
    from lib.python.core.runtime import CommandResult
    from lib.python.core.target import Target


ReadFailureFactory: TypeAlias = Callable[[], Union["APIError", "ReadToolError"]]


def _kubernetes_target() -> Target:
    """Build a nonsecret Kubernetes target for service-boundary tests.

    :returns: Target selecting only the synthetic Kubernetes authority.
    """
    return TARGET.Target(
        github=None,
        gitlab=None,
        kubernetes=TARGET.KubernetesTarget(
            context="unit-context",
            server="https://api.cluster.example.test:6443",
            kubeconfig=None,
        ),
        active_surfaces=("kubernetes",),
    )


def _command_result(
    stdout: str, *, returncode: int = 0, stderr: str = ""
) -> CommandResult:
    """Create one synthetic native-command result.

    :param stdout: Provider response body returned by the fake command.
    :param returncode: Native exit status assigned to the fake command.
    :param stderr: Native error text used only for classification.
    :returns: Runtime command result with a stable synthetic argv.
    """
    return RUNTIME.CommandResult(("kubectl",), returncode, stdout, stderr)


def _log_args(*, log_format: str = "json") -> argparse.Namespace:
    """Create the shared logging namespace used by CLI diagnostics.

    :param log_format: Diagnostic format requested by the test.
    :returns: Namespace carrying the logging and mode fields read by ``log_event``.
    """
    return argparse.Namespace(
        action="check",
        apply=True,
        dry_run=False,
        live_plan=False,
        log_file=None,
        log_format=log_format,
        log_level="debug",
        run_id="boundary-test",
    )


def _api_output_args() -> argparse.Namespace:
    """Create read-command output arguments for direct adapter tests.

    :returns: Namespace containing the fields read by the shared output adapter.
    """
    args = _log_args()
    args.describe = False
    args.dry_run = False
    args.live_plan = False
    return args


def test_json_model_accepts_readonly_sequences_and_rejects_non_json_values() -> None:
    """Accept immutable JSON containers while rejecting non-JSON boundaries.

    :returns: None; assertions validate the shared JSON value contract.
    """
    accepted = MappingProxyType(
        {
            "items": (
                MappingProxyType({"name": "unit"}),
                ("nested", MappingProxyType({"enabled": True})),
            )
        }
    )
    rejected: Sequence[object] = (
        b"raw-bytes",
        bytearray(b"raw-bytes"),
        {"metric": float("inf")},
        object(),
    )

    assert JSON_TYPES.is_json_value(accepted)
    for value in rejected:
        assert not JSON_TYPES.is_json_value(value)


def test_userlist_secret_fields_are_redacted_before_json_rendering() -> None:
    """Traverse custom sequences before rendering redacted report JSON.

    :returns: None; assertions validate that nested secret-named fields do not
        survive into the final rendered value.
    """
    redacted = RUNTIME.redact_tree(
        {"records": UserList([MappingProxyType({"GITLAB_TOKEN": SECRET})])}
    )
    rendered = REPORT.render_value(redacted, "json")

    assert SECRET not in rendered
    assert "[REDACTED]" in rendered


def test_kubernetes_object_reads_tuple_of_readonly_objects() -> None:
    """Accept provider arrays supplied as immutable object sequences.

    :returns: None; assertions validate object views produced from tuple items.
    """
    root = KUBERNETES_OBJECTS.KubernetesObject(
        {
            "items": (
                MappingProxyType({"metadata": MappingProxyType({"name": "alpha"})}),
                MappingProxyType({"metadata": MappingProxyType({"name": "bravo"})}),
            )
        }
    )

    assert tuple(item.name for item in root.objects("items")) == ("alpha", "bravo")


@pytest.mark.parametrize(
    "items",
    (
        "not-an-array",
        b"not-an-array",
        (1,),
        (MappingProxyType({"metadata": MappingProxyType({"name": "alpha"})}), "bad"),
    ),
)
def test_kubernetes_object_rejects_non_object_array_items(items: object) -> None:
    """Reject malformed Kubernetes object arrays with the stable item reason.

    :param items: Deliberately malformed provider value for the ``items`` field.
    :returns: None; assertions validate the stable boundary classification.
    """
    root = KUBERNETES_OBJECTS.KubernetesObject({"items": items})

    with pytest.raises(TypeError, match="invalid_item_response"):
        root.objects("items")


def test_kubernetes_apply_serializes_nested_immutable_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Copy immutable manifest containers before writing the private apply file.

    :param monkeypatch: Pytest fixture replacing the native Kubernetes command.
    :returns: None; assertions validate serialization and temporary-file cleanup.
    """
    service = authenticated_kubernetes_service(_kubernetes_target())
    captured: MutableMapping[str, Union[str, bool]] = {}

    def command(*args: str, openshift: bool = False) -> CommandResult:
        """Capture the manifest path and return a provider object.

        :param *args: Native apply arguments supplied by ``KubernetesService``.
        :param openshift: Whether the OpenShift client was requested.
        :returns: Successful apply result containing a minimal object.
        """
        assert openshift is False
        manifest = Path(args[args.index("-f") + 1])
        captured["exists_during_command"] = manifest.exists()
        captured["manifest_path"] = str(manifest)
        captured["payload"] = manifest.read_text(encoding="utf-8")
        return _command_result('{"metadata":{"name":"unit"}}')

    document: JSONObject = MappingProxyType(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": MappingProxyType(
                {
                    "name": "unit",
                    "labels": MappingProxyType({"app": "boundary"}),
                }
            ),
            "data": MappingProxyType({"items": ("one", "two")}),
        }
    )
    monkeypatch.setattr(service, "command", command)

    result = service.apply(document)
    payload_text = captured["payload"]
    manifest_path = captured["manifest_path"]
    assert isinstance(payload_text, str)
    assert isinstance(manifest_path, str)

    decoded = JSON_TYPES.json_object(CLI_API.response_decoder().decode(payload_text))
    assert decoded["metadata"] == {"labels": {"app": "boundary"}, "name": "unit"}
    assert decoded["data"] == {"items": ["one", "two"]}
    assert result == {"metadata": {"name": "unit"}}
    assert captured["exists_during_command"] is True
    assert not Path(manifest_path).exists()


def test_kubernetes_read_returns_none_for_empty_successful_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep absent resources distinct from empty successful apply responses.

    :param monkeypatch: Pytest fixture replacing the native Kubernetes command.
    :returns: None; assertions validate ignored empty read-back output.
    """
    service = authenticated_kubernetes_service(_kubernetes_target())
    monkeypatch.setattr(service, "command", lambda *_args: _command_result(""))

    assert service.read("configmap", "unit", "default") is None


@pytest.mark.parametrize("outcome", ("success", "conflict", "replacement"))
def test_kubernetes_conditional_delete_transmits_uid_and_never_deletes_replacement(
    outcome: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delete the observed UID atomically and retain concurrent replacements.

    :param outcome: Successful absence, API precondition conflict, or replacement read-back.
    :param monkeypatch: Replace only native command and read-back provider boundaries.
    """
    service = authenticated_kubernetes_service(_kubernetes_target())
    deletes: list[tuple[tuple[str, ...], Optional[bytes]]] = []
    reads: list[tuple[str, str, str]] = []

    def command(
        *args: str,
        openshift: bool = False,
        input_data: Optional[bytes] = None,
        timeout: Optional[float] = None,
    ) -> CommandResult:
        """Expose native discovery and enforce the API UID precondition.

        :param *args: Discovery or raw delete arguments sent to the native client.
        :param openshift: Whether the OpenShift client was selected.
        :param input_data: Native DeleteOptions sent privately through stdin.
        :param timeout: Optional remaining operation bound.
        :returns: Discovery, successful deletion, or terminal precondition conflict.
        """
        assert openshift is False
        assert timeout is None or timeout > 0
        if args[0] == "get":
            assert "--raw" in args
            assert "/api/v1" in args
            assert input_data is None
            return _command_result(
                json.dumps(
                    {
                        "resources": [
                            {
                                "name": "pods",
                                "kind": "Pod",
                                "namespaced": True,
                                "verbs": ["get", "delete"],
                            }
                        ]
                    }
                )
            )
        assert args[0] == "delete"
        deletes.append((args, input_data))
        assert len(deletes) == 1
        assert args == (
            "delete",
            "--raw",
            "/api/v1/namespaces/storage/pods/reader",
            "-f",
            "-",
        )
        assert json.loads(input_data) == {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": "observed-uid"},
            "propagationPolicy": "Foreground",
        }
        if outcome == "conflict":
            return _command_result(
                "", returncode=1, stderr="409 Conflict: UID precondition failed"
            )
        return _command_result('{"status":"Success"}')

    def read(
        kind: str, name: str, namespace: str, *, timeout: Optional[float] = None
    ) -> Optional[JSONObject]:
        """Return the original Pod before deletion and selected independent read-back.

        :param kind: Kind selected for the conditional operation.
        :param name: Exact selected resource name.
        :param namespace: Original selected namespace.
        :param timeout: Optional remaining provider command bound.
        :returns: Original UID, replacement UID, or independently observed absence.
        """
        assert (kind.casefold(), name, namespace) == ("pod", "reader", "storage")
        assert timeout is None or timeout > 0
        reads.append((kind, name, namespace))
        assert len(reads) <= 2
        if not deletes:
            return {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": name,
                    "namespace": namespace,
                    "uid": "observed-uid",
                },
            }
        return (
            {"metadata": {"uid": "replacement-uid"}}
            if outcome == "replacement"
            else None
        )

    monkeypatch.setattr(service, "command", command)
    monkeypatch.setattr(service, "read", read)
    if outcome == "success":
        service.delete("Pod", "reader", "storage", expected_uid="observed-uid")
        assert reads
    else:
        with pytest.raises(KUBERNETES.KubernetesError):
            service.delete("Pod", "reader", "storage", expected_uid="observed-uid")
    assert len(deletes) == 1


@pytest.mark.parametrize(
    ("operation", "stdout", "returncode", "stderr", "reason"),
    (
        ("apply", "", 0, "", "invalid_json"),
        ("read", "", 1, "", "command_failed"),
        ("apply", "", 1, "", "command_failed"),
        ("read", "{", 0, "", "invalid_json"),
        ("apply", '{"value": NaN}', 0, "", "invalid_json"),
        ("read", '{"value": 1, "value": 2}', 0, "", "invalid_json"),
        ("read", "[]", 0, "", "invalid_object_response"),
        ("apply", "null", 0, "", "invalid_object_response"),
        ("apply", '"text"', 0, "", "invalid_object_response"),
    ),
)
def test_kubernetes_read_and_apply_classify_invalid_provider_responses(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    stdout: str,
    returncode: int,
    stderr: str,
    reason: str,
) -> None:
    """Classify malformed native response shapes without leaking provider text.

    :param monkeypatch: Pytest fixture replacing the native Kubernetes command.
    :param operation: Service method exercised by this case.
    :param stdout: Native standard output returned by the fake command.
    :param returncode: Native exit status returned by the fake command.
    :param stderr: Native standard error returned by the fake command.
    :param reason: Stable ``KubernetesError`` reason expected by the test.
    :returns: None; assertions validate the shared object-response boundary.
    """
    service = authenticated_kubernetes_service(_kubernetes_target())
    monkeypatch.setattr(
        service,
        "command",
        lambda *_args: _command_result(stdout, returncode=returncode, stderr=stderr),
    )

    with pytest.raises(KUBERNETES.KubernetesError, match=reason):
        if operation == "read":
            service.read("configmap", "unit", "default")
        else:
            service.apply(
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {"name": "unit"},
                }
            )


def test_token_source_exports_selected_file_with_empty_clear_set(
    tmp_path: Path,
) -> None:
    """Export a selected file credential even when no ambient names are cleared.

    :param tmp_path: Scratch directory receiving the synthetic token file.
    :returns: None; assertions validate selected-token environment binding.
    """
    token_file = tmp_path / "selected.token"
    token_file.write_text("selected-token\n", encoding="utf-8")

    source = ACCESS_SOURCES._token_source(
        token_file, ("SELECTED_TOKEN",), "native-store", clear_names=()
    )

    assert source.reference == f"file:{token_file.resolve()}"
    assert source.environment == {"SELECTED_TOKEN": "selected-token"}


def test_token_source_keeps_selected_environment_when_clearing_disjoint_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Export the chosen environment credential while clearing another name.

    :param monkeypatch: Pytest fixture setting the selected environment token.
    :returns: None; assertions validate selected and cleared variable handling.
    """
    monkeypatch.setenv("SELECTED_TOKEN", "selected-token")

    source = ACCESS_SOURCES._token_source(
        None, ("SELECTED_TOKEN",), "native-store", clear_names=("OTHER_TOKEN",)
    )

    assert source.reference == "env:SELECTED_TOKEN"
    assert source.environment == {
        "OTHER_TOKEN": None,
        "SELECTED_TOKEN": "selected-token",
    }


def test_token_source_native_store_exports_no_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave native credential-store selection credential-free.

    :param monkeypatch: Pytest fixture removing the accepted environment name.
    :returns: None; assertions validate the no-selection child environment.
    """
    monkeypatch.delenv("SELECTED_TOKEN", raising=False)

    source = ACCESS_SOURCES._token_source(
        None, ("SELECTED_TOKEN",), "native-store", clear_names=("OTHER_TOKEN",)
    )

    assert source.reference == "native-store"
    assert source.environment == {"OTHER_TOKEN": None}


def test_log_event_derives_error_level_and_rejects_empty_explicit_level(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Derive severity from status while rejecting an empty explicit level.

    :param capsys: Pytest fixture capturing diagnostic output.
    :returns: None; assertions validate logging severity behavior.
    """
    args = _log_args()

    LOGGING.log_event(
        args, "boundary", "result", STATUS.BLOCKED, level=None, persist=False
    )
    record = JSON_TYPES.json_object(
        CLI_API.response_decoder().decode(capsys.readouterr().err)
    )

    assert record["level"] == "error"
    with pytest.raises(KeyError):
        LOGGING.log_event(
            args, "boundary", "result", STATUS.PASS, level="", persist=False
        )


def test_load_target_reports_malformed_https_authority(tmp_path: Path) -> None:
    """Translate malformed URL authority parsing into ``TargetError``.

    :param tmp_path: Scratch directory receiving the invalid target file.
    :returns: None; assertions validate the public target error boundary.
    """
    target_file = tmp_path / "target.toml"
    target_file.write_text('[gitlab]\nurl = "https://["\n', encoding="utf-8")

    with pytest.raises(TARGET.TargetError, match="invalid authority"):
        TARGET.load_target(target_file, required_surfaces=("gitlab",))


@pytest.mark.parametrize(
    ("failure_factory", "expected_diagnostic", "expected_exit"),
    (
        (
            lambda: READ_TOOLS.ReadToolError("transport", READ_TOOLS.ReadExit.BLOCKED),
            "transport",
            READ_TOOLS.ReadExit.BLOCKED,
        ),
        (
            lambda: CLI_API.APIError("transport"),
            "CONNECTIVITY_FAILED",
            READ_TOOLS.ReadExit.BLOCKED,
        ),
    ),
)
def test_read_cli_maps_only_api_error_classifications(
    capsys: pytest.CaptureFixture[str],
    failure_factory: ReadFailureFactory,
    expected_diagnostic: str,
    expected_exit: int,
) -> None:
    """Keep literal tool errors distinct from mapped API classifications.

    :param capsys: Pytest fixture capturing the adapter diagnostic stream.
    :param failure_factory: Factory returning the exception raised by the operation.
    :param expected_diagnostic: Public diagnostic expected after classification.
    :param expected_exit: Exit class returned by the shared output adapter.
    :returns: None; assertions validate error mapping and log classification.
    """
    args = _api_output_args()

    def operation() -> NoReturn:
        """Raise the selected read-command failure.

        :raises APIError: If the factory selects a classified provider failure.
        :raises ReadToolError: If the factory selects a literal read-tool failure.
        """  # noqa: DOC503 - The selected failure is the subject of the test.
        raise failure_factory()

    exit_code = READ_CLI._output(args, "bin/ci-api", operation, READ_CLI.ApiOutput.JSON)
    stderr = capsys.readouterr().err
    log_line, diagnostic_line = stderr.splitlines()
    record = JSON_TYPES.json_object(CLI_API.response_decoder().decode(log_line))

    assert exit_code == expected_exit
    assert record["error_class"] == "transport"
    assert diagnostic_line == f"ci-api: {expected_diagnostic}"


def test_action_plan_public_uses_canonical_body_digest() -> None:
    """Digest immutable action bodies through the shared canonical JSON encoder.

    :returns: None; assertions validate public body and plan fingerprints.
    """
    body: JSONObject = MappingProxyType(
        {
            "tag_list": ("linux", "k8s"),
            "metadata": MappingProxyType({"locked": False}),
        }
    )
    plan = ACTION.ActionPlan(
        kind="gitlab_runner",
        operation="create",
        origin="https://gitlab.example.test",
        target_kind="project",
        target_reference="team/repo",
        target_file="/selected/target.toml",
        target_source="env:CI_SKILLS_TARGET",
        body=body,
        revision="a" * 40,
    )

    public = plan.public()

    assert public["body_sha256"] == REPORT.plan_digest(body)
    assert public["plan_digest"] == plan.digest
    assert public["requested_tags"] == ["linux", "k8s"]
