"""Read immutable OCI manifest identities with the shared native transport."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from lib.python.core.cli_api import APIError, response_decoder
from lib.python.core.json_types import (
    JSONObject,
    json_integer,
    json_object,
    json_objects,
    json_string,
)
from lib.python.core.runtime import CommandResult, error_class, run_command_bounded

REGISTRY_SCHEME: Final = "docker://"
DIGEST_PREFIX: Final = "sha256:"

_OCI_MANIFEST: Final = "application/vnd.oci.image.manifest.v1+json"
_DOCKER_MANIFEST: Final = "application/vnd.docker.distribution.manifest.v2+json"
_OCI_INDEX: Final = "application/vnd.oci.image.index.v1+json"
_DOCKER_INDEX: Final = "application/vnd.docker.distribution.manifest.list.v2+json"
_IMAGE_MANIFESTS: Final = frozenset((_OCI_MANIFEST, _DOCKER_MANIFEST))
_MANIFEST_INDEXES: Final = frozenset((_OCI_INDEX, _DOCKER_INDEX))
_IMAGE_CONFIGS: Final = frozenset(
    (
        "application/vnd.oci.image.config.v1+json",
        "application/vnd.docker.container.image.v1+json",
    )
)
_OCI_LAYER: Final = "application/vnd.oci.image.layer"
_IMAGE_LAYERS: Final = frozenset(
    {
        "application/vnd.docker.image.rootfs.diff.tar",
        "application/vnd.docker.image.rootfs.diff.tar.gzip",
        "application/vnd.docker.image.rootfs.foreign.diff.tar.gzip",
        *(
            f"{_OCI_LAYER}{distribution}.tar{compression}"
            for distribution in ("", ".nondistributable")
            for compression in ("", "+gzip", "+zstd")
        ),
    }
)
_DIGEST: Final = re.compile(rf"{DIGEST_PREFIX}[0-9a-f]{{64}}")
_PLATFORM_NAME: Final = re.compile(r"[a-z0-9][a-z0-9_]{0,31}")
_ATTESTATION_LABEL: Final = "vnd.docker.reference.type"
_ATTESTATION_MANIFEST: Final = "attestation-manifest"


def image_at_digest(image: str, digest: str) -> str:
    """Pin an image without retaining a tag that native registry clients reject.

    :param image: Repository with an optional tag or existing digest.
    :param digest: Manifest digest returned by the registry read-back.
    :returns: Repository and digest; a registry port is preserved and a tag is removed.
    """
    repository = image.partition("@")[0]
    slash = repository.rfind("/")
    colon = repository.rfind(":")
    if colon > slash:
        repository = repository[:colon]
    return f"{repository}@{digest}"


def skopeo_reference(image: str) -> str:
    """Render a Docker transport reference accepted by Skopeo.

    :param image: Caller-selected image, optionally containing both tag and digest.
    :returns: Transport-prefixed reference; the digest takes precedence over a tag.
    """
    reference, separator, digest = image.partition("@")
    if separator:
        reference = image_at_digest(reference, digest)
    return REGISTRY_SCHEME + reference


class ManifestCommand(Protocol):
    """Execute a bounded native client invocation."""

    def __call__(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
        """Execute the native command.

        :param argv: Native client arguments.
        :param timeout: Maximum command duration in seconds.
        :returns: Captured transport result.
        """
        ...


def inspect_manifest_digest(
    image: str,
    *,
    timeout: float,
    authfile: Path | None = None,
    command: ManifestCommand = run_command_bounded,
) -> str:
    """Hash the raw manifest or multi-platform index served by the registry.

    :param image: Full caller-selected registry reference.
    :param timeout: Maximum native read duration, in seconds.
    :param authfile: Explicit registry login file. Omission uses anonymous access.
    :param command: Bounded native transport for this workflow.
    :returns: Exact registry manifest SHA-256 digest.
    :raises APIError: The native read fails or returns no manifest.
    """  # noqa: DOC502 - Native manifest errors propagate.
    return _raw_digest(_read_raw_manifest(image, timeout, authfile, command))


def _read_raw_manifest(
    image: str, timeout: float, authfile: Path | None, command: ManifestCommand
) -> str:
    credentials = (
        ["--authfile", str(authfile)] if authfile is not None else ["--no-creds"]
    )
    result = command(
        ["skopeo", "inspect", "--raw", *credentials, skopeo_reference(image)],
        timeout=timeout,
    )
    if result.returncode or not result.stdout:
        raise APIError(error_class(result) if result.returncode else "manifest_missing")
    return result.stdout


def _raw_digest(raw: str) -> str:
    return DIGEST_PREFIX + hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ImageManifestIdentity:
    """Bind the digest pin to admissible native runtime image digests.

    .. attribute :: digest
        :type: str

        Exact SHA-256 of the registry manifest or index bytes.

    .. attribute :: runtime_digests
        :type: frozenset[str]

        The digest pin and direct, platform-bearing image manifest digests.
    """

    digest: str
    runtime_digests: frozenset[str]


def _descriptor_digest(descriptor: JSONObject) -> str:
    digest = json_string(descriptor.get("digest"))
    if (
        _DIGEST.fullmatch(digest) is None
        or not 0 < json_integer(descriptor.get("size")) <= 2**63 - 1
    ):
        raise ValueError("manifest_descriptor_invalid")
    return digest


def _artifact(document: JSONObject) -> bool:
    if "artifactType" in document:
        if not json_string(document["artifactType"]).strip():
            raise ValueError("manifest_artifact_type_invalid")
        return True
    raw_annotations = document.get("annotations", {})
    annotations = json_object(raw_annotations)
    return annotations.get(_ATTESTATION_LABEL) == _ATTESTATION_MANIFEST


def _index_images(document: JSONObject) -> frozenset[str]:
    if "config" in document or "layers" in document:
        raise ValueError("manifest_index_shape_invalid")
    images: set[str] = set()
    for child in json_objects(document.get("manifests")):
        digest = _descriptor_digest(child)
        media_type = json_string(child.get("mediaType"))
        if not media_type.strip():
            raise ValueError("manifest_child_media_type_invalid")
        if _artifact(child) or media_type in _MANIFEST_INDEXES:
            continue
        if media_type not in _IMAGE_MANIFESTS:
            raise ValueError("manifest_child_media_type_invalid")
        platform = json_object(child.get("platform"))
        os = json_string(platform.get("os"))
        architecture = json_string(platform.get("architecture"))
        if any(_PLATFORM_NAME.fullmatch(value) is None for value in (os, architecture)):
            raise ValueError("manifest_platform_invalid")
        if "unknown" not in (os, architecture):
            images.add(digest)
    if not images:
        raise ValueError("manifest_runtime_images_missing")
    return frozenset(images)


def _validate_image(document: JSONObject) -> None:
    if "manifests" in document:
        raise ValueError("manifest_image_shape_invalid")
    config = json_object(document.get("config"))
    _descriptor_digest(config)
    if json_string(config.get("mediaType")) not in _IMAGE_CONFIGS:
        raise ValueError("manifest_config_media_type_invalid")
    for layer in json_objects(document.get("layers")):
        _descriptor_digest(layer)
        if json_string(layer.get("mediaType")) not in _IMAGE_LAYERS:
            raise ValueError("manifest_layer_media_type_invalid")


def inspect_image_identity(
    image: str,
    *,
    timeout: float,
    authfile: Path | None = None,
    command: ManifestCommand = run_command_bounded,
) -> ImageManifestIdentity:
    """Read a runnable image manifest or index and bind its immutable digests.

    An index contributes only direct platform image manifests. Declared
    artifacts, unknown platforms, and nested indexes are not runtime evidence.

    :param image: Full caller-selected registry tag or SHA-256 digest reference.
    :param timeout: Maximum native read duration, in seconds.
    :param authfile: Explicit registry login file. Omission uses anonymous access.
    :param command: Bounded native transport for this workflow.
    :returns: Verified root digest and admissible runtime manifest digests.
    :raises APIError: The native read, digest pin, or manifest shape is invalid.
    """  # noqa: DOC503 - Invalid shapes are normalized to APIError.
    raw = _read_raw_manifest(image, timeout, authfile, command)
    if "\ufffd" in raw:
        raise APIError("manifest_encoding_invalid")
    digest = _raw_digest(raw)
    _, separator, pin = image.partition("@")
    if separator and pin != digest:
        raise APIError("manifest_digest_mismatch")
    try:
        manifest = json_object(response_decoder().decode(raw))
        if json_integer(manifest.get("schemaVersion")) != 2 or _artifact(manifest):
            raise ValueError("manifest_image_schema_invalid")
        media_type = json_string(manifest.get("mediaType"))
        if media_type in _MANIFEST_INDEXES:
            return ImageManifestIdentity(digest, _index_images(manifest) | {digest})
        if media_type not in _IMAGE_MANIFESTS:
            raise ValueError("manifest_media_type_unsupported")
        _validate_image(manifest)
    except (TypeError, ValueError, RecursionError):
        raise APIError("manifest_image_invalid") from None
    return ImageManifestIdentity(digest, frozenset((digest,)))
