"""Prove artifact lifetime and exact run ownership across ISO removal.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from conftest import import_script_module
from test_artifact_storage import (
    FilePodCluster,
    checked_destination,
    destination,
    source_artifact,
    storage_state,
)

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
NATIVE = import_script_module("lib.python.core.kubernetes.ocp_native_input")
COMMANDS = import_script_module("lib.python.core.runtime")


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
            '{"ignition":{"version":"3.4.0"}}',
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
