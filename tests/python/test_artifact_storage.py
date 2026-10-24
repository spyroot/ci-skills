"""Prove exact-path artifact delivery and shared-storage preservation.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Optional

import pytest
from conftest import SKILL_ROOT, bind_kubernetes_fixture, import_script_module
from test_tool_pod import MemoryCluster

MODELS = import_script_module("lib.python.core.kubernetes.artifact_storage_models")
STORAGE = import_script_module("lib.python.core.kubernetes.artifact_storage_callbacks")
RUNTIME = import_script_module("lib.python.core.kubernetes.artifact_storage_runtime")
PODS = import_script_module("lib.python.core.kubernetes.tool_pod_models")
SCOPES = import_script_module("lib.python.core.kubernetes.run_scope")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
COMMANDS = import_script_module("lib.python.core.runtime")

STORAGE_SCRIPT = SKILL_ROOT / "lib/bash/kubernetes/artifact-storage.sh"


class FilePodCluster(MemoryCluster):
    """Execute actual storage shell operations against test-owned files."""

    def __init__(self) -> None:
        """Keep the existing object model and allow malformed provider size evidence."""
        super().__init__()
        self.reported_size_delta = 0

    def pod_exec(
        self,
        namespace: str,
        name: str,
        container: str,
        *argv: str,
        input_data: Optional[bytes] = None,
    ) -> COMMANDS.CommandResult:
        """Replace only Kubernetes transport with a bounded local child.

        :param namespace: Exact fixture namespace.
        :param name: Fixture Pod name.
        :param container: Selected fixture container.
        :param *argv: Installed Pod script and literal file arguments.
        :param input_data: Optional private bytes passed to the installed file script.
        :returns: Native result with optional malformed provider-size evidence.
        """
        assert ("pod", namespace, name) in self.objects
        assert container == "ci-skills-pod"
        self.executed.append(argv)
        result = subprocess.run(
            argv, input=input_data, capture_output=True, check=False, timeout=5
        )
        stdout = result.stdout.decode("utf-8")
        if (
            self.reported_size_delta
            and argv[3:5] == ("artifact-storage", "verify")
            and not result.returncode
        ):
            document = json.loads(stdout)
            document["size"] += self.reported_size_delta
            stdout = json.dumps(document)
        return COMMANDS.CommandResult(
            tuple(argv), result.returncode, stdout, result.stderr.decode("utf-8")
        )


def storage_state(tmp_path: Path) -> tuple[MODELS.ArtifactStorageState, FilePodCluster]:
    """Create verified producer inputs without a live Kubernetes connection.

    :param tmp_path: Isolated pytest directory used as the mounted volume.
    :returns: Shared storage state and the in-memory provider transport.
    """
    cluster = FilePodCluster()
    state = MODELS.ArtifactStorageState()
    bind_kubernetes_fixture(state, cluster)
    state.run_scope = SCOPES.RunScope.new(
        namespace="consumer-storage", run_id="storage-proof"
    )
    name = state.run_scope.name("tool-pod")
    observed = cluster.apply(
        {"kind": "Pod", "metadata": {"name": name, "namespace": "consumer-storage"}}
    )
    state.pod = PODS.PodHandle(
        "consumer-storage",
        name,
        observed["metadata"]["uid"],
        "ci-skills-pod",
        "registry.example.test/tools:unit",
        0,
    )
    return state, cluster


def destination(
    tmp_path: Path, *, temporary: bool = False
) -> MODELS.StorageDestination:
    """Select an exact output path and opaque native Kubernetes volume source.

    :param tmp_path: Test-owned mount directory.
    :param temporary: Select Pod-lifetime storage instead of an existing claim.
    :returns: Concrete caller destination without a default output filename.
    """
    return MODELS.StorageDestination(
        namespace="consumer-storage",
        volume_source=(
            {"emptyDir": {}}
            if temporary
            else {"persistentVolumeClaim": {"claimName": "caller-model-data"}}
        ),
        mount_path=str(tmp_path),
        artifact_path=str(tmp_path / "caller selected.iso"),
        build_output="scratch",
    )


def source_artifact(
    state: MODELS.ArtifactStorageState, path: Path
) -> PODS.ArtifactHandle:
    """Observe source identity from known fixture bytes.

    :param state: Shared state containing the producer Pod.
    :param path: File owned by the current pytest case.
    :returns: Producer handle with the real fixture checksum and length.
    """
    content = path.read_bytes()
    return PODS.ArtifactHandle(
        state.pod, str(path), hashlib.sha256(content).hexdigest(), len(content)
    )


def checked_destination(
    state: MODELS.ArtifactStorageState, selected: MODELS.StorageDestination
) -> None:
    """Supply the preceding mount check independently of copy behavior.

    :param state: Shared state passed to the storage callbacks.
    :param selected: Exact caller destination selected by the test.
    """
    state.storage_handle = MODELS.StorageHandle(
        selected, selected.mount_path, "nfs4", "storage.example.test:/models", None
    )


def test_native_volume_fields_and_destination_are_preserved_verbatim(
    tmp_path: Path,
) -> None:
    """Provider-specific fields remain opaque and caller paths are not relocated."""
    source = {
        "csi": {
            "driver": "future.storage.example.test",
            "volumeAttributes": {"vendor-specific": "leave unchanged"},
        }
    }
    document = {
        "namespace": "consumer-storage",
        "volumeSource": source,
        "mountPath": str(tmp_path),
        "artifactPath": str(tmp_path / "chosen.iso"),
        "buildOutput": "scratch",
    }
    selected = MODELS.StorageDestination.from_document(document)

    assert selected.as_dict() == document
    assert selected.tool_pod_mount().volume_source == source
    assert selected.tool_pod_mount().mount_path == str(tmp_path)
    source["csi"]["volumeAttributes"]["vendor-specific"] = "caller changed later"
    assert selected.as_dict()["volumeSource"]["csi"]["volumeAttributes"] == {
        "vendor-specific": "leave unchanged"
    }


@pytest.mark.parametrize("conflict", ("file", "symlink"))
def test_copy_refuses_existing_destination_without_modifying_caller_data(
    conflict: str, tmp_path: Path
) -> None:
    """Both a regular output and a symbolic link protect existing shared data."""
    source = tmp_path / "source.iso"
    source.write_bytes(b"new ISO")
    caller = tmp_path / "caller-model"
    caller.write_bytes(b"existing model bytes")
    output = tmp_path / "selected.iso"
    if conflict == "symlink":
        output.symlink_to(caller)
    else:
        output.write_bytes(b"existing ISO")
    before = output.lstat()

    result = subprocess.run(
        ["/bin/sh", str(STORAGE_SCRIPT), "copy", str(source), str(output)],
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 51
    assert output.lstat().st_ino == before.st_ino
    assert output.read_bytes() == (
        b"existing model bytes" if conflict == "symlink" else b"existing ISO"
    )
    assert caller.read_bytes() == b"existing model bytes"
    assert source.read_bytes() == b"new ISO"


@pytest.mark.parametrize("mismatch", ("digest", "size"))
def test_changed_copy_fails_readback_and_cleanup_preserves_source_and_neighbors(
    mismatch: str, tmp_path: Path
) -> None:
    """Changed final bytes cannot release retention or delete unrelated shared data."""
    state, cluster = storage_state(tmp_path)
    selected = destination(tmp_path)
    checked_destination(state, selected)
    source = tmp_path / "verified-source.iso"
    source.write_bytes(b"original")
    neighbor = tmp_path / "model-data"
    neighbor.write_bytes(b"do not remove")
    state.source_artifact = source_artifact(state, source)
    copy = STORAGE.CopyArtifact(MODELS.ArtifactStorageAction(selected))
    copy.bind(state)
    copy.run()
    output = Path(selected.artifact_path)
    assert output.read_bytes() == source.read_bytes()
    if mismatch == "digest":
        output.write_bytes(b"modified")
    else:
        cluster.reported_size_delta = 1
    verify = STORAGE.VerifyStoredArtifact(MODELS.ArtifactStorageAction(selected))
    verify.bind(state)

    with pytest.raises(
        KUBERNETES.KubernetesError, match="artifact_storage_readback_mismatch"
    ):
        verify.run()

    assert not state.destination_verified
    assert state.retained_resources
    copy.cleanup()
    assert not output.exists()
    assert source.read_bytes() == b"original"
    assert neighbor.read_bytes() == b"do not remove"


@pytest.mark.parametrize("temporary", (False, True))
def test_verified_copy_uses_exact_final_path_and_respects_storage_lifetime(
    temporary: bool, tmp_path: Path
) -> None:
    """Only persistent-volume proof releases producer retention after read-back."""
    state, _cluster = storage_state(tmp_path)
    selected = destination(tmp_path, temporary=temporary)
    checked_destination(state, selected)
    source = tmp_path / "source.iso"
    source.write_bytes(b"ISO payload\x00\xff")
    state.source_artifact = source_artifact(state, source)
    unrelated = SCOPES.resource_identity("ConfigMap", "another-run", "consumer-storage")
    state.retained_resources.add(unrelated)
    copy = STORAGE.CopyArtifact(MODELS.ArtifactStorageAction(selected))
    copy.bind(state)
    copy.run()
    verify = STORAGE.VerifyStoredArtifact(MODELS.ArtifactStorageAction(selected))
    verify.bind(state)

    result = verify.run()
    copy.cleanup()

    assert result["verified"] is True
    assert state.stored_artifact.path == selected.artifact_path
    assert Path(selected.artifact_path).read_bytes() == source.read_bytes()
    assert unrelated in state.retained_resources
    assert result["retention"] == ("pod" if temporary else "caller_selected_volume")
    producer = SCOPES.resource_identity("Pod", state.pod.name, state.pod.namespace)
    assert (producer in state.retained_resources) is temporary


def test_cleanup_refuses_a_path_not_created_by_this_run(tmp_path: Path) -> None:
    """An exact ownership mismatch stops cleanup before a Pod command is sent."""
    state, cluster = storage_state(tmp_path)
    caller = tmp_path / "model-data"
    caller.write_bytes(b"shared caller bytes")
    state.destination_created = True
    state.copied_destination = str(tmp_path / "owned-partial.iso")

    with pytest.raises(KUBERNETES.KubernetesError, match="cleanup_ownership_mismatch"):
        RUNTIME.remove_artifact(state, str(caller))

    assert cluster.executed == []
    assert caller.read_bytes() == b"shared caller bytes"


def test_direct_output_rejects_a_source_at_a_different_path(tmp_path: Path) -> None:
    """A direct build cannot silently reinterpret its output as the caller path."""
    state, cluster = storage_state(tmp_path)
    selected = replace(destination(tmp_path), build_output="destination")
    checked_destination(state, selected)
    source = tmp_path / "unexpected.iso"
    source.write_bytes(b"ISO")
    state.source_artifact = source_artifact(state, source)
    callback = STORAGE.CopyArtifact(MODELS.ArtifactStorageAction(selected))
    callback.bind(state)

    with pytest.raises(KUBERNETES.KubernetesError, match="direct_path_mismatch"):
        callback.run()

    assert cluster.executed == []
    assert not Path(selected.artifact_path).exists()
    assert source.read_bytes() == b"ISO"
