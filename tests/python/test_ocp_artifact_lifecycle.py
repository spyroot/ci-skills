"""Prove artifact lifetime and exact run ownership across ISO removal.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import pytest
import yaml
from conftest import import_script_module
from test_artifact_storage import (
    FilePodCluster,
    checked_destination,
    destination,
    source_artifact,
    storage_state,
)

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject

LIFECYCLE = import_script_module("lib.python.core.kubernetes.ocp_artifact_lifecycle")
RECORDS = import_script_module("lib.python.core.kubernetes.ocp_artifact_run")
MODELS = import_script_module("lib.python.core.kubernetes.ocp_artifact_models")
PODS = import_script_module("lib.python.core.kubernetes.tool_pod_models")
POD_LIFECYCLE = import_script_module("lib.python.core.kubernetes.tool_pod_lifecycle")
POD_RESOURCES = import_script_module("lib.python.core.kubernetes.tool_pod_resources")
SCOPES = import_script_module("lib.python.core.kubernetes.run_scope")
RESOURCES = import_script_module("lib.python.core.kubernetes.resources")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
CALLBACKS = import_script_module("lib.python.core.abstract.callback")
ISO = import_script_module("lib.python.core.kubernetes.ocp_iso")
ISO_VERIFY = import_script_module("lib.python.core.kubernetes.ocp_iso_verify")
ISO_BUILD = import_script_module("lib.python.core.kubernetes.ocp_iso_build")
NATIVE = import_script_module("lib.python.core.kubernetes.ocp_native_input")
COMMANDS = import_script_module("lib.python.core.runtime")
BUILD_WORKFLOW = import_script_module("lib.python.core.kubernetes.ocp_build_workflow")
ONBOARD = import_script_module("lib.python.core.kubernetes.ocp_onboard")
POD_FILES = import_script_module("lib.python.core.kubernetes.pod_files")


def test_native_failure_removes_exact_partial_output_and_report(
    tmp_path: Path,
) -> None:
    """A known native failure cannot leave its partial ISO on shared storage."""
    base, cluster = storage_state(tmp_path)
    state = MODELS.OCPArtifactState()
    state.kubernetes = cluster
    state.pod = base.pod
    state.run_scope = base.run_scope
    private = tmp_path / "private"
    private.mkdir()
    state.kubeconfig_file = POD_FILES.write_pod_file(
        state, PurePosixPath(private / "kubeconfig"), b"fixture kubeconfig"
    )
    state.pull_secret_file = POD_FILES.write_pod_file(
        state, PurePosixPath(private / "pull-secret"), b"{}"
    )
    tools = tmp_path / "bin"
    tools.mkdir()
    native = tools / "oc"
    native.write_text(
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do\n"
        "  case $1 in\n"
        "    --dir) directory=$2; shift 2 ;;\n"
        "    --output-name) output=$2; shift 2 ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        'printf partial > "$directory/$output"\n'
        'printf native-report > "$directory/report.json"\n'
        "exit 17\n",
        encoding="utf-8",
    )
    native.chmod(0o700)
    state.search_paths.append(str(tools))
    output = tmp_path / "selected.iso"
    report = tmp_path / "report.json"
    neighbor = tmp_path / "caller-model-data"
    neighbor.write_bytes(b"preserve shared data")
    callback = ISO_BUILD.BuildNodeImage(
        ISO.NodeImageAction(
            NATIVE.NativeNodeInput.read(mac_address="02:00:00:00:00:01"),
            str(output),
        )
    )
    callback.bind(state)

    with pytest.raises(KUBERNETES.KubernetesError, match="native_failed:exit=17"):
        callback.run()

    assert output.read_bytes() == b"partial"
    assert report.read_bytes() == b"native-report"
    callback.cleanup()

    assert not output.exists()
    assert not report.exists()
    assert not Path(state.native_status_path).exists()
    assert neighbor.read_bytes() == b"preserve shared data"


def test_final_iso_receipt_preserves_successful_cleanup_hook_evidence(
    tmp_path: Path,
) -> None:
    """The final report retains cleanup outcomes emitted by the real executor."""
    base, cluster = storage_state(tmp_path)
    state = MODELS.OCPArtifactState()
    state.kubernetes = cluster
    state.pod = base.pod
    state.run_scope = base.run_scope
    action = PODS.RemoveToolPodAction(state.run_scope.run_id)

    class CleanupEvidence(KUBERNETES.K8SCallback):
        """Supply distinct hook results without replacing executor or reporting."""

        def run(self) -> dict[str, object]:
            """Return an execution result distinct from cleanup evidence."""
            return {"action": "VERIFIED", "verified": True}

        def cleanup(self) -> dict[str, object]:
            """Return an observed exact private-file cleanup result."""
            return {
                "action": "APPLIED",
                "verified": True,
                "completion_record_removed": True,
            }

    callback = CleanupEvidence(action)
    BUILD_WORKFLOW.execute_callbacks(state, [callback])
    invocation = ONBOARD.NodeInvocation(
        ONBOARD.build_parser().parse_args(["remove", "--run-id", action.run_id])
    )
    invocation.state = state

    receipt = invocation.report("PASS", ())

    assert any(
        item.get("callback") == "CleanupEvidence"
        and item.get("hook") == "cleanup"
        and item.get("completion_record_removed") is True
        for item in receipt["records"]
    )


def invoke(
    callback: CALLBACKS.AbstractCallback, state: MODELS.OCPArtifactState
) -> dict[str, object]:
    """Run a concrete operation after the executor's normal shared-state binding.

    :param callback: Production lifecycle callback being exercised.
    :param state: Same artifact state shared across the ordered operations.
    :returns: Production callback result.
    """
    callback.bind(state)
    return callback.run()


def artifact_state(
    tmp_path: Path, *, temporary: bool = False
) -> tuple[MODELS.OCPArtifactState, FilePodCluster]:
    """Arrange real fixture bytes and provider objects for an already verified ISO.

    :param tmp_path: Test-owned volume and final artifact directory.
    :param temporary: Choose Pod-backed output instead of persistent storage.
    :returns: Artifact-aware state and reusable in-memory Kubernetes transport.
    """
    base, cluster = storage_state(tmp_path)
    state = MODELS.OCPArtifactState()
    state.kubernetes = cluster
    state.pod = base.pod
    state.run_scope = base.run_scope
    state.artifact_scope = base.run_scope
    selected = destination(tmp_path, temporary=temporary)
    checked_destination(state, selected)
    output = Path(selected.artifact_path)
    output.write_bytes(b"verified native ISO\x00\xff")
    state.stored_artifact = source_artifact(state, output)
    specification = PODS.ToolPodSpec.from_document(
        {"image": state.pod.image, "dependencies": []}
    )
    record = MODELS.NodeArtifactRecord(
        state.run_scope.run_id,
        selected,
        specification,
        (selected.tool_pod_mount(),),
        state.stored_artifact,
        state.storage_handle,
        (
            MODELS.ArtifactLifetime.POD
            if temporary
            else MODELS.ArtifactLifetime.CALLER_SELECTED_VOLUME
        ),
    )
    state.artifact_record = record
    state.artifact_record_present = True
    resources = state.run_scope.resources(cluster)
    producer_key = ("pod", state.pod.namespace, state.pod.name)
    producer = cluster.objects[producer_key]
    producer["metadata"]["labels"] = {
        **dict(state.run_scope.labels),
        RESOURCES.MANAGER_LABEL: RESOURCES.MANAGER_VALUE,
        RESOURCES.OPERATION_LABEL: state.run_scope.run_id,
    }
    resources.apply(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": state.run_scope.name(POD_RESOURCES.TOOL_POD_SCRIPT_ROLE),
                "labels": dict(state.run_scope.labels),
            },
            "data": {"hold.sh": "sleep"},
        }
    )
    applied = resources.apply(RECORDS.artifact_record_document(state.run_scope, record))
    state.artifact_record_uid = applied["uid"]
    return state, cluster


def select_reader(state: MODELS.OCPArtifactState, cluster: FilePodCluster) -> None:
    """Use the production reader plan with a distinct in-memory Pod identity.

    :param state: State containing a persistent artifact record.
    :param cluster: In-memory provider object store.
    """
    selected = LIFECYCLE.verification_pod_inputs(
        state.artifact_record, state.artifact_scope, timeout=30
    )
    assert not selected.reuses_build_pod
    name = selected.scope.name(POD_RESOURCES.TOOL_POD_ROLE)
    observed = cluster.apply(
        {
            "kind": "Pod",
            "metadata": {"name": name, "namespace": selected.scope.namespace},
        }
    )
    state.pod = PODS.PodHandle(
        selected.scope.namespace,
        name,
        observed["metadata"]["uid"],
        "ci-skills-pod",
        selected.preparation.spec.image,
        0,
    )


@pytest.fixture
def native_inspection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate native Ignition inspection while preserving real storage read-back.

    :param monkeypatch: Pytest automatic restoration of the unrelated native probe.
    """
    monkeypatch.setattr(
        LIFECYCLE,
        "inspect_verified_node_image",
        lambda _state, _artifact: {"native_inspection": "verified separately"},
    )


