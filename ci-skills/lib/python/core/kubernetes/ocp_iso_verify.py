"""Native in-cluster verification for generated OpenShift node ISOs.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import configparser
import gzip
import hashlib
import io
import re
import zlib
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from typing import Final
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
from lib.python.core.kubernetes.ocp_iso import (
    NodeImageCallback,
    OCPState,
    run_node_image_script,
)
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


def inspect_verified_node_image(
    state: OCPState,
    artifact: ArtifactHandle,
) -> JSONObject:
    """Inspect one independently verified node ISO artifact without retaining its Pod.

    :param state: Shared OCP state containing the exact prepared Pod.
    :param artifact: Exact size and digest identity read back from that Pod.
    :returns: Safe embedded-output and exact ISO identity evidence.
    :raises KubernetesError: If native inspection or embedded Ignition validation fails.
    """
    result = run_node_image_script(state, "inspect", artifact.path)
    if result.returncode:
        detail = sanitize(result.stderr or result.stdout, 4096)
        raise KubernetesError(
            f"node_image_inspection_failed:exit={result.returncode}:{detail}"
        )
    embedded = _embedded_evidence(result.stdout)
    state.embedded_ignition = embedded
    state.readback["embedded_ignition"] = embedded
    return {
        "artifact": artifact.as_dict(),
        "embedded_ignition": embedded,
    }


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
        evidence = inspect_verified_node_image(state, artifact)
        retain_verified_source(state, artifact)
        state.build_artifact = artifact
        state.readback["node_image_source"] = artifact.as_dict()
        return {
            "action": VERIFIED,
            "verified": True,
            **evidence,
        }


__all__ = ["VerifyNodeImage", "inspect_verified_node_image"]
