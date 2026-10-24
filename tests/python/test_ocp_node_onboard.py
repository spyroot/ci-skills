"""Exercise native ISO command inputs, planning and confirmation boundaries.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, MutableSequence, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import pytest
from conftest import configure_kubernetes_auth, import_script_module
from test_artifact_storage import FilePodCluster
from test_tool_pod import MemoryCluster

CLI = import_script_module("lib.python.core.kubernetes.ocp_onboard")
INVOCATION = import_script_module("lib.python.core.kubernetes.ocp_invocation")
DISCOVERY = import_script_module("lib.python.core.kubernetes.ocp_boot_image")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
PLAN = import_script_module("lib.python.core.report")
BUILD_WORKFLOW = import_script_module("lib.python.core.kubernetes.ocp_build_workflow")
RUNTIME = import_script_module("lib.python.core.runtime")


if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject


@dataclass(frozen=True)
class CallerInputs:
    """Keep independently editable caller files for plan-drift cases."""

    target: Path
    native: Path
    destination: Path
    tools: Path

    def argv(self, *extra: str) -> list[str]:
        """Compose a real command invocation using only caller-selected paths.

        :param *extra: Additional mode or native command options.
        :returns: Build arguments ready for the production parser.
        """
        return [
            "build",
            "--spec",
            str(self.native),
            "--destination",
            str(self.destination),
            "--json",
            *extra,
        ]


@pytest.fixture
def caller_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CallerInputs:
    """Provide only Kubernetes configuration and opaque caller-native bytes.

    :param tmp_path: Isolated directory containing the caller's inputs.
    :param monkeypatch: Select only this test's target through the public environment.
    :returns: Concrete input paths with no GitLab, Harbor or GitHub configuration.
    """
    target = tmp_path / "target.toml"
    target.write_text(
        '[kubernetes]\ncontext = "unit-context"\n'
        'server = "https://api.cluster.example.test:6443"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CI_SKILLS_TARGET", str(target))
    monkeypatch.delenv("K8S_ADMIN_DIAGNOSTICS_BINDING", raising=False)
    native = tmp_path / "caller-nodes.yaml"
    native.write_bytes(
        b"# native bytes remain opaque\r\nfuture-field: [invalid\x00\xff"
    )
    destination = tmp_path / "destination.json"
    destination.write_text(
        json.dumps(
            {
                "namespace": "consumer-storage",
                "volumeSource": {"persistentVolumeClaim": {"claimName": "caller-data"}},
                "mountPath": "/shared",
                "artifactPath": "/shared/requested/node.iso",
                "buildOutput": "scratch",
            }
        ),
        encoding="utf-8",
    )
    tools = tmp_path / "tools.json"
    tools.write_text(
        json.dumps(
            {
                "image": "registry.example.test/caller-tools@sha256:" + "c" * 64,
                "dependencies": [],
            }
        ),
        encoding="utf-8",
    )
    return CallerInputs(target, native, destination, tools)


class PlanningCluster(MemoryCluster):
    """Provide only finite discovery reads and record any unintended mutations."""

    def __init__(self) -> None:
        """Initialize published identities independently from implementation helpers."""
        super().__init__()
        self.timeout = 30
        self.inspector_digest = "d" * 64
        self.reads: MutableSequence[tuple[str, ...]] = []

    def read(self, kind: str, name: str, namespace: str) -> Optional[dict[str, object]]:
        """Return a native ClusterVersion object or the inherited object read.

        :param kind: Requested provider resource kind.
        :param name: Requested native object name.
        :param namespace: Requested scope, empty for the ClusterVersion.
        :returns: Controlled discovery object or independently stored resource.
        """
        self.reads.append((kind, name, namespace))
        if (kind, name, namespace) == ("clusterversion", "version", ""):
            return {
                "status": {
                    "desired": {
                        "version": "4.19.26",
                        "image": "registry.example.test/release@sha256:" + "e" * 64,
                    }
                }
            }
        return super().read(kind, name, namespace)

    def json(self, *args: str, openshift: bool = False) -> dict[str, object]:
        """Return provider image identities without invoking a native process.

        :param *args: Exact image-info request arguments.
        :param openshift: Native OpenShift transport selection.
        :returns: Image platform and immutable published digest.
        """
        self.reads.append(tuple(args))
        assert openshift
        assert args[:2] == ("image", "info")
        return {
            "config": {"os": "linux", "architecture": "amd64"},
            "digest": "sha256:" + self.inspector_digest,
        }


def bind_provider(monkeypatch: pytest.MonkeyPatch, cluster: PlanningCluster) -> None:
    """Replace credential/provider boundaries while retaining actual CLI composition.

    :param monkeypatch: Pytest restoration of narrow provider boundary doubles.
    :param cluster: Finite provider responses and mutation journal.
    """

    def publication(url: str, timeout: int) -> str:
        assert url.endswith("/4.19.26/sha256sum.txt")
        assert timeout > 0
        return "a" * 64 + "  openshift-client-linux-amd64-rhel9-4.19.26.tar.gz\n"

    monkeypatch.setattr(CLI, "bind_sources", lambda target, **_kwargs: target)
    configure_kubernetes_auth(monkeypatch, cluster)
    monkeypatch.setattr(DISCOVERY, "_published_text", publication)


def run_cli(
    argv: Sequence[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, object]]:
    """Decode the actual command output instead of constructing a report fixture.

    :param argv: Real parser arguments including JSON output selection.
    :param capsys: Pytest collection of the command's stdout and stderr.
    :returns: Actual command exit code and decoded stdout object.
    """
    code = CLI.main(argv)
    captured = capsys.readouterr()
    return code, json.loads(captured.out)


def test_help_exposes_native_inputs_and_cluster_destination_without_target_access(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An installed caller can discover the required inputs before configuring access."""

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("help attempted target or credential resolution")

    monkeypatch.setattr(CLI, "resolve_target", forbidden)
    monkeypatch.setattr(CLI, "bind_sources", forbidden)
    with pytest.raises(SystemExit) as result:
        CLI.main(("--help",))

    assert result.value.code == 0
    output = capsys.readouterr().out
    assert all(
        option in output
        for option in ("--spec", "--mac-address", "--destination", "--tool-pod-spec")
    )