def test_persistent_artifact_survives_removal_of_only_original_run_objects(
    tmp_path: Path, native_inspection: None
) -> None:
    """Before/after reads through a separate Pod prove the caller's bytes survive."""
    state, cluster = artifact_state(tmp_path)
    record = state.artifact_record
    selected_path = Path(record.artifact.path)
    original_bytes = selected_path.read_bytes()
    neighbor = tmp_path / "model-data"
    neighbor.write_bytes(b"shared model")
    for kind, name in (
        ("PersistentVolumeClaim", "caller-model-data"),
        ("PersistentVolume", "caller-volume"),
        ("ConfigMap", "unrelated-run"),
        ("Namespace", "consumer-storage"),
    ):
        cluster.apply(
            {"kind": kind, "metadata": {"name": name, "namespace": "consumer-storage"}}
        )
    select_reader(state, cluster)
    reader = state.pod
    action = SCOPES.ResolveRunScopeAction(record.run_id)

    before = invoke(LIFECYCLE.VerifyArtifactBeforeRemoval(action), state)
    invoke(LIFECYCLE.SelectArtifactRunForRemoval(action), state)
    invoke(POD_LIFECYCLE.RemoveToolPod(PODS.RemoveToolPodAction(record.run_id)), state)
    after = invoke(LIFECYCLE.VerifyArtifactAfterRemoval(action), state)
    deleted = invoke(LIFECYCLE.DeleteNodeArtifactRecord(action), state)

    assert before["artifact"]["sha256"] == after["artifact"]["sha256"]
    assert after["build_pod_absent"] is True
    assert deleted["artifact_preserved"] is True
    assert selected_path.read_bytes() == original_bytes
    assert neighbor.read_bytes() == b"shared model"
    assert cluster.read("Pod", reader.name, reader.namespace) is not None
    assert cluster.deleted == [
        ("pod", record.artifact.pod.namespace, record.artifact.pod.name),
        (
            "configmap",
            state.artifact_scope.namespace,
            state.artifact_scope.name(POD_RESOURCES.TOOL_POD_SCRIPT_ROLE),
        ),
        (
            "configmap",
            state.artifact_scope.namespace,
            state.artifact_scope.name(RECORDS.ARTIFACT_RECORD_ROLE),
        ),
    ]


