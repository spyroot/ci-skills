"""Native registry copies and Harbor read-back shared by workflow callbacks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, cast
from urllib.parse import quote, urlencode

from .action import AbstractCallback, HarborCallback
from .cli_api import APIError
from .credentials import RegistryCredential, resolve_harbor_credential
from .http import HTTPClient
from .runtime import error_class, run_command_bounded
from .status import APPLIED, NO_OP
from .target import HarborTarget, Target, TargetError

HARBOR_SERVICE_KEY: Final = "harbor"
REGISTRY_SCHEME: Final = "docker://"
DIGEST_PREFIX: Final = "sha256:"
HARBOR_API: Final = "api/v2.0"
REGISTRY_TOKEN_ENDPOINT: Final = "service/token"
REGISTRY_TOKEN_SERVICE: Final = "harbor-registry"
COPY_FLAGS: Final = ("--all", "--preserve-digests")


@dataclass
class HarborService:
    """Selected registry, private native auth file, and bounded API client."""

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

    def digest(self, repository: str, reference: str) -> str | None:
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
                REGISTRY_SCHEME + image,
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
                REGISTRY_SCHEME + source,
                REGISTRY_SCHEME + destination,
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

    def authorize(self, repository: str, actions: tuple[str, ...]) -> dict[str, Any]:
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
        try:
            # This is a claim read from the authenticated TLS token endpoint,
            # not a client-side signature verification or a substitute for push.
            token = response["token"]
            payload = token.split(".")[1]
            claims = json.loads(
                base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            )
            granted = sorted(
                {
                    action
                    for entry in claims.get("access", [])
                    if entry.get("type") == "repository" and entry.get("name") == name
                    for action in entry.get("actions", [])
                }
            )
        except (KeyError, IndexError, TypeError, ValueError):
            raise APIError("registry_grant_invalid") from None
        if not set(actions).issubset(granted):
            raise APIError("registry_actions_not_granted")
        return {
            "identity": self.credential.username,
            "credential_source": self.credential.source,
            "origin": self.target.url,
            "repository": name,
            "granted_actions": granted,
        }


@dataclass(frozen=True)
class TagSnapshot:
    """Registry tag state captured before a workflow may mutate it."""

    service: HarborService = field(repr=False)
    repository: str
    tag: str
    prior_digest: str | None

    @classmethod
    def capture(cls, service: HarborService, repository: str, tag: str) -> TagSnapshot:
        """Read and retain the exact current state of one registry tag.

        :param service: Authenticated registry service.
        :param repository: Project-relative repository selected by the caller.
        :param tag: Exact mutable tag that a later operation may change.
        :returns: Snapshot that can restore the observed digest or absence.
        :raises APIError: If the current registry state cannot be read.
        """
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


class HarborAuth(HarborCallback[None, HarborService]):
    """Resolve existing Harbor credentials once and prove requested access."""

    def __init__(
        self, *, target: Target, repository: str, actions: tuple[str, ...], timeout: int
    ) -> None:
        """Bind the exact target and access needed by the following operations.

        :param target: Shared target resolver result.
        :param repository: Caller-selected repository under its Harbor project.
        :param actions: Required native registry capabilities.
        :param timeout: Bound for native commands and API requests.
        """
        super().__init__()
        self.target = target
        self.repository = repository
        self.actions = actions
        self.timeout = timeout
        self._path: Path | None = None
        self.evidence: dict[str, Any] = {}

    def run(self) -> HarborService:
        """Create one private native auth file and verify the selected grant.

        :returns: Shared Harbor service for dependent callbacks.
        :raises TargetError: If Harbor target or credentials are unavailable.
        :raises APIError: If native registry authorization fails.
        :raises OSError: If the temporary credential file cannot be created.
        """
        if self.target.harbor is None:
            raise TargetError("harbor_target_missing")
        credential = resolve_harbor_credential(self.target.harbor)
        descriptor, name = tempfile.mkstemp(
            prefix="ci-skills-registry-", suffix=".json"
        )
        self._path = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(credential.dockerconfig(self.target.harbor.host))
        service = HarborService(
            self.target.harbor, credential, self._path, self.timeout
        )
        self.evidence = service.authorize(self.repository, self.actions)
        self.context.services[HARBOR_SERVICE_KEY] = service
        return service

    def cleanup(self) -> None:
        """Remove only this invocation's temporary credential file.

        :raises OSError: If the private helper cannot be removed.
        """
        if self._path is not None:
            self._path.unlink(missing_ok=True)


@dataclass(frozen=True)
class PushAction:
    """Selected registry destination and an explicit or dependent source."""

    repository: str
    tag: str
    source: str | AbstractCallback[Any]


class HarborPush(HarborCallback[PushAction, dict[str, Any]]):
    """Publish an image with the native copy client and exact digest read-back."""

    def collect(self) -> None:
        """Initialize destination recovery before publication can change a tag."""
        self._tag_snapshot: TagSnapshot | None = None
        if self.action is None:
            raise ValueError("push_action_required")
        service = cast(HarborService, self.harbor)
        self._tag_snapshot = TagSnapshot.capture(
            service, self.action.repository, self.action.tag
        )

    def cleanup(self) -> None:
        """Restore or remove the destination tag when this workflow fails.

        A pre-existing tag is copied back from its prior content digest. A tag
        created by this invocation is removed instead.

        :raises APIError: If prior tag state cannot be restored and read back.
        """
        if not self.context.failed or self._tag_snapshot is None:
            return
        self._tag_snapshot.restore()

    def run(self) -> dict[str, Any]:
        """Skip an identical tag or copy and verify the requested image.

        :returns: Destination, digest, and independently verified action evidence.
        :raises ValueError: If the required source result is missing.
        :raises APIError: If native registry copy or read-back fails.
        """
        action = self.action
        if action is None:
            raise ValueError("push_action_required")
        service = cast(HarborService, self.harbor)
        source = action.source
        if isinstance(source, AbstractCallback):
            source = (
                source.result.get("image")
                if isinstance(source.result, dict)
                else getattr(source.result, "image", None)
            )
        if not isinstance(source, str) or not source:
            raise ValueError("source_image_required")
        expected = service.inspect(source)
        current = service.digest(action.repository, action.tag)
        destination = service.image(action.repository, action.tag)
        changed = current != expected
        digest = service.copy(source, destination) if changed else expected
        if service.digest(action.repository, action.tag) != digest:
            raise APIError("published_digest_mismatch")
        return {
            "action": APPLIED if changed else NO_OP,
            "verified": True,
            "image": service.image(action.repository, digest),
            "digest": digest,
        }
