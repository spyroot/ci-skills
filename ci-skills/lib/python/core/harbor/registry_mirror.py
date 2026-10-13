"""Native registry copies and Harbor read-back shared by workflow callbacks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Optional
from urllib.parse import quote, urlencode

from lib.python.access.harbor_credentials import (
    RegistryCredential,
    resolve_harbor_credential,
)
from lib.python.core.abstract.callback import Callback
from lib.python.core.abstract.skill_action import ActionT_co, SkillAction
from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.cli_api import APIError, response_decoder
from lib.python.core.delivery_state import DeliveryState, ProviderAuthAction
from lib.python.core.json_types import JSONObject
from lib.python.core.runtime import error_class, run_command_bounded
from lib.python.core.target import HarborTarget, Target, TargetError
from lib.python.protocol.http import HTTPClient
from lib.python.sys.status import APPLIED, NO_OP

HARBOR_SERVICE_KEY: Final = SkillPluginType.HARBOR.value
REGISTRY_SCHEME: Final = "docker://"
DIGEST_PREFIX: Final = "sha256:"
HARBOR_API: Final = "api/v2.0"
REGISTRY_TOKEN_ENDPOINT: Final = "service/token"
REGISTRY_TOKEN_SERVICE: Final = "harbor-registry"
COPY_FLAGS: Final = ("--all", "--preserve-digests")


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


def _native_image(image: str) -> str:
    """Render a Docker transport reference accepted by Skopeo.

    :param image: Caller-selected image, optionally containing both tag and digest.
    :returns: Transport-prefixed reference; the digest takes precedence over a tag.
    """
    reference, separator, digest = image.partition("@")
    if separator:
        reference = image_at_digest(reference, digest)
    return REGISTRY_SCHEME + reference


@dataclass
class HarborService:
    """Bind one registry target to private native and API credentials.

    :param target: Selected Harbor origin, host, project, and credential reference.
    :param credential: Resolved registry username and password kept out of reports.
    :param authfile: Private native-client auth file path.
    :param timeout: Runtime bound for registry API and native commands.

    .. attribute :: target
        :type: HarborTarget

        Selected Harbor origin, host, project, and credential reference.

    .. attribute :: credential
        :type: RegistryCredential

        Resolved registry username and password kept out of reports.

    .. attribute :: authfile
        :type: Path

        Private native-client auth file path.

    .. attribute :: timeout
        :type: int

        Runtime bound for registry API and native commands.
    """

    target: HarborTarget
    credential: RegistryCredential = field(repr=False)
    authfile: Path = field(repr=False)
    timeout: int

    @property
    def dockerconfig(self) -> bytes:
        """Return native credential bytes for an explicitly owned Secret.

        :returns: Private Docker configuration; never include it in reports.
        """
        return self.credential.dockerconfig(self.target.host)

    @property
    def api(self) -> HTTPClient:
        """Return the shared HTTP client bound to this registry.

        :returns: Exact-origin API client with private Basic authentication.
        """
        encoded = base64.b64encode(
            f"{self.credential.username}:{self.credential.password}".encode()
        ).decode()
        return HTTPClient(
            self.target.url, authorization=f"Basic {encoded}", timeout=self.timeout
        )

    def image(self, repository: str, reference: str) -> str:
        """Build a native image reference from caller-selected values.

        :param repository: Repository relative to the configured project.
        :param reference: Exact tag or digest.
        :returns: Full image reference on the configured registry.
        """
        separator = "@" if reference.startswith(DIGEST_PREFIX) else ":"
        return f"{self.target.host}/{self.target.project}/{repository}{separator}{reference}"

    def artifact_path(self, repository: str, reference: str) -> str:
        """Encode Harbor's nested repository and artifact path.

        :param repository: Project-relative repository, including nested names.
        :param reference: Tag or manifest digest.
        :returns: Relative Harbor artifact API path.
        """
        project = quote(self.target.project, safe="")
        name = quote(quote(repository, safe=""), safe="")
        return f"{HARBOR_API}/projects/{project}/repositories/{name}/artifacts/{quote(reference, safe='')}"

    def digest(self, repository: str, reference: str) -> Optional[str]:
        """Read the registry's artifact digest, preserving an actual absence.

        :param repository: Project-relative repository.
        :param reference: Tag or digest to read back.
        :returns: Provider digest, or None for HTTP 404.
        :raises APIError: If access fails or the returned digest is malformed.
        """
        try:
            result = self.api.request("GET", self.artifact_path(repository, reference))
        except APIError as exc:
            if exc.reason == "provider_404":
                return None
            raise
        digest = result.get("digest") if isinstance(result, dict) else None
        if not isinstance(digest, str) or not digest.startswith(DIGEST_PREFIX):
            raise APIError("artifact_digest_missing")
        return digest

    def inspect(self, image: str) -> str:
        """Read a manifest through the native client and compute its exact digest.

        :param image: Caller-selected full registry image reference.
        :returns: SHA-256 digest of the returned manifest bytes.
        :raises APIError: If the native read fails or yields no manifest.
        """
        result = run_command_bounded(
            [
                "skopeo",
                "inspect",
                "--raw",
                "--authfile",
                str(self.authfile),
                _native_image(image),
            ],
            timeout=self.timeout,
        )
        if result.returncode or not result.stdout:
            raise APIError(
                error_class(result) if result.returncode else "manifest_missing"
            )
        return DIGEST_PREFIX + hashlib.sha256(result.stdout.encode()).hexdigest()

    def copy(self, source: str, destination: str) -> str:
        """Copy all manifests and independently prove destination digest equality.

        :param source: Full source image reference.
        :param destination: Full destination image reference.
        :returns: Independently observed destination digest.
        :raises APIError: If the copy or either independent read-back fails.
        """
        expected = self.inspect(source)
        result = run_command_bounded(
            [
                "skopeo",
                "copy",
                *COPY_FLAGS,
                "--authfile",
                str(self.authfile),
                _native_image(source),
                _native_image(destination),
            ],
            timeout=self.timeout,
        )
        if result.returncode:
            raise APIError(error_class(result))
        observed = self.inspect(destination)
        if observed != expected:
            raise APIError("published_digest_mismatch")
        return observed

    def delete_tag(self, repository: str, tag: str) -> bool:
        """Remove one selected tag without deleting other tags or shared content.

        :param repository: Project-relative repository selected by the caller.
        :param tag: Exact tag selected for deletion.
        :returns: Whether the tag existed and was removed.
        :raises APIError: If deletion or independent absence read-back fails.
        """
        digest = self.digest(repository, tag)
        if digest is None:
            return False
        path = self.artifact_path(repository, digest)
        self.api.request("DELETE", f"{path}/tags/{quote(tag, safe='')}")
        if self.digest(repository, tag) is not None:
            raise APIError("tag_delete_incomplete")
        return True

    def authorize(self, repository: str, actions: Sequence[str]) -> JSONObject:
        """Read the authenticated registry grant for the requested operation.

        :param repository: Selected project-relative repository.
        :param actions: Native registry capabilities needed by this operation.
        :returns: Safe identity, source, repository, and actual granted actions.
        :raises APIError: If the selected registry does not grant required actions.
        """
        name = f"{self.target.project}/{repository}"
        query = urlencode(
            {
                "service": REGISTRY_TOKEN_SERVICE,
                "scope": f"repository:{name}:{','.join(actions)}",
            }
        )
        response = self.api.request("GET", f"{REGISTRY_TOKEN_ENDPOINT}?{query}")
        granted = self._granted_actions(response, name)
        if not set(actions).issubset(granted):
            raise APIError("registry_actions_not_granted")
        return {
            "identity": self.credential.username,
            "credential_source": self.credential.source,
            "origin": self.target.url,
            "repository": name,
            "granted_actions": granted,
        }

    @staticmethod
    def _granted_actions(response: JSONObject, repository: str) -> Sequence[str]:
        """Read capability claims returned by the authenticated registry endpoint.

        This parses a response from the selected TLS authority; it does not claim
        independent JWT signature verification or successful image publication.

        :param response: JSON object returned by the registry token endpoint.
        :param repository: Exact project/repository scope requested by the caller.
        :returns: Sorted capabilities granted for that repository scope.
        :raises APIError: If token encoding, JSON, or capability fields are invalid.
        """
        token = response.get("token")
        if not isinstance(token, str):
            raise APIError("registry_grant_invalid")
        try:
            payload = token.split(".")[1]
            raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            claims = response_decoder().decode(raw.decode("utf-8"))
        except (IndexError, ValueError, UnicodeError, RecursionError):
            raise APIError("registry_grant_invalid") from None
        entries = claims.get("access", []) if isinstance(claims, Mapping) else None
        if not isinstance(entries, list):
            raise APIError("registry_grant_invalid")
        granted: set[str] = set()
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise APIError("registry_grant_invalid")
            actions = entry.get("actions", [])
            if not isinstance(actions, list) or any(
                not isinstance(item, str) for item in actions
            ):
                raise APIError("registry_grant_invalid")
            if entry.get("type") == "repository" and entry.get("name") == repository:
                granted.update(actions)
        return sorted(granted)


@dataclass(frozen=True)
class TagSnapshot:
    """Retain a registry tag's prior digest or absence for recovery.

    :param service: Authenticated Harbor service used for read-back and restoration.
    :param repository: Selected project-relative destination repository.
    :param tag: Exact mutable destination tag.
    :param prior_digest: Observed digest, or None if the tag was absent.

    .. attribute :: service
        :type: HarborService

        Authenticated Harbor service used for read-back and restoration.

    .. attribute :: repository
        :type: str

        Selected project-relative destination repository.

    .. attribute :: tag
        :type: str

        Exact mutable destination tag.

    .. attribute :: prior_digest
        :type: Optional[str]

        Observed digest, or None if the tag was absent.
    """

    service: HarborService = field(repr=False)
    repository: str
    tag: str
    prior_digest: Optional[str]

    @classmethod
    def capture(cls, service: HarborService, repository: str, tag: str) -> TagSnapshot:
        """Read and retain the exact current state of one registry tag.

        :param service: Authenticated registry service.
        :param repository: Project-relative repository selected by the caller.
        :param tag: Exact mutable tag that a later operation may change.
        :returns: Snapshot that can restore the observed digest or absence.
        :raises APIError: If the current registry state cannot be read.
        """  # noqa: DOC502 - Registry read/restore helpers propagate APIError.
        return cls(service, repository, tag, service.digest(repository, tag))

    def restore(self) -> bool:
        """Restore the captured digest or absence with independent read-back.

        :returns: Whether provider state required a write.
        :raises APIError: If restoration or its independent read-back fails.
        """
        current = self.service.digest(self.repository, self.tag)
        if current == self.prior_digest:
            return False
        if self.prior_digest is None:
            self.service.delete_tag(self.repository, self.tag)
        else:
            restored = self.service.copy(
                self.service.image(self.repository, self.prior_digest),
                self.service.image(self.repository, self.tag),
            )
            if restored != self.prior_digest:
                raise APIError("tag_recovery_digest_mismatch")
        if self.service.digest(self.repository, self.tag) != self.prior_digest:
            raise APIError("tag_recovery_readback_mismatch")
        return True


class HarborCallback(Callback[DeliveryState, ActionT_co]):
    """Read the private registry service assigned by authentication."""

    @property
    def plugin_type(self) -> SkillPluginType:
        """Identify the configuration shared by this plugin's callbacks.

        :returns: The harbor plugin's configuration cache key.
        """
        return SkillPluginType.HARBOR

    def read_config(self) -> JSONObject:
        """Reuse a caller-bound registry service for direct library operations.

        :returns: Empty evidence after retaining the existing private configuration.
        :raises TargetError: Authentication or the caller supplied no registry service.
        """  # noqa: DOC502 - Direct library callers provide the existing service.
        if not hasattr(self.state, "harbor"):
            raise TargetError(f"{SkillPluginType.HARBOR.value}_configuration_missing")
        self.state.harbor_target = self.harbor.target
        self.state.harbor_credential = self.harbor.credential
        return {}

    def is_configured(self) -> bool:
        """Check that target and credential selection completed in shared state.

        :returns: Whether the invocation has concrete registry configuration.
        """
        return hasattr(self.state, "harbor_target") and hasattr(
            self.state, "harbor_credential"
        )

    @property
    def harbor(self) -> HarborService:
        """Read the verified service retained in shared delivery state.

        :returns: Bound registry service with its private native auth file.
        :raises AttributeError: If composition omitted Harbor authentication.
        """  # noqa: DOC502 - Missing composition state raises AttributeError.
        return self.state.harbor


@dataclass(frozen=True)
class HarborAuthAction(ProviderAuthAction):
    """Describe the exact registry grant required by a delivery workflow.

    :param target: Runtime-selected target containing Harbor access configuration.
    :param timeout: Maximum seconds for each API or native registry command.
    :param repository: Project-relative repository whose grant will be verified.
    :param actions: Explicit native registry capabilities required by the workflow.

    .. attribute :: repository
        :type: str

        Project-relative repository whose grant will be verified.

    .. attribute :: actions
        :type: Sequence[str]

        Explicit native registry capabilities required by the workflow.
    """

    repository: str
    actions: Sequence[str]


class HarborAuth(HarborCallback[HarborAuthAction]):
    """Resolve existing credentials and prove the requested registry grant."""

    def __init__(
        self, target: Target, repository: str, actions: Sequence[str], timeout: int
    ):
        """Retain required authentication inputs and own any temporary auth file.

        :param target: Shared runtime target resolver result.
        :param repository: Caller-selected repository under its Harbor project.
        :param actions: Registry capabilities needed by the declared workflow.
        :param timeout: Maximum seconds for native commands and API requests.
        """
        super().__init__(HarborAuthAction(target, timeout, repository, actions))
        self._path: Optional[Path] = None

    def read_config(self) -> JSONObject:
        """Read this plugin's declared credential through the shared resolver once.

        :returns: Empty setup evidence; credential material remains private.
        :raises TargetError: The selected Harbor target or credentials are unavailable.
        """  # noqa: DOC503 - The existing credential reader validates its input files.
        target = self.action.target.harbor
        if target is None:
            raise TargetError("harbor_target_missing")
        credential = resolve_harbor_credential(target)
        self.state.harbor_target = target
        self.state.harbor_credential = credential
        return {}

    def run(self) -> JSONObject:
        """Create private native credentials and retain the verified service.

        :returns: Safe identity, origin, repository, and granted capabilities.
        :raises TargetError: If Harbor target or credentials are unavailable.
        :raises APIError: If the registry does not grant the required capabilities.
        :raises OSError: If the private credential file cannot be created or written.
        """  # noqa: DOC502 - Registry authorization and private-file writes can fail.
        action = self.action
        target = self.state.harbor_target
        credential = self.state.harbor_credential
        descriptor, name = tempfile.mkstemp(
            prefix="ci-skills-registry-", suffix=".json"
        )
        self._path = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(credential.dockerconfig(target.host))
        service = HarborService(target, credential, self._path, action.timeout)
        evidence = service.authorize(action.repository, action.actions)
        self.state.harbor = service
        self.state.access_evidence[HARBOR_SERVICE_KEY] = evidence
        return evidence

    def cleanup(self) -> JSONObject:
        """Remove this invocation's private native credential helper.

        :returns: Empty evidence after cleanup; no credential paths are reported.
        :raises OSError: If the created private helper cannot be removed.
        """  # noqa: DOC502 - Path.unlink can propagate filesystem errors.
        if self._path is not None:
            self._path.unlink(missing_ok=True)
        return {}


@dataclass(frozen=True)
class PushAction(SkillAction):
    """Select a registry destination for the preceding producer's image.

    :param repository: Project-relative destination repository.
    :param tag: Destination tag selected for publication and independent read-back.

    .. attribute :: repository
        :type: str

        Project-relative destination repository.

    .. attribute :: tag
        :type: str

        Destination tag selected for publication and independent read-back.
    """

    repository: str
    tag: str


class HarborPush(HarborCallback[PushAction]):
    """Publish the state-owned source image and retain its immutable destination."""

    def __init__(self, action: PushAction):
        """Retain the required destination and initialize recovery ownership.

        :param action: Destination repository and tag selected by the workflow.
        """
        super().__init__(action)
        self._tag_snapshot: Optional[TagSnapshot] = None

    def collect(self) -> JSONObject:
        """Capture the destination's prior digest or absence before publication.

        :returns: Empty report evidence; the recovery snapshot remains internal.
        :raises APIError: If the current destination tag cannot be read.
        """  # noqa: DOC502 - Registry read/restore helpers propagate APIError.
        self._tag_snapshot = TagSnapshot.capture(
            self.harbor, self.action.repository, self.action.tag
        )
        return {}

    def cleanup(self) -> JSONObject:
        """Restore the prior tag or absence after a failed workflow.

        :returns: Empty evidence after independent recovery verification.
        :raises APIError: If the captured tag state cannot be restored and read back.
        """  # noqa: DOC502 - Registry read/restore helpers propagate APIError.
        if self.state.failed and self._tag_snapshot is not None:
            self._tag_snapshot.restore()
        return {}

    def run(self) -> JSONObject:
        """Skip an identical tag or copy and verify the preceding producer's image.

        :returns: Applied/no-op destination, digest, and verified read-back evidence.
        :raises ValueError: If the producer supplied no immutable source image.
        :raises AttributeError: If composition omitted the source-image producer.
        :raises APIError: If native copying or independent digest verification fails.
        """  # noqa: DOC503 - Source state and registry helpers propagate failures.
        action, service = self.action, self.harbor
        source = self.state.source_image
        if not source or "@sha256:" not in source:
            raise ValueError("immutable_source_image_required")
        expected = service.inspect(source)
        current = service.digest(action.repository, action.tag)
        destination = service.image(action.repository, action.tag)
        changed = current != expected
        digest = service.copy(source, destination) if changed else expected
        if service.digest(action.repository, action.tag) != digest:
            raise APIError("published_digest_mismatch")
        self.state.published_image = service.image(action.repository, digest)
        return {
            "action": APPLIED if changed else NO_OP,
            "verified": True,
            "image": self.state.published_image,
            "digest": digest,
        }