@pytest.mark.parametrize("mismatch", ("ownership", "uid", "content"))
def test_record_delete_rejects_replaced_or_changed_objects_before_any_deletion(
    mismatch: str, tmp_path: Path
) -> None:
    """A matching name never authorizes deleting another run or replacement record."""
    state, cluster = artifact_state(tmp_path)
    state.artifact_before_removal_verified = True
    state.artifact_after_removal_verified = True
    name = state.artifact_scope.name(RECORDS.ARTIFACT_RECORD_ROLE)
    document = cluster.objects[("configmap", state.artifact_scope.namespace, name)]
    if mismatch == "ownership":
        document["metadata"]["labels"][RESOURCES.OPERATION_LABEL] = "another-run"
    elif mismatch == "uid":
        document["metadata"]["uid"] = "replacement-uid"
    else:
        altered = replace(state.artifact_record.artifact, sha256="b" * 64)
        changed = replace(state.artifact_record, artifact=altered)
        document["data"] = RECORDS.artifact_record_document(
            state.artifact_scope, changed
        )["data"]

    with pytest.raises(KUBERNETES.KubernetesError, match="node_artifact_record_"):
        invoke(
            LIFECYCLE.DeleteNodeArtifactRecord(
                SCOPES.ResolveRunScopeAction(state.artifact_record.run_id)
            ),
            state,
        )

    assert cluster.deleted == []
    assert Path(state.artifact_record.artifact.path).is_file()
    assert state.artifact_record_present


