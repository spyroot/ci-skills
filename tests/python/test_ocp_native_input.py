"""Preserve native OpenShift node inputs and verified Pod staging boundaries.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from collections.abc import Callable, MutableSequence
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Optional

import pytest
from conftest import import_script_module

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject

ISO = import_script_module("lib.python.core.kubernetes.ocp_iso")
ISO_VERIFY = import_script_module("lib.python.core.kubernetes.ocp_iso_verify")

NATIVE = import_script_module("lib.python.core.kubernetes.ocp_native_input")
POD_FILES = import_script_module("lib.python.core.kubernetes.pod_files")
MODELS = import_script_module("lib.python.core.kubernetes.tool_pod_models")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
RUNTIME = import_script_module("lib.python.core.runtime")
TARGET = import_script_module("lib.python.core.target")


@pytest.fixture
def pod_file_state() -> MODELS.ToolPodState:
    """Provide a concrete prepared Pod state without contacting Kubernetes.

    :returns: State with a stable Pod identity and service for native boundary doubles.
    """
    target = TARGET.Target(
        github=None,
        gitlab=None,
        kubernetes=TARGET.KubernetesTarget(
            "unit-context", "https://api.example.test:6443", None
        ),
        active_surfaces=("kubernetes",),
    )
    state = MODELS.ToolPodState()
    state.kubernetes = KUBERNETES.KubernetesService(target)
    state.pod = MODELS.PodHandle(
        "unit-ns", "unit-pod", "unit-uid", "tools", "unit-image", 0
    )
    return state


@pytest.mark.parametrize(
    "content",
    (
        b"# caller comment\r\nhosts: &hosts []\r\nfuture-native-field: *hosts\r\n",
        b"deliberately: [invalid YAML\x00\xff\r\n",
    ),
)
def test_native_spec_preserves_exact_bytes_without_custom_yaml_validation(
    content: bytes, tmp_path: Path
) -> None:
    """Native input acceptance belongs to oc, including future fields and errors.

    :param content: Native YAML bytes or bytes that the provider must reject.
    :param tmp_path: Private directory containing the caller's original file.
    """
    path = tmp_path / "caller-input.yaml"
    path.write_bytes(content)

    selected = NATIVE.NativeNodeInput.read(spec=path)

    assert selected.mac_address == ""
    assert [(item.name, item.content) for item in selected.files] == [
        ("nodes-config.yaml", content)
    ]
    assert selected.arguments() == ()
    assert selected.evidence() == {
        "route": "file",
        "mac_address": "",
        "files": [
            {
                "name": "nodes-config.yaml",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ],
    }
    assert path.read_bytes() == content
    assert content.decode("utf-8", errors="replace") not in json.dumps(
        selected.evidence()
    )


@pytest.mark.parametrize("with_network", (False, True))
def test_mac_route_forwards_caller_value_and_optional_unchanged_network_file(
    with_network: bool, tmp_path: Path
) -> None:
    """Forward MAC and network bytes for oc to interpret without wrapper policy.

    :param with_network: Whether the caller supplies a separate native network file.
    :param tmp_path: Private directory holding an optional native file.
    """
    network = tmp_path / "caller-network.yaml"
    content = b"# retain this comment\r\ninterfaces: []\r\n"
    network.write_bytes(content)
    mac = "caller-native-mac-value"
    selected = NATIVE.NativeNodeInput.read(
        mac_address=mac, network_config=network if with_network else None
    )

    expected = ("--mac-address", mac)
    if with_network:
        expected += ("--network-config-path", "network-config.yaml")
    assert selected.arguments() == expected
    assert selected.mac_address == mac
    assert [(item.name, item.content) for item in selected.files] == (
        [("network-config.yaml", content)] if with_network else []
    )


@pytest.mark.parametrize(
    ("with_spec", "mac", "with_network", "error"),
    (
        (False, None, False, "node_input_requires_spec_or_mac_address"),
        (False, "", False, "node_input_requires_spec_or_mac_address"),
        (False, None, True, "node_input_requires_spec_or_mac_address"),
        (True, "00:11:22:33:44:55", False, "node_input_choose_spec_or_mac_address"),
        (True, None, True, "node_input_choose_spec_or_mac_address"),
    ),
)
def test_ambiguous_or_incomplete_routes_fail_before_reading_files(
    with_spec: bool,
    mac: Optional[str],
    with_network: bool,
    error: str,
    tmp_path: Path,
) -> None:
    """Reject only wrapper route conflicts before trying an absent input file.

    :param with_spec: Whether a native YAML route was selected.
    :param mac: Optional MAC flag value.
    :param with_network: Whether a separate network path was supplied.
    :param error: Expected invocation-shape failure.
    :param tmp_path: Directory containing deliberately absent input paths.
    """
    with pytest.raises(ValueError, match=f"^{error}$"):
        NATIVE.NativeNodeInput.read(
            spec=tmp_path / "absent-spec" if with_spec else None,
            mac_address=mac,
            network_config=tmp_path / "absent-network" if with_network else None,
        )


def test_confirmation_evidence_changes_when_native_file_bytes_change(
    tmp_path: Path,
) -> None:
    """Even a comment change binds a different caller input without parsing it.

    :param tmp_path: Private directory holding the same input path across reads.
    """
    path = tmp_path / "nodes.yaml"
    path.write_bytes(b"hosts: []\n")
    before = NATIVE.NativeNodeInput.read(spec=path)
    path.write_bytes(b"# revised\nhosts: []\n")
    after = NATIVE.NativeNodeInput.read(spec=path)

    assert (
        before.evidence()["files"][0]["sha256"]
        != after.evidence()["files"][0]["sha256"]
    )
    assert before.files[0].content == b"hosts: []\n"
    assert after.files[0].content == b"# revised\nhosts: []\n"


@pytest.mark.parametrize("outcome", ("success", "checksum", "size", "native-error"))
def test_pod_staging_passes_exact_bytes_and_requires_matching_native_readback(
    outcome: str, monkeypatch: pytest.MonkeyPatch, pod_file_state: MODELS.ToolPodState
) -> None:
    """A file handle is returned only after native size and digest match input.

    :param outcome: Successful read-back, changed digest/size, or provider refusal.
    :param monkeypatch: Replace only the native Pod execution boundary.
    :param pod_file_state: Prepared Pod identity and bound service for adapter tests.
    """
    state = pod_file_state
    content = b"native-input\x00\xff\r\n"
    digest = hashlib.sha256(content).hexdigest()
    observed: MutableSequence[tuple[tuple[str, ...], Optional[bytes]]] = []

    def native_exec(
        *argv: str, input_data: Optional[bytes] = None
    ) -> RUNTIME.CommandResult:
        """Return programmable provider read-back while retaining exact call bytes.

        :param *argv: Selected Pod identity, native staging script and destination.
        :param input_data: Exact private bytes sent through stdin.
        :returns: Native result for the selected read-back case.
        """
        observed.append((argv, input_data))
        checksum = "0" * 64 if outcome == "checksum" else digest
        size = len(content) + (1 if outcome == "size" else 0)
        return RUNTIME.CommandResult(
            argv,
            23 if outcome == "native-error" else 0,
            f"{checksum}\n{size}\ncreated\n",
            "native refusal" if outcome == "native-error" else "",
        )

    monkeypatch.setattr(state.kubernetes, "pod_exec", native_exec)
    destination = PurePosixPath("/assets/nodes-config.yaml")
    if outcome == "success":
        handle = POD_FILES.write_pod_file(state, destination, content)
        assert (handle.pod, handle.path, handle.sha256, handle.size) == (
            state.pod,
            str(destination),
            digest,
            len(content),
        )
    else:
        expected = (
            "pod_file_write_failed:exit=23"
            if outcome == "native-error"
            else "pod_file_readback_mismatch"
        )
        with pytest.raises(KUBERNETES.KubernetesError, match=expected):
            POD_FILES.write_pod_file(state, destination, content)
    assert len(observed) == 1
    argv, supplied = observed[0]
    assert supplied == content
    assert argv[:6] == (
        "unit-ns",
        "unit-pod",
        "tools",
        "/bin/sh",
        "-ceu",
        POD_FILES.POD_FILE_SCRIPT.read_text(encoding="utf-8"),
    )
    assert argv[-3:] == ("pod-file", str(destination), "write")
    assert all("native-input" not in argument for argument in argv)


@pytest.mark.parametrize("ownership", ("created", "reused"))
def test_stage_reports_identical_reuse_without_claiming_file_ownership(
    ownership: str,
    pod_file_state: MODELS.ToolPodState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only an exclusive creation grants this invocation cleanup ownership.

    :param ownership: Native evidence distinguishing new bytes from identical reuse.
    :param pod_file_state: Concrete prepared Pod and provider service.
    :param monkeypatch: Replace the native operation with ownership read-back.
    """
    content = b"native-input\x00\xff\r\n"
    digest = hashlib.sha256(content).hexdigest()
    observed: MutableSequence[tuple[tuple[str, ...], Optional[bytes]]] = []

    def native_exec(
        *argv: str, input_data: Optional[bytes] = None
    ) -> RUNTIME.CommandResult:
        """Record exact transport and return the selected ownership evidence.

        :param *argv: Pod identity and shell script invocation.
        :param input_data: Caller bytes forwarded through stdin.
        :returns: Native identity with an explicit creation or reuse marker.
        """
        observed.append((argv, input_data))
        return RUNTIME.CommandResult(
            argv, 0, f"{digest}\n{len(content)}\n{ownership}\n", ""
        )

    monkeypatch.setattr(pod_file_state.kubernetes, "pod_exec", native_exec)
    destination = PurePosixPath("/assets/native-input")

    staged = POD_FILES.stage_pod_file(pod_file_state, destination, content)

    assert staged.created is (ownership == "created")
    assert staged.artifact.sha256 == digest
    assert staged.artifact.size == len(content)
    assert observed[0][0][-3:] == ("pod-file", str(destination), "stage")
    assert observed[0][1] == content
    assert len(observed) == 1
    if ownership == "reused":
        with pytest.raises(
            KUBERNETES.KubernetesError, match="pod_file_readback_mismatch"
        ):
            POD_FILES.write_pod_file(pod_file_state, destination, content)