@pytest.mark.parametrize("override", (False, True))
def test_offline_build_preserves_opaque_input_and_never_reads_a_provider(
    override: bool,
    caller_inputs: CallerInputs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A Kubernetes-only user can plan with native bytes and optional caller tools."""

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("offline ISO planning reached credentials or a provider")

    monkeypatch.setattr(CLI, "bind_sources", forbidden)
    monkeypatch.setattr(KUBERNETES.K8SAuth, "read_config", forbidden)
    monkeypatch.setattr(KUBERNETES.KubernetesService, "command", forbidden)
    monkeypatch.setattr(DISCOVERY, "_published_text", forbidden)
    extra = ("--tool-pod-spec", str(caller_inputs.tools)) if override else ()

    code, result = run_cli(caller_inputs.argv(*extra), capsys)

    assert code == 0
    assert result["status"] == "DRY_RUN"
    assert result["records"] == []
    inputs = result["plan"]["inputs"]
    content = caller_inputs.native.read_bytes()
    assert inputs["node_input"]["route"] == "file"
    assert inputs["node_input"]["files"] == [
        {
            "name": "nodes-config.yaml",
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    ]
    assert inputs["destination"] == json.loads(caller_inputs.destination.read_text())
    assert inputs["tool_source"] == ("caller" if override else "provider")
    assert inputs["tool_pod"] == (
        json.loads(caller_inputs.tools.read_text()) if override else {}
    )
    assert "native bytes remain opaque" not in json.dumps(result)


def test_mac_and_network_route_passes_exact_native_values_through_the_parser(
    caller_inputs: CallerInputs,
    tmp_path: Path,
) -> None:
    """The wrapper accepts native MAC text and does not parse the network YAML."""
    network = tmp_path / "caller-network.yaml"
    content = b"not: [our schema\x00\xff\r\n"
    network.write_bytes(content)
    args = CLI.build_parser().parse_args(
        [
            "build",
            "--mac-address",
            "native-future-value",
            "--network-config-path",
            str(network),
            "--destination",
            str(caller_inputs.destination),
        ]
    )

    inputs = INVOCATION.NodeBuildInputs.read(args)

    assert inputs.node.mac_address == "native-future-value"
    assert inputs.node.arguments() == (
        "--mac-address",
        "native-future-value",
        "--network-config-path",
        "network-config.yaml",
    )
    assert inputs.node.files[0].content == content
    assert inputs.tools is None


def test_parser_refuses_simultaneous_native_file_and_mac_routes() -> None:
    """Ambiguous native invocation shape is rejected before any files are opened."""
    with pytest.raises(SystemExit) as error:
        CLI.build_parser().parse_args(
            ["build", "--spec", "unopened.yaml", "--mac-address", "caller-mac"]
        )
    assert error.value.code == 2


def test_build_requires_the_caller_to_choose_a_destination(
    caller_inputs: CallerInputs, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing storage is reported without inventing a laptop or cluster path."""
    code, result = run_cli(
        [
            "build",
            "--spec",
            str(caller_inputs.native),
            "--json",
        ],
        capsys,
    )

    assert code == 2
    assert result["status"] == "BLOCKED"
    assert result["errors"][0]["reason"].startswith("build_requires_destination:")
    assert result["records"] == []
    assert result["plan"] == {}


def test_live_plan_resolves_published_tools_without_creating_any_resources(
    caller_inputs: CallerInputs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Live planning binds original inputs before redacting its mutation-free report."""
    cluster = PlanningCluster()
    bind_provider(monkeypatch, cluster)
    digests: MutableSequence[str] = []
    emit_result = CLI.emit_result

    def capture_plan(data: JSONObject, args: argparse.Namespace) -> int:
        """Observe the original plan while retaining the real report writer.

        :param data: Actual command report before display redaction.
        :param args: Caller-selected output and receipt settings.
        :returns: Real report writer's command status.
        """
        plan = data["plan"]
        assert isinstance(plan, Mapping)
        inputs = plan["inputs"]
        assert isinstance(inputs, Mapping)
        assert isinstance(inputs["pull_secret"], Mapping)
        digests.append(PLAN.plan_digest(plan))
        return emit_result(data, args)

    monkeypatch.setattr(CLI, "emit_result", capture_plan)
    code, result = run_cli(caller_inputs.argv("--live-plan"), capsys)

    assert code == 0
    assert result["status"] == "PLANNED"
    assert cluster.reads
    assert cluster.applied == cluster.deleted == cluster.executed == []
    inputs = result["plan"]["inputs"]
    assert inputs["release"]["source"] == "provider"
    assert inputs["tool_pod"]["image"].endswith("@sha256:" + "d" * 64)
    assert inputs["node_selector"] == {"kubernetes.io/arch": "amd64"}
    assert inputs["pull_secret"] == "[REDACTED]"
    assert digests == [result["plan_digest"]]


def test_caller_tool_override_avoids_provider_release_and_publication_discovery(
    caller_inputs: CallerInputs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An explicit existing tool specification remains exact and needs no publication lookup."""
    cluster = PlanningCluster()
    bind_provider(monkeypatch, cluster)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("caller tools triggered default publication discovery")

    monkeypatch.setattr(DISCOVERY, "_published_text", forbidden)
    code, result = run_cli(
        caller_inputs.argv("--live-plan", "--tool-pod-spec", str(caller_inputs.tools)),
        capsys,
    )

    assert code == 0
    assert result["status"] == "PLANNED"
    assert result["plan"]["inputs"]["release"]["source"] == "caller"
    assert result["plan"]["inputs"]["tool_pod"] == json.loads(
        caller_inputs.tools.read_text()
    )
    assert cluster.reads == cluster.applied == cluster.deleted == cluster.executed == []


@pytest.mark.parametrize(
    "change", ("native", "destination", "resolved-tools", "target", "run-id")
)
def test_changed_live_plan_inputs_invalidate_confirmation_before_any_build_callback(
    change: str,
    caller_inputs: CallerInputs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A previous confirmation cannot authorize different bytes, tools or target."""
    cluster = PlanningCluster()
    bind_provider(monkeypatch, cluster)
    code, planned = run_cli(
        caller_inputs.argv("--live-plan", "--run-id", "first-run"), capsys
    )
    assert code == 0
    extra = ["--run-id", "first-run"]
    if change == "native":
        caller_inputs.native.write_bytes(caller_inputs.native.read_bytes() + b"changed")
    elif change == "destination":
        selected = json.loads(caller_inputs.destination.read_text())
        selected["artifactPath"] = "/shared/caller-selected-other.iso"
        caller_inputs.destination.write_text(json.dumps(selected), encoding="utf-8")
    elif change == "resolved-tools":
        cluster.inspector_digest = "b" * 64
    elif change == "target":
        caller_inputs.target.write_text(
            caller_inputs.target.read_text().replace(
                "api.cluster.example.test", "api.other.example.test"
            ),
            encoding="utf-8",
        )
    else:
        extra = ["--run-id", "second-run"]

    def forbidden_build(*_args: object, **_kwargs: object) -> None:
        pytest.fail("stale confirmation constructed a build callback collection")

    monkeypatch.setattr(CLI, "build_callbacks", forbidden_build)
    code, rejected = run_cli(
        caller_inputs.argv(*extra, "--apply", "--confirm-plan", planned["plan_digest"]),
        capsys,
    )

    assert code == 2
    assert rejected["status"] == "BLOCKED"
    assert rejected["errors"][0]["reason"] == (
        "kubernetes_runtime_authority_mismatch"
        if change == "target"
        else "confirm_plan_mismatch_rerun_plan"
    )
    if change == "target":
        assert rejected["plan"] == {}
        assert "plan_digest" not in rejected
    else:
        assert rejected["plan_digest"] != planned["plan_digest"]
    assert cluster.applied == cluster.deleted == cluster.executed == []


@pytest.mark.parametrize("confirmation", ("", "0" * 64), ids=("missing", "incorrect"))
def test_unconfirmed_apply_never_constructs_mutating_callbacks(
    confirmation: str,
    caller_inputs: CallerInputs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Neither absence nor an unrelated digest authorizes the build composition."""
    cluster = PlanningCluster()
    bind_provider(monkeypatch, cluster)

    def forbidden_build(*_args: object, **_kwargs: object) -> None:
        pytest.fail("unconfirmed apply constructed a build callback collection")

    monkeypatch.setattr(CLI, "build_callbacks", forbidden_build)
    extra = ("--confirm-plan", confirmation) if confirmation else ()

    code, result = run_cli(caller_inputs.argv("--apply", *extra), capsys)

    assert code == 2
    assert result["errors"][0]["reason"] == "confirm_plan_mismatch_rerun_plan"
    assert cluster.applied == cluster.deleted == cluster.executed == []


class NativeLifecycleCluster(FilePodCluster, PlanningCluster):
    """Use real Pod file operations with finite native transport responses."""

    def __init__(self, mount: Path) -> None:
        """Bind storage to the test directory and initialize native call evidence.

        :param mount: Test-owned directory representing the caller's mounted volume.
        """
        super().__init__()
        self.mount = mount
        self.identity = "unit-user"
        self.native_timeout = False
        self.native_calls = 0
        self.native_status_path = Path()
        self.apply({"kind": "Namespace", "metadata": {"name": "consumer-storage"}})
        self.apply(
            {
                "kind": "Secret",
                "metadata": {"name": "pull-secret", "namespace": "openshift-config"},
                "data": {".dockerconfigjson": "e30="},
            }
        )

    def command(self, *argv: str) -> RUNTIME.CommandResult:
        """Export a fixture inline credential at the existing native CLI boundary.

        :param *argv: Exact flattened kubeconfig request.
        :returns: A deterministic private kubeconfig without real credentials.
        """
        assert argv == (
            "config",
            "view",
            "--raw",
            "--minify",
            "--flatten",
            "-o",
            "json",
        )
        return RUNTIME.CommandResult(
            tuple(argv), 0, '{"users":[{"user":{"token":"fixture"}}]}', ""
        )

    def json(self, *argv: str, openshift: bool = False) -> dict[str, object]:
        """Retain both native image discovery and real labeled object lookup.

        :param *argv: Existing discovery or resource-list arguments.
        :param openshift: Select the native OpenShift discovery transport.
        :returns: Deterministic native response from the existing fixture helper.
        """
        if argv[0] == "get":
            return MemoryCluster.json(self, *argv)
        return PlanningCluster.json(self, *argv, openshift=openshift)

    def pod_exec(
        self,
        namespace: str,
        name: str,
        container: str,
        *argv: str,
        input_data: Optional[bytes] = None,
    ) -> RUNTIME.CommandResult:
        """Replace the external cluster/native process and run installed file logic.

        :param namespace: Exact fixture namespace selected by the command.
        :param name: Exact prepared Pod name.
        :param container: Selected prepared container.
        :param *argv: Installed script operation and literal arguments.
        :param input_data: Optional private stdin for file staging or removal.
        :returns: Controlled native response or real installed filesystem result.
        """
        label = argv[3] if len(argv) > 3 else ""
        if label == "artifact-storage" and argv[4] == "mount-info":
            self.executed.append(argv)
            return RUNTIME.CommandResult(
                tuple(argv),
                0,
                json.dumps(
                    {
                        "filesystems": [
                            {
                                "target": str(self.mount),
                                "fstype": "nfs4",
                                "source": "fixture:/data",
                            }
                        ]
                    }
                ),
                "",
            )
        if label == "pod-credential":
            self.executed.append(argv)
            output = (
                '{"status":{"userInfo":{"username":"unit-user"}}}'
                if "whoami" in argv
                else ""
            )
            return RUNTIME.CommandResult(tuple(argv), 0, output, "")
        if label == "ocp-node-image" and argv[4] == "run":
            self.executed.append(argv)
            self.native_calls += 1
            Path(argv[5], argv[6]).write_bytes(b"fixture node ISO")
            self.native_status_path = Path(argv[9])
            if self.native_timeout:
                return RUNTIME.CommandResult(
                    tuple(argv), 124, "", "fixture transport timeout"
                )
            self.native_status_path.write_text('{"exit_code":0}', encoding="utf-8")
            return RUNTIME.CommandResult(tuple(argv), 0, "", "")
        if label == "ocp-node-image" and argv[4] == "inspect":
            self.executed.append(argv)
            return RUNTIME.CommandResult(
                tuple(argv), 0, '{"ignition":{"version":"3.4.0"}}', ""
            )
        if label in ("artifact-storage", "pod-file", "ocp-node-image"):
            return FilePodCluster.pod_exec(
                self, namespace, name, container, *argv, input_data=input_data
            )
        assert input_data is None
        return MemoryCluster.pod_exec(self, namespace, name, container, *argv)


def lifecycle_inputs(
    caller_inputs: CallerInputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> NativeLifecycleCluster:
    """Select isolated paths while retaining the real build callback composition.

    :param caller_inputs: Existing caller-file fixture and Kubernetes-only target.
    :param tmp_path: Every in-Pod file operation remains under this test directory.
    :param monkeypatch: Restore provider doubles and the temporary work root.
    :returns: Cluster transport journal used by both command invocations.
    """
    selected = json.loads(caller_inputs.destination.read_text())
    selected.update(mountPath=str(tmp_path), artifactPath=str(tmp_path / "node.iso"))
    caller_inputs.destination.write_text(json.dumps(selected), encoding="utf-8")
    monkeypatch.setattr(BUILD_WORKFLOW, "WORK_DIRECTORY", tmp_path / "work")
    cluster = NativeLifecycleCluster(tmp_path)
    bind_provider(monkeypatch, cluster)
    return cluster


def test_same_run_and_input_rebuild_reads_verified_iso_without_native_rerun(
    caller_inputs: CallerInputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Repeating a successful build verifies the saved ISO and returns a no-op."""
    cluster = lifecycle_inputs(caller_inputs, tmp_path, monkeypatch)
    argv = caller_inputs.argv(
        "--tool-pod-spec", str(caller_inputs.tools), "--run-id", "repeat-build"
    )
    code, planned = run_cli([*argv, "--live-plan"], capsys)
    assert code == 0
    applied = [*argv, "--apply", "--confirm-plan", planned["plan_digest"]]
    first_code, first = run_cli(applied, capsys)
    assert first_code == 0, first
    assert cluster.native_calls == 1
    expected = (tmp_path / "node.iso").read_bytes()
    original = first["artifact"]

    second_code, second = run_cli(applied, capsys)

    assert second_code == 0, second
    assert second["status"] == "PASS"
    assert second["artifact"]["sha256"] == original["sha256"]
    assert any(record.get("action") == "NO_OP" for record in second["records"])
    assert cluster.native_calls == 1
    assert (tmp_path / "node.iso").read_bytes() == expected


def test_timed_out_native_run_is_removable_by_run_id_after_observed_completion(
    caller_inputs: CallerInputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A transport timeout retains ownership until native failure is read back."""
    cluster = lifecycle_inputs(caller_inputs, tmp_path, monkeypatch)
    cluster.native_timeout = True
    argv = caller_inputs.argv(
        "--tool-pod-spec", str(caller_inputs.tools), "--run-id", "timed-native"
    )
    code, planned = run_cli([*argv, "--live-plan"], capsys)
    assert code == 0
    code, interrupted = run_cli(
        [*argv, "--apply", "--confirm-plan", planned["plan_digest"]], capsys
    )
    assert code == 2
    assert interrupted["native_completion_unknown"] is True
    assert interrupted["cleanup"]["retained"]
    assert cluster.native_calls == 1
    cluster.native_status_path.write_text('{"exit_code":17}', encoding="utf-8")

    remove = ["remove", "--run-id", "timed-native", "--json"]
    planned_code, removal = run_cli([*remove, "--live-plan"], capsys)

    assert planned_code == 0, removal
    code, removed = run_cli(
        [*remove, "--apply", "--confirm-plan", removal["plan_digest"]], capsys
    )
    assert code == 0, removed
    assert removed["cleanup"]["status"] == "PASS"
    assert cluster.native_calls == 1
    assert not any(kind in ("pod", "configmap") for kind, _, _ in cluster.objects)
    assert not (tmp_path / "node.iso").exists()