def test_persistent_record_cannot_be_deleted_without_both_live_readbacks(
    tmp_path: Path,
) -> None:
    """A record is retained when post-removal artifact evidence has not been collected."""
    state, cluster = artifact_state(tmp_path)
    state.artifact_before_removal_verified = True

    with pytest.raises(
        KUBERNETES.KubernetesError, match="persistent_readback_required"
    ):
        invoke(
            LIFECYCLE.DeleteNodeArtifactRecord(
                SCOPES.ResolveRunScopeAction(state.artifact_record.run_id)
            ),
            state,
        )

    assert cluster.deleted == []
    assert state.artifact_record_present


def test_changed_final_bytes_after_producer_removal_are_rejected(
    tmp_path: Path, native_inspection: None
) -> None:
    """Post-removal proof re-reads final bytes rather than trusting the earlier report."""
    state, cluster = artifact_state(tmp_path)
    select_reader(state, cluster)
    action = SCOPES.ResolveRunScopeAction(state.artifact_record.run_id)
    invoke(LIFECYCLE.VerifyArtifactBeforeRemoval(action), state)
    invoke(LIFECYCLE.SelectArtifactRunForRemoval(action), state)
    invoke(POD_LIFECYCLE.RemoveToolPod(PODS.RemoveToolPodAction(action.run_id)), state)
    Path(state.artifact_record.artifact.path).write_bytes(b"changed after removal")

    with pytest.raises(KUBERNETES.KubernetesError, match="content_readback_mismatch"):
        invoke(LIFECYCLE.VerifyArtifactAfterRemoval(action), state)

    assert not state.artifact_after_removal_verified
    assert state.artifact_record_present


def test_temporary_output_reuses_original_pod_and_explicitly_ends_its_lifetime(
    tmp_path: Path,
) -> None:
    """Pod-backed output retains the producer until explicit removal proves absence."""
    state, cluster = artifact_state(tmp_path, temporary=True)
    record = state.artifact_record
    selected = LIFECYCLE.verification_pod_inputs(
        record, state.artifact_scope, timeout=30
    )
    assert selected.reuses_build_pod
    assert selected.scope == state.artifact_scope
    assert selected.preparation.retain
    assert selected.preparation.mounts == record.tool_mounts
    action = SCOPES.ResolveRunScopeAction(record.run_id)

    with pytest.raises(KUBERNETES.KubernetesError, match="build_pod_still_present"):
        invoke(LIFECYCLE.VerifyTemporaryBackingRemoved(action), state)
    invoke(LIFECYCLE.SelectArtifactRunForRemoval(action), state)
    invoke(POD_LIFECYCLE.RemoveToolPod(PODS.RemoveToolPodAction(record.run_id)), state)
    removed = invoke(LIFECYCLE.VerifyTemporaryBackingRemoved(action), state)
    deleted = invoke(LIFECYCLE.DeleteNodeArtifactRecord(action), state)

    assert removed["artifact_lifetime_ended"] is True
    assert removed["build_pod_absent"] is True
    assert deleted["artifact_preserved"] is False
    assert not state.artifact_record_present
    assert not any(
        kind in {"persistentvolumeclaim", "persistentvolume", "namespace"}
        for kind, _namespace, _name in cluster.deleted
    )