@pytest.mark.parametrize(
    ("output", "exit_status", "expected"),
    (
        ("removed\n", 0, True),
        ("absent\n", 0, False),
        ("unknown\n", 0, None),
        ("", 32, None),
    ),
)
def test_cleanup_transmits_owned_digest_and_size_and_requires_native_readback(
    output: str,
    exit_status: int,
    expected: Optional[bool],
    pod_file_state: MODELS.ToolPodState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup returns a boolean only for verified removal or verified absence.

    :param output: Native removal evidence or an invalid response.
    :param exit_status: Provider exit status, including changed-file refusal.
    :param expected: Expected deletion boolean, absent when an error must propagate.
    :param pod_file_state: Concrete prepared Pod and provider service.
    :param monkeypatch: Replace the native execution boundary with selected evidence.
    """
    artifact = MODELS.ArtifactHandle(
        pod_file_state.pod, "/assets/native-input", "a" * 64, 42
    )
    observed: MutableSequence[tuple[tuple[str, ...], Optional[bytes]]] = []

    def native_exec(
        *argv: str, input_data: Optional[bytes] = None
    ) -> RUNTIME.CommandResult:
        """Capture cleanup identity and return the selected provider evidence.

        :param *argv: Pod identity and native cleanup invocation.
        :param input_data: Expected digest and size supplied through private stdin.
        :returns: Native provider result, including an optional refusal.
        """
        observed.append((argv, input_data))
        return RUNTIME.CommandResult(
            argv, exit_status, output, "changed" if exit_status else ""
        )

    monkeypatch.setattr(pod_file_state.kubernetes, "pod_exec", native_exec)
    if expected is None:
        message = (
            "pod_file_remove_failed:exit=32"
            if exit_status
            else "pod_file_cleanup_readback_mismatch"
        )
        with pytest.raises(KUBERNETES.KubernetesError, match=message):
            POD_FILES.remove_pod_file(pod_file_state, artifact)
    else:
        assert POD_FILES.remove_pod_file(pod_file_state, artifact) is expected
    assert len(observed) == 1
    assert observed[0][0][-3:] == ("pod-file", artifact.path, "remove")
    assert observed[0][1] == f"{artifact.sha256} 42\n".encode("ascii")


@pytest.fixture
def inspect_embedded_output(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[JSONObject], JSONObject]:
    """Inspect native output through the real decoder and observation boundary.

    :param monkeypatch: Replace only external coreos-installer execution.
    :returns: Public inspector bound to an isolated artifact and controlled output.
    """
    state = ISO.OCPState()
    pod = MODELS.PodHandle("unit-ns", "unit-pod", "unit-uid", "tools", "image", 0)
    artifact = MODELS.ArtifactHandle(pod, "/assets/node.iso", "a" * 64, 4096)

    def inspect(document: JSONObject) -> JSONObject:
        """Return observations produced from the supplied native Ignition object.

        :param document: Native output, including opaque private provider fields.
        :returns: Embedded evidence exposed by the public ISO inspector.
        """
        payload = json.dumps(document)

        def native_inspect(
            selected: ISO.OCPState, operation: str, *arguments: str
        ) -> RUNTIME.CommandResult:
            """Supply native output without replacing the decoder or inspector.

            :param selected: Concrete shared state sent to the command boundary.
            :param operation: Native inspection operation selected by production.
            :param *arguments: Exact artifact path passed through production.
            :returns: Successful native command output for the current document.
            """
            assert selected is state
            assert (operation, arguments) == ("inspect", (artifact.path,))
            return RUNTIME.CommandResult(
                ("coreos-installer", "iso", "ignition", "show", artifact.path),
                0,
                payload,
                "",
            )

        monkeypatch.setattr(ISO_VERIFY, "run_node_image_script", native_inspect)
        result = ISO_VERIFY.inspect_verified_node_image(state, artifact)
        assert result["artifact"] == artifact.as_dict()
        assert state.readback["embedded_ignition"] == result["embedded_ignition"]
        return result["embedded_ignition"]

    return inspect


def test_embedded_native_host_and_network_observations_exclude_credentials(
    inspect_embedded_output: Callable[[JSONObject], JSONObject],
) -> None:
    """Decode compressed native files while exposing only identity/network fields.

    :param inspect_embedded_output: Real public inspection with native output supplied.
    """
    mac = b"02:00:00:10:00:01\n"
    network = (
        b"[connection]\ninterface-name=ens3\ntype=ethernet\n"
        b"[ethernet]\nmac-address=02:00:00:10:00:01\nmtu=9000\n"
        b"[wifi-security]\npsk=private-wifi-marker\n"
        b"[802-1x]\npassword=private-password-marker\n"
    )
    root_hints = b"deviceName: /dev/sda\nserialNumber: private-disk-marker\n"
    files = [
        {
            "path": "/etc/assisted/hostconfig/worker-01/mac_addresses",
            "contents": {
                "source": "data:;base64,"
                + base64.b64encode(gzip.compress(mac)).decode(),
                "compression": "gzip",
            },
        },
        {
            "path": "/etc/assisted/network/worker-01/ens3.nmconnection",
            "contents": {
                "source": "data:;base64," + base64.b64encode(network).decode()
            },
        },
        {
            "path": "/etc/assisted/hostnames/02:00:00:10:00:01",
            "contents": {"source": "data:,worker-01%0A"},
        },
        {
            "path": "/etc/assisted/hostconfig/worker-01/root-device-hints.yaml",
            "contents": {
                "source": "data:;base64," + base64.b64encode(root_hints).decode()
            },
        },
        {
            "path": "/etc/assisted/hostconfig/worker-01/pull-secret",
            "contents": {"source": "data:,private-pull-secret-marker"},
        },
    ]
    evidence = inspect_embedded_output(
        {
            "ignition": {"version": "3.4.0"},
            "storage": {"files": files},
            "passwd": {"users": [{"passwordHash": "private-user-marker"}]},
        }
    )

    configuration = evidence["configuration"]
    by_kind = {file["kind"]: file for file in configuration["files"]}
    assert evidence["version"] == "3.4.0"
    assert set(by_kind) == {"mac_addresses", "network", "hostname", "root_device_hints"}
    assert by_kind["mac_addresses"]["observations"] == {
        "mac_addresses": ["02:00:00:10:00:01"]
    }
    assert by_kind["network"]["observations"] == {
        "connection": {"interface-name": "ens3", "type": "ethernet"},
        "ethernet": {"mac-address": "02:00:00:10:00:01", "mtu": "9000"},
    }
    assert by_kind["hostname"]["observations"] == {"hostname": "worker-01"}
    for kind, content in (("mac_addresses", mac), ("network", network)):
        assert by_kind[kind]["size"] == len(content)
        assert by_kind[kind]["sha256"] == hashlib.sha256(content).hexdigest()
    assert (
        by_kind["root_device_hints"]["sha256"] == hashlib.sha256(root_hints).hexdigest()
    )
    assert by_kind["root_device_hints"]["unobserved"] == "digest_only"
    report = json.dumps(evidence)
    assert "private-" not in report
    assert "pull-secret" not in report
    assert "passwordHash" not in report


def test_mac_only_dhcp_output_is_observable_without_a_network_file(
    inspect_embedded_output: Callable[[JSONObject], JSONObject],
) -> None:
    """Native DHCP output remains valid when no explicit network file is generated.

    :param inspect_embedded_output: Public inspection using controlled native output.
    """
    evidence = inspect_embedded_output(
        {
            "ignition": {"version": "3.4.0"},
            "storage": {
                "files": [
                    {
                        "path": "/etc/assisted/hostconfig/node/mac_addresses",
                        "contents": {"source": "data:,02:00:00:10:00:01%0A"},
                    }
                ]
            },
        }
    )

    configuration = evidence["configuration"]
    assert configuration["files"][0]["observations"] == {
        "mac_addresses": ["02:00:00:10:00:01"]
    }
    assert set(configuration["unobserved_fields"]) == {
        "root_device_hints",
        "role",
        "hostname",
        "network",
    }


@pytest.mark.parametrize(
    "source",
    (
        "https://private-host.example.test/private-body-marker",
        "data:;base64,private-body-marker!",
        "data:,private-body-marker-without-keyfile-section",
    ),
    ids=("external-resource", "malformed-base64", "malformed-keyfile"),
)
def test_unreadable_selected_output_reports_absence_without_private_content(
    source: str, inspect_embedded_output: Callable[[JSONObject], JSONObject]
) -> None:
    """Opaque or malformed selected output must not leak source URLs or file bodies.

    :param source: External resource or unreadable inline native keyfile content.
    :param inspect_embedded_output: Public inspection using controlled native output.
    """
    path = "/etc/assisted/network/node/ens3.nmconnection"
    evidence = inspect_embedded_output(
        {
            "ignition": {"version": "3.4.0"},
            "storage": {"files": [{"path": path, "contents": {"source": source}}]},
        }
    )

    configuration = evidence["configuration"]
    file = configuration["files"][0]
    assert file["path"] == path
    assert file["unobserved"]
    assert "observations" not in file
    assert "network" in configuration["unobserved_fields"]
    report = json.dumps(evidence)
    assert source not in report
    assert "private-host" not in report
    assert "private-body-marker" not in report
