"""Native in-cluster verification for generated OpenShift node ISOs.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import binascii
import configparser
import gzip
import hashlib
import io
import re
import zlib
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from typing import Final, Optional
from urllib.parse import unquote_to_bytes

import yaml

from lib.python.core.binary_build import BinaryBuildPlanner, BuildConfigLoader
from lib.python.core.cli_api import MAX_RESPONSE_BYTES, response_decoder
from lib.python.core.json_types import JSONObject, JSONValue, is_json_value
from lib.python.core.kubernetes.artifact_storage_callbacks import (
    retain_verified_source,
)
from lib.python.core.kubernetes.artifact_storage_runtime import observe_artifact
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.ocp_artifact_models import NativeComparisonStatus
from lib.python.core.kubernetes.ocp_iso import (
    NodeImageCallback,
    OCPState,
    run_node_image_script,
)
from lib.python.core.kubernetes.ocp_native_input import NativeNodeInput
from lib.python.core.kubernetes.tool_pod_models import ArtifactHandle
from lib.python.core.read_tools import ReadToolError
from lib.python.core.runtime import redact_tree, sanitize
from lib.python.sys.status import VERIFIED

_ASSISTED_ROOT: Final = "/etc/assisted/"
_HOST_CONFIG_FILES: Final = {
    "mac_addresses": "mac_addresses",
    "root-device-hints.yaml": "root_device_hints",
    "role": "role",
}
_FILE_KINDS: Final = (*_HOST_CONFIG_FILES.values(), "hostname", "network")
_IP_FIELDS: Final = (
    "method",
    "gateway",
    "dns",
    "dns-search",
    "dns-options",
    "dns-priority",
    "ignore-auto-dns",
    "ignore-auto-routes",
    "never-default",
    "may-fail",
    "route-metric",
    "route-table",
)
# NetworkManager's keyfile format stores addresses, routes, and rules by index.
_IP_INDEXED_FIELD: Final = re.compile(
    r"(?:address[0-9]+|route[0-9]+(?:_options)?|routing-rule[0-9]+)"
)
_NETWORK_FIELDS: Final = {
    "connection": ("interface-name", "type"),
    "ethernet": ("mac-address", "cloned-mac-address", "mtu"),
    "802-3-ethernet": ("mac-address", "cloned-mac-address", "mtu"),
    "match": ("interface-name",),
    "ipv4": _IP_FIELDS,
    "ipv6": (*_IP_FIELDS, "addr-gen-mode", "ip6-privacy"),
}


_NMSTATE_MANIFEST: Final = "/etc/assisted/manifests/nmstateconfig.yaml"
_INFRAENV_MANIFEST: Final = "/etc/assisted/manifests/infraenv.yaml"
_COMPARED_HOST_FIELDS: Final = frozenset(
    {"hostname", "interfaces", "rootDeviceHints", "networkConfig", "role"}
)
_COMPARED_NODE_FIELDS: Final = frozenset(
    {"hosts", "cpuArchitecture", "sshKey", "bootArtifactsBaseURL"}
)
_ARCHITECTURES: Final = {"amd64": "x86_64", "arm64": "aarch64"}


class EmbeddedObservationError(ValueError):
    """Explain an unavailable observation without including private file content."""


def _selected_kind(path: str) -> str:
    """Classify only native host identity and NetworkManager output files.

    :param path: Exact path declared by an embedded Ignition file entry.
    :returns: A recognized output kind, or an empty string for unselected files.
    """
    if not path.startswith(_ASSISTED_ROOT):
        return ""
    parts = path[len(_ASSISTED_ROOT) :].split("/")
    if len(parts) == 3 and parts[0] == "hostconfig":
        return _HOST_CONFIG_FILES.get(parts[-1], "")
    if len(parts) == 2 and parts[0] == "hostnames":
        return "hostname"
    if len(parts) >= 2 and parts[0] == "network":
        return "network"
    return ""


def _resource_bytes(resource: JSONObject) -> bytes:
    """Decode inline Ignition contents without fetching external source URLs.

    :param resource: Native Ignition contents or append resource mapping.
    :returns: Inline decoded bytes, including supported gzip decompression.
    :raises EmbeddedObservationError: Content is external, unsupported or oversized.
    :raises ValueError: Inline base64 content is malformed.
    :raises OSError: Compressed contents cannot be decoded.
    :raises EOFError: Compressed contents are incomplete.
    :raises zlib.error: Compressed contents are corrupt.
    """
    source = resource.get("source")
    if source is None:
        return b""
    if not isinstance(source, str) or not source.startswith("data:"):
        raise EmbeddedObservationError("content_not_inline")
    header, separator, body = source.partition(",")
    if not separator:
        raise EmbeddedObservationError("content_data_url_invalid")
    content = unquote_to_bytes(body)
    if header.lower().endswith(";base64"):
        content = base64.b64decode(content, validate=True)
    compression = resource.get("compression")
    if compression == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as stream:
            content = stream.read(MAX_RESPONSE_BYTES + 1)
    elif compression not in (None, ""):
        raise EmbeddedObservationError("content_compression_unobserved")
    if len(content) > MAX_RESPONSE_BYTES:
        raise EmbeddedObservationError("content_observation_limit")
    return content


def _file_bytes(file: JSONObject) -> bytes:
    """Reconstruct a selected inline file in native contents-and-append order.

    :param file: Selected native Ignition file entry.
    :returns: The complete inline byte sequence used for its digest and observations.
    :raises TypeError: Contents or append resources do not have their native shapes.
    :raises EmbeddedObservationError: Reconstructed content exceeds the shared limit.
    """
    contents = file.get("contents", {})
    append = file.get("append", [])
    if not isinstance(contents, Mapping) or not isinstance(append, list):
        raise TypeError("content_shape_unobserved")
    output = bytearray(_resource_bytes(contents))
    for resource in append:
        if not isinstance(resource, Mapping):
            raise TypeError("content_shape_unobserved")
        output.extend(_resource_bytes(resource))
        if len(output) > MAX_RESPONSE_BYTES:
            raise EmbeddedObservationError("content_observation_limit")
    return bytes(output)


def _network_observations(content: str) -> JSONObject:
    """Select native interface and IP configuration, never credential sections.

    :param content: Decoded native NetworkManager keyfile text.
    :returns: Only selected nonsecret properties through bounded shared redaction.
    :raises configparser.Error: The keyfile cannot be parsed.
    """
    parser = configparser.ConfigParser(
        interpolation=None, strict=False, default_section=""
    )
    parser.read_string(content)
    fields: MutableMapping[str, JSONValue] = {}
    for section, keys in _NETWORK_FIELDS.items():
        if not parser.has_section(section):
            continue
        values = {
            key: sanitize(parser.get(section, key))
            for key in parser.options(section)
            if key in keys
            or (section in ("ipv4", "ipv6") and _IP_INDEXED_FIELD.fullmatch(key))
        }
        if values:
            fields[section] = values
    return fields


def _file_observations(kind: str, content: bytes) -> JSONObject:
    """Read only recognized identity fields from native generated file bytes.

    :param kind: Output kind selected from its native path.
    :param content: Complete inline bytes of that selected file.
    :returns: Safe native observations. Root hints reuse the existing safe YAML
        loader and JSON expansion bound; neither validates native build inputs.
    :raises UnicodeError: A selected text field is not UTF-8.
    :raises configparser.Error: A selected network keyfile cannot be parsed.
    :raises yaml.YAMLError: Generated root-device hints cannot be decoded safely.
    :raises ReadToolError: Decoded hints exceed the shared JSON expansion bound.
    :raises EmbeddedObservationError: Decoded hints cannot be represented as an object.
    :raises RecursionError: Decoded hints exceed the parser or traversal depth.
    """
    text = content.decode("utf-8")
    if not text.strip():
        return {}
    if kind == "root_device_hints":
        value: object = yaml.load(text, Loader=BuildConfigLoader)
        BinaryBuildPlanner.check_json_size(value)
        if not is_json_value(value) or not isinstance(value, Mapping):
            raise EmbeddedObservationError("root_device_hints_unreadable")
        return {kind: redact_tree(value)} if value else {}
    if kind == "network":
        return _network_observations(text)
    if kind == "mac_addresses":
        return {
            kind: [sanitize(line.strip()) for line in text.splitlines() if line.strip()]
        }
    return {kind: sanitize(text.strip())}


def _file_evidence(file: JSONObject, path: str, kind: str) -> JSONObject:
    """Identify one selected file and report observable fields or a safe reason.

    :param file: Selected native Ignition entry; contents never enter the report.
    :param path: Exact selected native file path.
    :param kind: Its recognized host or network output kind.
    :returns: File identity and safe observations, with missing observations explicit.
    """
    evidence: MutableMapping[str, JSONValue] = {"path": path, "kind": kind}
    try:
        content = _file_bytes(file)
        evidence.update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
        observations = _file_observations(kind, content)
    except EmbeddedObservationError as exc:
        evidence["unobserved"] = str(exc)
        return evidence
    except (
        TypeError,
        ValueError,
        UnicodeError,
        OSError,
        EOFError,
        RecursionError,
        ReadToolError,
        zlib.error,
        configparser.Error,
        yaml.YAMLError,
    ):
        evidence["unobserved"] = "inline_content_or_fields_unreadable"
        return evidence
    if observations:
        evidence["observations"] = observations
    else:
        evidence["unobserved"] = "fields_absent"
    return evidence


def _configuration_evidence(document: JSONObject) -> JSONObject:
    """Collect selected native output records without imposing required fields.

    :param document: Actual embedded Ignition object decoded by the shared boundary.
    :returns: Selected file evidence and fields not observed in this output.
    """
    storage = document.get("storage")
    files = storage.get("files", []) if isinstance(storage, Mapping) else []
    selected: MutableSequence[JSONObject] = []
    observed: set[str] = set()
    if isinstance(files, Sequence) and not isinstance(files, (str, bytes)):
        for file in files:
            if not isinstance(file, Mapping):
                continue
            path = file.get("path")
            kind = _selected_kind(path) if isinstance(path, str) else ""
            if kind and isinstance(path, str):
                evidence = _file_evidence(file, path, kind)
                selected.append(evidence)
                if "observations" in evidence:
                    observed.add(kind)
    return {
        "files": selected,
        "unobserved_fields": [kind for kind in _FILE_KINDS if kind not in observed],
    }


def _embedded_evidence(payload: str) -> JSONObject:
    """Select safe evidence from actual embedded Ignition JSON.

    :param payload: Exact private output from ``coreos-installer``.
    :returns: Ignition identity and selected native host/network observations.
        Field text uses the shared bounded redaction; unobserved values stay explicit.
    :raises KubernetesError: If output is invalid JSON or lacks native version data.
    """
    try:
        document = response_decoder().decode(payload)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise KubernetesError("node_image_embedded_ignition_invalid") from exc
    if not isinstance(document, Mapping):
        raise KubernetesError("node_image_embedded_ignition_invalid")
    ignition = document.get("ignition")
    version = ignition.get("version") if isinstance(ignition, Mapping) else None
    if not isinstance(version, str) or not version:
        raise KubernetesError("node_image_embedded_ignition_invalid")
    encoded = payload.encode()
    return {
        "version": version,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "size": len(encoded),
        "configuration": _configuration_evidence(document),
    }


class NativeInputComparator:
    """Compare caller intent with observable native content in a built ISO."""

    @staticmethod
    def _embedded_files(document: JSONObject) -> Mapping[str, bytes]:
        """Return decoded native files needed only for post-build comparison.

        :param document: Actual embedded Ignition object.
        :returns: Selected native paths and their exact decoded bytes.
        """
        storage = document.get("storage")
        files = storage.get("files", []) if isinstance(storage, Mapping) else []
        result: dict[str, bytes] = {}
        if not isinstance(files, Sequence) or isinstance(files, (str, bytes)):
            return result
        for entry in files:
            if not isinstance(entry, Mapping):
                continue
            path = entry.get("path")
            if not isinstance(path, str):
                continue
            if path.startswith(
                (
                    _NMSTATE_MANIFEST,
                    _INFRAENV_MANIFEST,
                    "/etc/assisted/hostconfig/",
                    "/etc/assisted/hostnames/",
                )
            ):
                try:
                    result[path] = _file_bytes(entry)
                except (
                    EmbeddedObservationError,
                    TypeError,
                    ValueError,
                    OSError,
                    EOFError,
                    binascii.Error,
                    zlib.error,
                ):
                    continue
        return result

    @staticmethod
    def _yaml_documents(content: bytes) -> tuple[Mapping[str, object], ...]:
        """Decode provider-accepted native YAML after the ISO exists.

        :param content: Exact caller or embedded bytes.
        :returns: Mapping documents in their native order.
        :raises yaml.YAMLError: YAML cannot be decoded.
        :raises TypeError: A nonempty document is not an object.
        """
        result: list[Mapping[str, object]] = []
        for document in yaml.safe_load_all(content):
            if document is None:
                continue
            if not isinstance(document, Mapping):
                raise TypeError("native_yaml_document_not_object")
            result.append(document)
        return tuple(result)

    @classmethod
    def _embedded_yaml(
        cls, files: Mapping[str, bytes], path: str
    ) -> tuple[Mapping[str, object], ...]:
        """Decode embedded YAML only when the ISO exposes a readable file.

        :param files: Selected inline files from the generated ISO.
        :param path: Native path containing the requested YAML mapping.
        :returns: Decoded mappings, or no mapping when the output is unreadable.
        """
        content = files.get(path)
        if content is None:
            return ()
        try:
            return cls._yaml_documents(content)
        except (yaml.YAMLError, TypeError, UnicodeError, RecursionError):
            return ()

    @staticmethod
    def _embedded_text(files: Mapping[str, bytes], path: str) -> str | None:
        """Read one selected native text file when its bytes are UTF-8.

        :param files: Selected inline files from the generated ISO.
        :param path: Native path containing the requested text field.
        :returns: Decoded text, or ``None`` when it cannot be observed.
        """
        content = files.get(path)
        if content is None:
            return None
        try:
            return content.decode("utf-8").strip()
        except UnicodeError:
            return None

    @staticmethod
    def _normalized_mac(value: object) -> str:
        """Normalize one observed MAC only for post-output equality.

        :param value: Caller or embedded MAC candidate.
        :returns: Lowercase hexadecimal characters, or an empty string.
        """
        if not isinstance(value, str):
            return ""
        return "".join(
            character for character in value.casefold() if character.isalnum()
        )

    @staticmethod
    def _native_equal(expected: object, actual: object) -> bool:
        """Compare native structures without Python's bool/integer equivalence.

        :param expected: Caller-supplied native value.
        :param actual: Value decoded from generated native output.
        :returns: Whether objects have the same JSON-compatible structure and values.
        """
        if isinstance(expected, Mapping) and isinstance(actual, Mapping):
            remaining = list(actual.items())
            if len(expected) != len(remaining):
                return False
            for expected_key, expected_value in expected.items():
                match = next(
                    (
                        index
                        for index, (actual_key, actual_value) in enumerate(remaining)
                        if type(expected_key) is type(actual_key)
                        and expected_key == actual_key
                        and NativeInputComparator._native_equal(
                            expected_value, actual_value
                        )
                    ),
                    None,
                )
                if match is None:
                    return False
                remaining.pop(match)
            return True
        if (
            isinstance(expected, Sequence)
            and not isinstance(expected, (str, bytes, bytearray))
            and isinstance(actual, Sequence)
            and not isinstance(actual, (str, bytes, bytearray))
        ):
            return len(expected) == len(actual) and all(
                NativeInputComparator._native_equal(left, right)
                for left, right in zip(expected, actual, strict=True)
            )
        return type(expected) is type(actual) and expected == actual

    @staticmethod
    def _first_host_mac(host: Mapping[str, object]) -> tuple[str, str]:
        """Return the first caller interface MAC in path and comparison forms.

        :param host: One provider-accepted native host object.
        :returns: Lowercase native path value and normalized comparison identity.
        """
        interfaces = host.get("interfaces")
        if not isinstance(interfaces, Sequence) or isinstance(
            interfaces, (str, bytes, bytearray)
        ):
            return "", ""
        for interface in interfaces:
            if not isinstance(interface, Mapping):
                continue
            value = interface.get("macAddress")
            normalized = NativeInputComparator._normalized_mac(value)
            if isinstance(value, str) and normalized:
                return value.casefold(), normalized
        return "", ""

    @staticmethod
    def _specs_for_mac(
        specs: Sequence[Mapping[str, object]], expected_mac: str
    ) -> tuple[Mapping[str, object], ...]:
        """Select only NMState specs associated with one caller MAC.

        :param specs: Native NMState specification objects extracted from the ISO.
        :param expected_mac: Normalized caller MAC identity.
        :returns: Every spec whose interface mapping contains that MAC.
        """
        if not expected_mac:
            return ()
        matched: list[Mapping[str, object]] = []
        for spec in specs:
            interfaces = spec.get("interfaces")
            if not isinstance(interfaces, Sequence) or isinstance(
                interfaces, (str, bytes, bytearray)
            ):
                continue
            if any(
                isinstance(interface, Mapping)
                and NativeInputComparator._normalized_mac(interface.get("macAddress"))
                == expected_mac
                for interface in interfaces
            ):
                matched.append(spec)
        return tuple(matched)

    @staticmethod
    def _host_roots_for_mac(
        files: Mapping[str, bytes], expected_mac: str
    ) -> tuple[str, ...]:
        """Find native hostconfig roots whose MAC file identifies one caller host.

        :param files: Decoded native output files keyed by embedded path.
        :param expected_mac: Normalized caller MAC identity.
        :returns: Matching hostconfig roots in deterministic path order.
        """
        if not expected_mac:
            return ()
        roots: list[str] = []
        for path in sorted(files):
            if not path.endswith("/mac_addresses"):
                continue
            host_root = path.removesuffix("/mac_addresses")
            observed = NativeInputComparator._host_macs(files, host_root)
            if expected_mac in observed:
                roots.append(host_root)
        return tuple(roots)

    @staticmethod
    def _host_macs(files: Mapping[str, bytes], host_root: str) -> frozenset[str]:
        """Read normalized MACs from one already-associated native host directory.

        :param files: Decoded native output files keyed by embedded path.
        :param host_root: Exact hostconfig directory selected through its first MAC.
        :returns: Normalized MAC identities for that one embedded host.
        """
        path = f"{host_root}/mac_addresses" if host_root else ""
        if path not in files:
            return frozenset()
        try:
            lines = files[path].decode("utf-8").splitlines()
        except UnicodeError:
            return frozenset()
        return frozenset(
            NativeInputComparator._normalized_mac(line.strip())
            for line in lines
            if line.strip()
        )

    @classmethod
    def compare(
        cls,
        node_input: NativeNodeInput,
        document: JSONObject,
        artifact_sha256: str,
    ) -> JSONObject:
        """Compare caller input with known native structures embedded in the ISO.

        This runs only after OpenShift has accepted the untouched input and created the
        ISO. It does not replace native validation or define an OpenShift schema.

        :param node_input: Exact caller input retained by the build action.
        :param document: Actual Ignition object extracted from the generated ISO.
        :param artifact_sha256: Exact generated ISO digest binding the observation.
        :returns: Non-secret field coverage and input identity for this artifact.
        :raises yaml.YAMLError: A caller or embedded native YAML document is unreadable.
        :raises TypeError: A provider-accepted YAML document has an unusable shape.
        :raises EmbeddedObservationError: Required embedded bytes are unavailable.
        """
        files = cls._embedded_files(document)
        matched: list[str] = []
        unverified: list[str] = []
        mismatched: list[str] = []

        def compare(
            name: str, expected: object, actual: object, *, present: bool
        ) -> None:
            if not present:
                unverified.append(name)
            elif cls._native_equal(expected, actual):
                matched.append(name)
            else:
                mismatched.append(name)

        nmstate = cls._embedded_yaml(files, _NMSTATE_MANIFEST)
        nmstate_specs = [item.get("spec") for item in nmstate]
        nmstate_specs = [item for item in nmstate_specs if isinstance(item, Mapping)]
        infraenv = cls._embedded_yaml(files, _INFRAENV_MANIFEST)
        infraenv_specs = [item.get("spec") for item in infraenv]
        infraenv_specs = [item for item in infraenv_specs if isinstance(item, Mapping)]
        if node_input.mac_address:
            expected_mac = cls._normalized_mac(node_input.mac_address)
            selected_specs = cls._specs_for_mac(nmstate_specs, expected_mac)
            selected_roots = cls._host_roots_for_mac(files, expected_mac)
            association_present = bool(selected_specs or selected_roots)
            association_unambiguous = (
                len(selected_specs) <= 1 and len(selected_roots) <= 1
            )
            compare(
                "mac_address",
                True,
                True,
                present=bool(expected_mac)
                and association_present
                and association_unambiguous,
            )
            if node_input.files:
                expected_documents = cls._yaml_documents(node_input.files[0].content)
                expected_network = expected_documents[0] if expected_documents else None
                observed_network = (
                    selected_specs[0].get("config")
                    if len(selected_specs) == 1
                    else None
                )
                compare(
                    "network_config",
                    expected_network,
                    observed_network,
                    present=expected_network is not None
                    and observed_network is not None,
                )
        else:
            expected_documents = cls._yaml_documents(node_input.files[0].content)
            expected = expected_documents[0] if expected_documents else {}
            hosts = expected.get("hosts", [])
            if not isinstance(hosts, Sequence) or isinstance(hosts, (str, bytes)):
                raise TypeError("native_hosts_not_array")
            for index, host_value in enumerate(hosts):
                if not isinstance(host_value, Mapping):
                    raise TypeError("native_host_not_object")
                host = host_value
                prefix = f"hosts[{index}]"
                path_mac, expected_mac = cls._first_host_mac(host)
                if not expected_mac:
                    unverified.extend(
                        f"{prefix}.{name}"
                        for name in sorted(set(host) & _COMPARED_HOST_FIELDS)
                    )
                    unverified.extend(
                        f"{prefix}.{name}"
                        for name in sorted(set(host) - _COMPARED_HOST_FIELDS)
                    )
                    continue
                selected_roots = cls._host_roots_for_mac(files, expected_mac)
                host_root = selected_roots[0] if len(selected_roots) == 1 else ""
                expected_interfaces = host.get("interfaces")
                selected_specs = cls._specs_for_mac(nmstate_specs, expected_mac)
                if "interfaces" in host:
                    observed = (
                        selected_specs[0].get("interfaces")
                        if len(selected_specs) == 1
                        else None
                    )
                    if observed is not None or "networkConfig" in host:
                        compare(
                            f"{prefix}.interfaces",
                            expected_interfaces,
                            observed,
                            present=observed is not None,
                        )
                    elif isinstance(expected_interfaces, Sequence) and not isinstance(
                        expected_interfaces, (str, bytes, bytearray)
                    ):
                        host_macs = cls._host_macs(files, host_root)
                        if not expected_interfaces:
                            unverified.append(f"{prefix}.interfaces")
                        for interface_index, interface_value in enumerate(
                            expected_interfaces
                        ):
                            interface_prefix = f"{prefix}.interfaces[{interface_index}]"
                            if not isinstance(interface_value, Mapping):
                                mismatched.append(interface_prefix)
                                continue
                            if "macAddress" in interface_value:
                                expected_interface_mac = cls._normalized_mac(
                                    interface_value["macAddress"]
                                )
                                compare(
                                    f"{interface_prefix}.macAddress",
                                    True,
                                    expected_interface_mac in host_macs,
                                    present=bool(
                                        host_root
                                        and expected_interface_mac
                                        and host_macs
                                    ),
                                )
                            if "name" in interface_value:
                                unverified.append(f"{interface_prefix}.name")
                            unverified.extend(
                                f"{interface_prefix}.{name}"
                                for name in sorted(
                                    set(interface_value) - {"macAddress", "name"}
                                )
                            )
                    else:
                        mismatched.append(f"{prefix}.interfaces")
                if "networkConfig" in host:
                    observed = (
                        selected_specs[0].get("config")
                        if len(selected_specs) == 1
                        else None
                    )
                    compare(
                        f"{prefix}.networkConfig",
                        host["networkConfig"],
                        observed,
                        present=observed is not None,
                    )
                if "rootDeviceHints" in host:
                    path = f"{host_root}/root-device-hints.yaml" if host_root else ""
                    observed_documents = cls._embedded_yaml(files, path)
                    observed = observed_documents[0] if observed_documents else None
                    compare(
                        f"{prefix}.rootDeviceHints",
                        host["rootDeviceHints"],
                        observed,
                        present=observed is not None,
                    )
                if "role" in host:
                    path = f"{host_root}/role" if host_root else ""
                    observed_role = cls._embedded_text(files, path)
                    compare(
                        f"{prefix}.role",
                        host["role"],
                        observed_role,
                        present=observed_role is not None,
                    )
                if "hostname" in host:
                    path = f"/etc/assisted/hostnames/{path_mac}" if path_mac else ""
                    observed = cls._embedded_text(files, path)
                    compare(
                        f"{prefix}.hostname",
                        host["hostname"],
                        observed,
                        present=bool(host_root) and observed is not None,
                    )
                unverified.extend(
                    f"{prefix}.{name}"
                    for name in sorted(set(host) - _COMPARED_HOST_FIELDS)
                )
            if "cpuArchitecture" in expected:
                supplied_architecture = expected["cpuArchitecture"]
                expected_value = (
                    _ARCHITECTURES.get(supplied_architecture, supplied_architecture)
                    if isinstance(supplied_architecture, str)
                    else supplied_architecture
                )
                observed = [
                    item["cpuArchitecture"]
                    for item in infraenv_specs
                    if "cpuArchitecture" in item
                ]
                selected = next(
                    (
                        item
                        for item in observed
                        if cls._native_equal(expected_value, item)
                    ),
                    None,
                )
                compare(
                    "cpuArchitecture",
                    expected_value,
                    selected,
                    present=bool(observed),
                )
            if "sshKey" in expected:
                supplied_key = expected["sshKey"]
                expected_value = (
                    supplied_key.strip("|\n\t")
                    if isinstance(supplied_key, str)
                    else supplied_key
                )
                observed = [
                    value.strip("|\n\t") if isinstance(value, str) else value
                    for item in infraenv_specs
                    if "sshAuthorizedKey" in item
                    for value in (item["sshAuthorizedKey"],)
                ]
                selected = next(
                    (
                        item
                        for item in observed
                        if cls._native_equal(expected_value, item)
                    ),
                    None,
                )
                compare("sshKey", expected_value, selected, present=bool(observed))
            if "bootArtifactsBaseURL" in expected:
                unverified.append("bootArtifactsBaseURL")
            unverified.extend(sorted(set(expected) - _COMPARED_NODE_FIELDS))

        result: MutableMapping[str, JSONValue] = {
            "status": NativeComparisonStatus.MATCHED
            if matched and not unverified and not mismatched
            else NativeComparisonStatus.UNVERIFIED,
            "artifact_sha256": artifact_sha256,
            "input_identity": node_input.evidence(),
            "matched_fields": matched,
            "unverified_fields": unverified,
        }
        if mismatched:
            result["status"] = NativeComparisonStatus.MISMATCH
            result["mismatched_fields"] = mismatched
        return result


def inspect_verified_node_image(
    state: OCPState,
    artifact: ArtifactHandle,
    node_input: Optional[NativeNodeInput] = None,
) -> JSONObject:
    """Inspect one independently verified node ISO artifact without retaining its Pod.

    :param state: Shared OCP state containing the exact prepared Pod.
    :param artifact: Exact size and digest identity read back from that Pod.
    :param node_input: Original native input for initial post-build comparison.
    :returns: Safe embedded-output and exact ISO identity evidence.
    :raises KubernetesError: If native inspection or embedded Ignition validation fails.
    """
    result = run_node_image_script(state, "inspect", artifact.path)
    if result.returncode:
        detail = sanitize(result.stderr or result.stdout, 4096)
        raise KubernetesError(
            f"node_image_inspection_failed:exit={result.returncode}:{detail}"
        )
    try:
        document = response_decoder().decode(result.stdout)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise KubernetesError("node_image_embedded_ignition_invalid") from exc
    if not isinstance(document, Mapping):
        raise KubernetesError("node_image_embedded_ignition_invalid")
    embedded = _embedded_evidence(result.stdout)
    state.embedded_ignition = embedded
    state.readback["embedded_ignition"] = embedded
    evidence: MutableMapping[str, JSONValue] = {
        "artifact": artifact.as_dict(),
        "embedded_ignition": embedded,
    }
    if node_input is not None:
        try:
            comparison = NativeInputComparator.compare(
                node_input, document, artifact.sha256
            )
        except (
            EmbeddedObservationError,
            TypeError,
            ValueError,
            UnicodeError,
            OSError,
            EOFError,
            RecursionError,
            binascii.Error,
            zlib.error,
            configparser.Error,
            yaml.YAMLError,
        ) as exc:
            raise KubernetesError(
                "node_image_native_output_comparison_unreadable"
            ) from exc
        state.readback["native_input_comparison"] = comparison
        evidence["native_input_comparison"] = comparison
        if comparison["status"] == NativeComparisonStatus.MISMATCH:
            fields = comparison.get("mismatched_fields", [])
            detail = ",".join(str(item) for item in fields)
            raise KubernetesError(f"node_image_native_input_mismatch:{detail}")
    return evidence


class VerifyNodeImage(NodeImageCallback):
    """Inspect actual embedded Ignition and retain its exact ISO source."""

    def run(self) -> JSONObject:
        """Verify native output and immediately retain its backing resources.

        :returns: Safe embedded-output and exact ISO size/checksum evidence.
        :raises KubernetesError: If build did not complete, inspection fails, output
            is not valid Ignition, or artifact read-back cannot be retained.
        """
        state = self.ocp_state
        if not state.build_completed:
            raise KubernetesError("node_image_build_incomplete")
        artifact = observe_artifact(state, self.action.build_path)
        evidence = inspect_verified_node_image(state, artifact, self.action.node_input)
        retain_verified_source(state, artifact)
        state.build_artifact = artifact
        state.readback["node_image_source"] = artifact.as_dict()
        return {
            "action": VERIFIED,
            "verified": True,
            **evidence,
        }


__all__ = ["VerifyNodeImage", "inspect_verified_node_image"]