def test_repeat_remove_of_an_absent_run_is_noop_without_provider_deletes() -> None:
    """An absent labeled run can be removed again without claiming an artifact exists."""
    state = MODELS.OCPArtifactState()
    cluster = FilePodCluster()
    state.kubernetes = cluster
    action = SCOPES.ResolveRunScopeAction("absent-artifact-run")

    result = invoke(LIFECYCLE.DeleteNodeArtifactRecord(action), state)

    assert result["action"] == "NO_OP"
    assert result["record_absent"] is True
    assert cluster.deleted == []


@pytest.mark.parametrize("producer", (False, True), ids=("reader", "producer"))
def test_iso_inspection_cleans_reader_resources_but_retains_verified_producer(
    producer: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading an ISO must not acquire the producer's resource-retention lifetime.

    :param producer: Exercise build verification or a later read-only inspection.
    :param tmp_path: Test-owned artifact bytes and provider object fixture.
    :param monkeypatch: Replace only external coreos-installer execution.
    """
    state, cluster = artifact_state(tmp_path)
    artifact = state.stored_artifact
    preparation = PODS.PrepareToolPodAction(
        state.artifact_record.tool_spec,
        run_id=state.run_scope.run_id,
        namespace=state.run_scope.namespace,
        retain=False,
    )
    stage = POD_LIFECYCLE.StageToolPod(preparation)
    stage.bind(state)
    stage.resources = state.run_scope.resources(cluster)
    stage.resources.created.extend(
        (
            ("ConfigMap", state.run_scope.name(POD_RESOURCES.TOOL_POD_SCRIPT_ROLE)),
            ("Pod", state.pod.name),
        )
    )

    def native_inspect(
        selected: ISO.OCPState, operation: str, *arguments: str
    ) -> COMMANDS.CommandResult:
        """Supply native Ignition output through the external command boundary.

        :param selected: Shared state sent to native inspection.
        :param operation: Requested native operation.
        :param *arguments: Exact artifact path passed to coreos-installer.
        :returns: Successful native Ignition JSON output.
        """
        assert selected is state
        assert (operation, arguments) == ("inspect", (artifact.path,))
        return COMMANDS.CommandResult(
            ("coreos-installer", "iso", "ignition", "show", artifact.path),
            0,
            json.dumps(
                {
                    "ignition": {"version": "3.4.0"},
                    "storage": {
                        "files": [
                            _embedded_file(
                                "/etc/assisted/hostconfig/node/mac_addresses",
                                "02:00:00:00:00:01\n",
                            )
                        ]
                    },
                }
            ),
            "",
        )

    monkeypatch.setattr(ISO_VERIFY, "run_node_image_script", native_inspect)
    if producer:
        state.build_completed = True
        action = ISO.NodeImageAction(
            NATIVE.NativeNodeInput.read(mac_address="02:00:00:00:00:01"),
            artifact.path,
        )
        result = invoke(ISO_VERIFY.VerifyNodeImage(action), state)
        assert result["verified"] is True
    else:
        result = ISO_VERIFY.inspect_verified_node_image(state, artifact)

    assert result["artifact"] == artifact.as_dict()
    assert result["embedded_ignition"]["version"] == "3.4.0"
    cleanup = stage.cleanup()

    assert (
        cluster.read("Pod", state.pod.name, state.pod.namespace) is not None
    ) is producer
    script_name = state.run_scope.name(POD_RESOURCES.TOOL_POD_SCRIPT_ROLE)
    assert (
        cluster.read("ConfigMap", script_name, state.pod.namespace) is not None
    ) is producer
    assert len(cleanup["resources"]) == (0 if producer else 2)
    assert hasattr(state, "build_artifact") is producer
    assert hasattr(state, "source_artifact") is producer
    assert ("node_image_source" in state.readback) is producer
    if producer:
        assert state.build_artifact == artifact
        assert state.source_artifact == artifact
    assert Path(artifact.path).read_bytes() == b"verified native ISO\x00\xff"


def _embedded_file(path: str, content: str) -> JSONObject:
    """Encode one native provider file without replacing the production decoder."""
    return {
        "path": path,
        "contents": {
            "source": "data:;base64," + base64.b64encode(content.encode()).decode()
        },
    }


@pytest.fixture
def native_output() -> tuple[JSONObject, JSONObject]:
    """Provide two distinct native hosts and independently path-bound output."""
    hosts = []
    files = []
    manifests = []
    for index in (1, 2):
        mac = f"02:00:00:10:00:0{index}"
        hostname = f"worker-0{index}"
        interfaces = [{"name": "ens3", "macAddress": mac}]
        network = {
            "interfaces": [
                {
                    "name": "ens3",
                    "type": "ethernet",
                    "state": "up",
                    "mac-address": mac,
                    "mtu": 9000,
                    "ipv4": {"enabled": True, "dhcp": True},
                }
            ]
        }
        disk = {"deviceName": f"/dev/disk/by-id/disk-{index}"}
        hosts.append(
            {
                "hostname": hostname,
                "interfaces": interfaces,
                "networkConfig": network,
                "rootDeviceHints": disk,
            }
        )
        files.extend(
            (
                _embedded_file(
                    f"/etc/assisted/hostconfig/{hostname}/mac_addresses", mac
                ),
                _embedded_file(f"/etc/assisted/hostnames/{mac}", hostname),
                _embedded_file(
                    f"/etc/assisted/hostconfig/{hostname}/root-device-hints.yaml",
                    yaml.safe_dump(disk),
                ),
            )
        )
        manifests.append(
            {
                "apiVersion": "agent-install.openshift.io/v1beta1",
                "kind": "NMStateConfig",
                "metadata": {"name": hostname},
                "spec": {
                    "interfaces": deepcopy(interfaces),
                    "config": deepcopy(network),
                },
            }
        )
    files.append(
        _embedded_file(
            "/etc/assisted/manifests/nmstateconfig.yaml", yaml.safe_dump_all(manifests)
        )
    )
    return {"hosts": hosts}, {
        "ignition": {"version": "3.4.0"},
        "storage": {"files": files},
    }


@pytest.fixture
def verify_native_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[NATIVE.NativeNodeInput, JSONObject], JSONObject]:
    """Run the real build verifier against actual fixture ISO bytes and native output."""
    state, _cluster = artifact_state(tmp_path)
    state.build_completed = True

    def verify(node_input: NATIVE.NativeNodeInput, document: JSONObject) -> JSONObject:
        def inspect(
            selected: ISO.OCPState, operation: str, *arguments: str
        ) -> COMMANDS.CommandResult:
            assert selected is state
            assert (operation, arguments) == ("inspect", (state.stored_artifact.path,))
            return COMMANDS.CommandResult(
                ("coreos-installer", "iso", "ignition", "show", *arguments),
                0,
                json.dumps(document),
                "",
            )

        monkeypatch.setattr(ISO_VERIFY, "run_node_image_script", inspect)
        return invoke(
            ISO_VERIFY.VerifyNodeImage(
                ISO.NodeImageAction(node_input, state.stored_artifact.path)
            ),
            state,
        )

    return verify


def _caller_input(tmp_path: Path, document: JSONObject) -> NATIVE.NativeNodeInput:
    """Read exact caller-native YAML through the public input boundary."""
    path = tmp_path / "nodes-config.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return NATIVE.NativeNodeInput.read(spec=path)


@pytest.mark.parametrize("route", ("mac", "yaml"))
def test_native_build_verifies_supplied_values_for_the_correct_host(
    route: str,
    tmp_path: Path,
    native_output: tuple[JSONObject, JSONObject],
    verify_native_output: Callable[[NATIVE.NativeNodeInput, JSONObject], JSONObject],
) -> None:
    """Accept actual MAC or native YAML settings only when their output matches."""
    expected, output = native_output
    node_input = (
        NATIVE.NativeNodeInput.read(mac_address="02:00:00:10:00:01")
        if route == "mac"
        else _caller_input(tmp_path, expected)
    )
    result = verify_native_output(node_input, output)
    comparison = result["native_input_comparison"]
    assert result["verified"] is True
    assert comparison["status"] == "matched"
    assert comparison["artifact_sha256"] == result["artifact"]["sha256"]
    assert comparison["input_identity"] == node_input.evidence()
    assert comparison["unverified_fields"] == []
    assert comparison["matched_fields"]


def test_native_yaml_without_network_config_accepts_mac_and_reports_unobserved_name(
    tmp_path: Path,
    verify_native_output: Callable[[NATIVE.NativeNodeInput, JSONObject], JSONObject],
) -> None:
    """Native DHCP input has no NMState manifest from which to prove interface names."""
    expected = {
        "hosts": [{"interfaces": [{"name": "ens3", "macAddress": "02:00:00:10:00:01"}]}]
    }
    output = {
        "ignition": {"version": "3.4.0"},
        "storage": {
            "files": [
                _embedded_file(
                    "/etc/assisted/hostconfig/host-0/mac_addresses", "02:00:00:10:00:01"
                )
            ]
        },
    }
    node_input = _caller_input(tmp_path, expected)

    result = verify_native_output(node_input, output)

    comparison = result["native_input_comparison"]
    assert result["verified"] is True
    assert comparison["status"] == "unverified"
    assert comparison["input_identity"] == node_input.evidence()
    assert comparison["matched_fields"] == ["hosts[0].interfaces[0].macAddress"]
    assert comparison["unverified_fields"] == ["hosts[0].interfaces[0].name"]
    assert "mismatched_fields" not in comparison


def test_native_build_without_mac_association_reports_host_settings_unverified(
    tmp_path: Path,
    native_output: tuple[JSONObject, JSONObject],
    verify_native_output: Callable[[NATIVE.NativeNodeInput, JSONObject], JSONObject],
) -> None:
    """Unavailable host association cannot contradict a successful native build."""
    _expected, output = native_output
    requested = {
        "hosts": [
            {
                "hostname": "worker-01",
                "rootDeviceHints": {"deviceName": "/dev/disk/by-id/disk-1"},
                "role": "worker",
            }
        ]
    }
    output["storage"]["files"].append(
        _embedded_file("/etc/assisted/hostconfig/worker-01/role", "worker")
    )
    node_input = _caller_input(tmp_path, requested)

    result = verify_native_output(node_input, output)

    comparison = result["native_input_comparison"]
    assert result["verified"] is True
    assert comparison["status"] == "unverified"
    assert comparison["input_identity"] == node_input.evidence()
    assert comparison["matched_fields"] == []
    assert set(comparison["unverified_fields"]) == {
        "hosts[0].hostname",
        "hosts[0].rootDeviceHints",
        "hosts[0].role",
    }
    assert "mismatched_fields" not in comparison


def test_native_build_rejects_hostnames_swapped_between_mac_paths(
    tmp_path: Path,
    native_output: tuple[JSONObject, JSONObject],
    verify_native_output: Callable[[NATIVE.NativeNodeInput, JSONObject], JSONObject],
) -> None:
    """The expected names elsewhere in the ISO do not prove the right host binding."""
    expected, output = native_output
    files = output["storage"]["files"]
    files[1]["contents"], files[4]["contents"] = (
        files[4]["contents"],
        files[1]["contents"],
    )
    with pytest.raises(KUBERNETES.KubernetesError, match="native_input_mismatch"):
        verify_native_output(_caller_input(tmp_path, expected), output)


def test_mac_network_comparison_rejects_a_matching_network_on_another_host(
    tmp_path: Path,
    native_output: tuple[JSONObject, JSONObject],
    verify_native_output: Callable[[NATIVE.NativeNodeInput, JSONObject], JSONObject],
) -> None:
    """A MAC selection cannot borrow another host's correct network values."""
    expected, output = native_output
    selected_network = expected["hosts"][1]["networkConfig"]
    path = tmp_path / "network.yaml"
    path.write_text(yaml.safe_dump(selected_network), encoding="utf-8")
    node_input = NATIVE.NativeNodeInput.read(
        mac_address="02:00:00:10:00:01", network_config=path
    )
    with pytest.raises(KUBERNETES.KubernetesError, match="native_input_mismatch"):
        verify_native_output(node_input, output)


@pytest.mark.parametrize("defect", ("missing", "unreadable", "bool-as-integer"))
def test_native_build_rejects_missing_unreadable_or_wrongly_typed_requested_values(
    defect: str,
    tmp_path: Path,
    native_output: tuple[JSONObject, JSONObject],
    verify_native_output: Callable[[NATIVE.NativeNodeInput, JSONObject], JSONObject],
) -> None:
    """Requested native fields need readable output with the same native value types."""
    expected, output = native_output
    files = output["storage"]["files"]
    if defect == "missing":
        del files[2]
    elif defect == "unreadable":
        files[2]["contents"] = {"source": "https://example.test/private-disk-data"}
    else:
        documents = list(
            yaml.safe_load_all(
                base64.b64decode(files[-1]["contents"]["source"].split(",", 1)[1])
            )
        )
        documents[0]["spec"]["config"]["interfaces"][0]["ipv4"]["dhcp"] = 1
        files[-1] = _embedded_file(files[-1]["path"], yaml.safe_dump_all(documents))
    with pytest.raises(
        KUBERNETES.KubernetesError,
        match="native_(input_mismatch|output_comparison_unreadable)",
    ):
        verify_native_output(_caller_input(tmp_path, expected), output)


@pytest.mark.parametrize(
    "legacy", (False, True), ids=("compared-artifact", "legacy-record")
)
def test_standalone_verify_reuses_only_comparison_bound_to_unchanged_iso(
    legacy: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh artifact reads preserve matched proof while legacy records stay unverified."""
    state, _cluster = artifact_state(tmp_path)
    document = {
        "ignition": {"version": "3.4.0"},
        "storage": {
            "files": [
                _embedded_file(
                    "/etc/assisted/hostconfig/node/mac_addresses", "02:00:00:10:00:01"
                )
            ]
        },
    }
    inspections = []

    def inspect(
        _selected: ISO.OCPState, _operation: str, *arguments: str
    ) -> COMMANDS.CommandResult:
        inspections.append(arguments)
        return COMMANDS.CommandResult(
            ("inspect", *arguments), 0, json.dumps(document), ""
        )

    monkeypatch.setattr(ISO_VERIFY, "run_node_image_script", inspect)
    if not legacy:
        state.build_completed = True
        initial = invoke(
            ISO_VERIFY.VerifyNodeImage(
                ISO.NodeImageAction(
                    NATIVE.NativeNodeInput.read(mac_address="02:00:00:10:00:01"),
                    state.stored_artifact.path,
                )
            ),
            state,
        )["native_input_comparison"]
        record = replace(state.artifact_record, native_input_comparison=initial)
        state.artifact_record = MODELS.NodeArtifactRecord.from_document(
            record.as_dict()
        )
    action = SCOPES.ResolveRunScopeAction(state.artifact_record.run_id)
    result = invoke(LIFECYCLE.VerifyArtifactReference(action), state)
    comparison = result["native_input_comparison"]
    assert comparison["artifact_unchanged"] is True
    assert comparison["artifact_sha256"] == state.artifact_record.artifact.sha256
    assert comparison["status"] == ("unverified" if legacy else "matched")
    if legacy:
        assert comparison["unverified_fields"] == [
            "legacy_record_without_input_comparison"
        ]
    else:
        assert comparison["input_identity"] == initial["input_identity"]
        assert comparison["matched_fields"] == initial["matched_fields"]
    before = len(inspections)
    Path(state.artifact_record.artifact.path).write_bytes(b"different ISO")
    with pytest.raises(KUBERNETES.KubernetesError, match="content_readback_mismatch"):
        invoke(LIFECYCLE.VerifyArtifactReference(action), state)
    assert len(inspections) == before
