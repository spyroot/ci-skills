"""Resolve release-compatible tool Pod inputs for native OpenShift node images.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import string
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Optional
from urllib.parse import quote

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.kubernetes.tool_pod_models import ToolPodSpec, ToolPodState
from lib.python.core.kubernetes.tool_pod_runtime import ToolPodK8SCallback
from lib.python.core.runtime import error_class, run_command_bounded

CLUSTER_VERSION_KIND: Final = "clusterversion"
CLUSTER_VERSION_NAME: Final = "version"
COREOS_INSTALLER_IMAGE: Final = "quay.io/coreos/coreos-installer:release"
DIGEST_PREFIX: Final = "sha256:"
OCP_CLIENT_ROOT: Final = "https://mirror.openshift.com/pub/openshift-v4/clients/ocp"
OCP_CHECKSUM_MANIFEST: Final = "sha256sum.txt"
OCP_CLIENT_DESTINATION: Final = "/tmp/ci-skills-tools/bin"
OCP_CLIENT_PLATFORM: Final = "linux"
OCP_CLIENT_VARIANT: Final = "rhel9"
KUBERNETES_ARCH_LABEL: Final = "kubernetes.io/arch"
CHECKSUM_OUTPUT_LIMIT: Final = 1024 * 1024
SHA256_LENGTH: Final = 64
SHA256_CHARACTERS: Final = frozenset(string.hexdigits)


@dataclass(frozen=True)
class NodeToolAction(SkillAction):
    """Select caller-supplied tools or request provider-backed discovery.

    :param tools: Optional exact CI12.0 input supplied by the caller.

    .. attribute :: tools
        :type: Optional[ToolPodSpec]

        Exact caller override, or ``None`` to resolve published defaults.
    """

    tools: Optional[ToolPodSpec] = None

    def __post_init__(self) -> None:
        """Reject a non-CI12.0 override before any provider read.

        :raises TypeError: If the supplied override is not a ``ToolPodSpec``.
        """
        if self.tools is not None and not isinstance(self.tools, ToolPodSpec):
            raise TypeError("node_image_tool_spec_invalid")


@dataclass
class NodeToolState(ToolPodState):
    """Retain resolved tools and safe release evidence in shared state.

    .. attribute :: resolved_tools
        :type: ToolPodSpec

        Exact caller override or provider-resolved CI12.0 input.

    .. attribute :: release_evidence
        :type: JSONObject

        Safe published identities used to resolve the default input.

    .. attribute :: node_selector
        :type: Mapping[str, str]

        Native placement selected from the cluster release architecture.
    """

    resolved_tools: ToolPodSpec = field(init=False)
    release_evidence: JSONObject = field(default_factory=dict)
    node_selector: Mapping[str, str] = field(default_factory=dict)


def _required_text(value: object, failure: str) -> str:
    """Require one nonempty provider string without coercion.

    :param value: Candidate field from a provider response.
    :param failure: Stable error returned for a missing or malformed value.
    :returns: Original nonempty provider text.
    :raises KubernetesError: If the value is not nonempty text.
    """
    if not isinstance(value, str) or not value:
        raise KubernetesError(failure)
    return value


def _release(document: JSONObject) -> tuple[str, str]:
    """Read the desired version and immutable image from ClusterVersion.

    :param document: Native ``ClusterVersion/version`` response.
    :returns: Desired release version and image selected by the cluster.
    :raises KubernetesError: If the provider response lacks either identity.
    """
    status = document.get("status")
    desired = status.get("desired") if isinstance(status, Mapping) else None
    if not isinstance(desired, Mapping):
        raise KubernetesError("node_image_cluster_release_missing")
    return (
        _required_text(
            desired.get("version"), "node_image_cluster_release_version_missing"
        ),
        _required_text(
            desired.get("image"), "node_image_cluster_release_image_missing"
        ),
    )


def _published_path_segment(value: str) -> str:
    """Encode one provider value as a single URL path segment.

    :param value: Provider-published release version.
    :returns: Percent-encoded path segment retaining no slash semantics.
    :raises KubernetesError: If the provider supplied unusable text.
    """
    if not value or any(character in value for character in ("\x00", "\n", "\r")):
        raise KubernetesError("node_image_cluster_release_version_invalid")
    return quote(value, safe="._-")


def _client_archive(version: str, architecture: str) -> tuple[str, str, str]:
    """Derive the official client archive and checksum-manifest URLs.

    :param version: Provider-selected OCP release version.
    :param architecture: Provider-selected release-image architecture.
    :returns: Archive basename, archive URL and published checksum URL.
    """
    encoded_version = _published_path_segment(version)
    encoded_architecture = _published_path_segment(architecture)
    archive = (
        f"openshift-client-{OCP_CLIENT_PLATFORM}-{encoded_architecture}-"
        f"{OCP_CLIENT_VARIANT}-{version}.tar.gz"
    )
    directory = f"{OCP_CLIENT_ROOT}/{encoded_version}"
    return (
        archive,
        f"{directory}/{quote(archive, safe='._-')}",
        (f"{directory}/{OCP_CHECKSUM_MANIFEST}"),
    )


def _checksum(manifest: str, archive: str) -> str:
    """Select one exact archive digest from a published sha256sum file.

    :param manifest: Exact UTF-8 text returned by the provider.
    :param archive: Expected archive basename.
    :returns: Lowercase hexadecimal SHA-256.
    :raises KubernetesError: If the entry is absent, duplicated, or malformed.
    """
    matches: list[str] = []
    for line in manifest.splitlines():
        digest, separator, name = line.partition(" ")
        if not separator or name.lstrip(" *") != archive:
            continue
        if len(digest) != SHA256_LENGTH or any(
            character not in SHA256_CHARACTERS for character in digest
        ):
            raise KubernetesError("node_image_client_checksum_invalid")
        matches.append(digest.lower())
    if len(matches) != 1:
        raise KubernetesError("node_image_client_checksum_unresolved")
    return matches[0]


def _published_text(url: str, timeout: int) -> str:
    """Read one bounded public provider document through the shared CLI runtime.

    :param url: Exact published HTTPS document URL.
    :param timeout: Positive transport time bound in seconds.
    :returns: Provider response text.
    :raises KubernetesError: If curl fails or returns an empty document.
    """
    result = run_command_bounded(
        (
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--max-time",
            str(timeout),
            url,
        ),
        timeout=timeout,
        max_stdout_bytes=CHECKSUM_OUTPUT_LIMIT,
    )
    if result.returncode:
        raise KubernetesError(f"node_image_published_read_failed:{error_class(result)}")
    if not result.stdout:
        raise KubernetesError("node_image_published_read_empty")
    return result.stdout


def _image_platform(document: JSONObject) -> tuple[str, str]:
    """Read the provider-observed operating system and architecture.

    :param document: Native ``oc image info -o json`` response.
    :returns: Operating system and architecture from the image configuration.
    :raises KubernetesError: If either platform component is absent.
    """
    configuration = document.get("config")
    if not isinstance(configuration, Mapping):
        raise KubernetesError("node_image_platform_missing")
    return (
        _required_text(configuration.get("os"), "node_image_operating_system_missing"),
        _required_text(
            configuration.get("architecture"), "node_image_architecture_missing"
        ),
    )


def _image_digest(document: JSONObject) -> str:
    """Read and validate the immutable content digest returned by OpenShift.

    :param document: Native ``oc image info -o json`` response.
    :returns: Provider-reported SHA-256 content digest.
    :raises KubernetesError: If the digest is absent or malformed.
    """
    digest = _required_text(
        document.get("digest"), "node_image_inspector_digest_missing"
    )
    value = digest.removeprefix(DIGEST_PREFIX)
    if (
        not digest.startswith(DIGEST_PREFIX)
        or len(value) != SHA256_LENGTH
        or any(character not in SHA256_CHARACTERS for character in value)
    ):
        raise KubernetesError("node_image_inspector_digest_invalid")
    return digest.lower()


def _image_repository(image: str) -> str:
    """Remove an OCI tag while retaining a registry port when present.

    :param image: Provider-owned tag or digest reference.
    :returns: Repository portion without a tag or digest.
    """
    repository = image.partition("@")[0]
    slash, colon = repository.rfind("/"), repository.rfind(":")
    if colon > slash:
        repository = repository[:colon]
    return repository


def _image_info(
    kubernetes: KubernetesService,
    image: str,
    architecture: Optional[str] = None,
) -> JSONObject:
    """Read one OCI identity with the target-bound OpenShift client.

    :param kubernetes: Verified target-bound Kubernetes service.
    :param image: Exact provider image tag or digest.
    :param architecture: Optional platform selected from the cluster release.
    :returns: Native image information object.
    :raises KubernetesError: If the response is not a JSON object.
    """  # noqa: DOC502 - KubernetesService.json reports native command failures.
    arguments = ["image", "info", image]
    if architecture is not None:
        arguments.append(f"--filter-by-os={OCP_CLIENT_PLATFORM}/{architecture}")
    arguments.extend(("-o", "json"))
    document = kubernetes.json(*arguments, openshift=True)
    if not isinstance(document, Mapping):
        raise KubernetesError("node_image_image_info_invalid")
    return document


def _immutable_public_image(
    kubernetes: KubernetesService,
    image: str,
    architecture: str,
) -> str:
    """Resolve one public OCI tag to the selected platform's digest.

    :param kubernetes: Verified target-bound Kubernetes service.
    :param image: Public provider image tag.
    :param architecture: Architecture selected from the cluster release image.
    :returns: Repository pinned to the platform-specific provider digest.
    :raises KubernetesError: If the image platform differs from the release.
    """
    document = _image_info(kubernetes, image, architecture)
    operating_system, observed_architecture = _image_platform(document)
    if operating_system != OCP_CLIENT_PLATFORM or observed_architecture != architecture:
        raise KubernetesError("node_image_inspector_platform_mismatch")
    return f"{_image_repository(image)}@{_image_digest(document)}"


def _resolved_document(image: str, archive_url: str, checksum: str) -> JSONObject:
    """Build the existing CI12.0 input without a parallel tool schema.

    :param image: Immutable official CoreOS installer image.
    :param archive_url: Release-matched official OpenShift client archive.
    :param checksum: Published archive SHA-256.
    :returns: Existing tool-Pod document consumed by ``ToolPodSpec``.
    """
    return {
        "image": image,
        "dependencies": [
            {
                "name": "coreos-installer",
                "commands": ["coreos-installer"],
                "probe": ["coreos-installer", "--version"],
            },
            {
                "name": "rsync",
                "commands": ["rsync"],
                "yum": ["rsync"],
                "probe": ["rsync", "--version"],
            },
            {
                "name": "oc",
                "commands": ["oc"],
                "probe": ["oc", "version", "--client", "-o", "json"],
                "linux": {
                    "method": "archive",
                    "url": archive_url,
                    "sha256": checksum,
                    "destination": OCP_CLIENT_DESTINATION,
                    "files": ["oc"],
                    "stripComponents": 0,
                },
            },
        ],
    }


class ResolveNodeImageTools(ToolPodK8SCallback[NodeToolAction]):
    """Resolve one exact CI12.0 tool specification before any mutation."""

    @property
    def node_tool_state(self) -> NodeToolState:
        """Return the composed state used by tool discovery.

        :returns: Shared node-tool state.
        :raises TypeError: If the workflow supplied another state class.
        """
        state = self.tool_state
        if not isinstance(state, NodeToolState):
            raise TypeError("node_image_tool_state_required")
        return state

    def run(self) -> JSONObject:
        """Retain a caller override or resolve exact published defaults.

        :returns: Safe source, release and immutable tool identities.
        :raises KubernetesError: If cluster or published identities cannot be read.
        :raises OSError: If a required native client cannot start.
        """  # noqa: DOC502,DOC503 - provider and native reads propagate failures.
        if self.action.tools is not None:
            tools = self.action.tools
            node_selector: Mapping[str, str] = {}
            evidence: JSONObject = {
                "source": "caller",
                "tool_fingerprint": tools.fingerprint,
                "tool_image": tools.image,
            }
        else:
            cluster_version = self.kubernetes.read(
                CLUSTER_VERSION_KIND, CLUSTER_VERSION_NAME, ""
            )
            if cluster_version is None:
                raise KubernetesError("node_image_cluster_version_missing")
            version, release_image = _release(cluster_version)
            release_platform = _image_info(self.kubernetes, release_image)
            operating_system, architecture = _image_platform(release_platform)
            if operating_system != OCP_CLIENT_PLATFORM:
                raise KubernetesError("node_image_release_platform_invalid")
            archive, archive_url, checksum_url = _client_archive(version, architecture)
            checksum = _checksum(
                _published_text(checksum_url, self.kubernetes.timeout), archive
            )
            inspector_image = _immutable_public_image(
                self.kubernetes, COREOS_INSTALLER_IMAGE, architecture
            )
            tools = ToolPodSpec.from_document(
                _resolved_document(inspector_image, archive_url, checksum)
            )
            node_selector = {KUBERNETES_ARCH_LABEL: architecture}
            evidence = {
                "source": "provider",
                "cluster_release_version": version,
                "cluster_release_image": release_image,
                "cluster_release_operating_system": operating_system,
                "cluster_release_architecture": architecture,
                "inspector_image": inspector_image,
                "oc_archive": archive_url,
                "oc_archive_sha256": checksum,
                "tool_fingerprint": tools.fingerprint,
            }
        self.node_tool_state.resolved_tools = tools
        self.node_tool_state.release_evidence = evidence
        self.node_tool_state.node_selector = node_selector
        return evidence
