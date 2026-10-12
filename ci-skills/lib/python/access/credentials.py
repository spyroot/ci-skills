"""Resolve effective CLI credential sources once for a diagnostic invocation.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import socket
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from dataclasses import dataclass, field, replace
from os import PathLike
from pathlib import Path
from types import MappingProxyType
from typing import Final, Optional, Union

from lib.python.core.catalog import (
    GITHUB_TOKEN_VARIABLES,
    GITLAB_VARIABLES,
    github_variables,
)
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
from lib.python.core.paths import SKILL_ROOT
from lib.python.core.provenance import ProvenanceError, SkillIdentity, skill_identity
from lib.python.core.target import (
    GitLabOperationTarget,
    HarborTarget,
    Target,
    TargetError,
    assert_external_path,
)


@dataclass(frozen=True)
class CredentialSource:
    """Resolved credential source and child-process environment overrides.

    :param reference: Reportable source identifier without credential contents.
    :param environment: Variables to set; ``None`` removes an ambient variable.

    .. attribute :: reference
        :type: str

        Reportable source identifier without credential contents.

    .. attribute :: environment
        :type: Mapping[str, Optional[str]]

        Variables to set; ``None`` removes an ambient variable.
    """

    reference: str
    environment: Mapping[str, Optional[str]] = field(repr=False)


@dataclass(frozen=True)
class Sources:
    """Credential and kubeconfig selections frozen for one invocation.

    :param github: Selected GitHub credential source, or ``None`` if inactive.
    :param gitlab: Selected GitLab credential source, or ``None`` if inactive.
    :param kubernetes: Selected kubeconfig environment, or ``None`` if inactive.
    :param kubeconfig_files: Resolved kubeconfig files in search-path order.
    :param execution_host: Hostname where source selection occurred.
    :param kubeconfig_digests: SHA-256 digests used to detect later file changes.

    .. attribute :: github
        :type: Optional[CredentialSource]

        Selected GitHub credential source, or ``None`` if inactive.

    .. attribute :: gitlab
        :type: Optional[CredentialSource]

        Selected GitLab credential source, or ``None`` if inactive.

    .. attribute :: kubernetes
        :type: Optional[CredentialSource]

        Selected kubeconfig environment, or ``None`` if inactive.

    .. attribute :: kubeconfig_files
        :type: tuple[Path, ...]

        Resolved kubeconfig files in search-path order.

    .. attribute :: execution_host
        :type: str

        Hostname where source selection occurred.

    .. attribute :: kubeconfig_digests
        :type: tuple[str, ...]

        SHA-256 digests used to detect later file changes.
    """

    github: Optional[CredentialSource]
    gitlab: Optional[CredentialSource]
    kubernetes: Optional[CredentialSource]
    kubeconfig_files: tuple[Path, ...]
    execution_host: str
    # Content digests captured at bind time. Pinning the FILENAME is not
    # pinning the target: the gate verifies the server, then every later
    # command reopens the same mutable path and re-resolves the context by
    # name, so a file rewritten in between would redirect the commands with no
    # second comparison.
    kubeconfig_digests: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedGitLabCredential:
    """GitLab token retained in memory with its reportable origin.

    :param source: Credential source identifier without the token value.
    :param token: Selected secret, excluded from representation and comparison.

    .. attribute :: source
        :type: str

        Credential source identifier without the token value.

    .. attribute :: token
        :type: str

        Selected secret, excluded from representation and comparison.
    """

    source: str
    token: str = field(repr=False, compare=False)


class GitLabAuthentication(ABC):
    """Bind one GitLab target through a selectable credential source."""

    def __init__(self, target: GitLabOperationTarget):
        """Retain the selected GitLab target for this authentication attempt.

        :param target: Validated host and target configuration to bind.
        """
        self._target = target

    @abstractmethod
    def resolve(self) -> ResolvedGitLabCredential:
        """Resolve the selected credential without exposing it in a report.

        :returns: Token and reportable source for the exact target host.
        :raises TargetError: If the selected source is empty or unreadable.
        """

    def bind(
        self,
        *,
        target_kind: str,
        target_reference: str,
        target_source: str,
        revision: Optional[str] = None,
    ) -> BoundGitLabSession:
        """Bind credential, host, target, and skill identity for one operation.

        :param target_kind: Exact GitLab target kind, project or group.
        :param target_reference: Exact project or group reference.
        :param target_source: Reportable source of the selected target.
        :param revision: Explicit skill revision for an installed copy.
        :returns: Session used for access read-back and later API operations.
        :raises TargetError: If target selection or credential binding fails.
        """
        if target_kind not in {"project", "group"} or not target_reference:
            raise TargetError("gitlab_target_missing")
        credential = self.resolve()
        identity = resolve_skill_identity(revision)
        environment: Mapping[str, Optional[str]] = {
            **dict.fromkeys(GITLAB_VARIABLES),
            "CI_JOB_TOKEN": None,
            "GLAB_ENABLE_CI_AUTOLOGIN": None,
            "GITLAB_HOST": self._target.gitlab.host,
            "GLAB_NO_PROMPT": "1",
            "GITLAB_TOKEN": credential.token,
        }
        return BoundGitLabSession(
            origin=self._target.gitlab.url,
            host=self._target.gitlab.host,
            target_kind=target_kind,
            target_reference=target_reference,
            target_source=target_source,
            target_file=self._target.source_file,
            credential_source=credential.source,
            credential_digest=(
                "sha256:" + hashlib.sha256(credential.token.encode("utf-8")).hexdigest()
            ),
            execution_host=socket.getfqdn(),
            skill=identity,
            environment=MappingProxyType(environment),
        )


class ConfiguredGitLabAuthentication(GitLabAuthentication):
    """Resolve the declared file, environment, or exact-host glab store."""

    def resolve(self) -> ResolvedGitLabCredential:
        """Select one credential without falling through after a bad source.

        :returns: Token and source selected by the target configuration.
        :raises TargetError: If the selected token cannot be read or pinned.
        """
        source = _token_source(
            self._target.gitlab.token_file,
            GITLAB_VARIABLES,
            f"glab-credential-store:{self._target.gitlab.host}",
        )
        if source.reference.startswith("glab-credential-store:"):
            token = _glab_store_token(self._target.gitlab.host)
        else:
            selected = [
                value for value in source.environment.values() if value is not None
            ]
            if len(selected) != 1:
                raise TargetError("credential_source_unresolved")
            token = selected[0]
        return ResolvedGitLabCredential(source.reference, token)


def read_token_file(path: Union[str, PathLike[str]]) -> str:
    """Read the selected private token file for a child provider client.

    :param path: String or text path-like filename selected by runtime configuration;
        its file must contain UTF-8 text and be at most 4096 bytes.
    :returns: Nonempty token stripped of surrounding whitespace.
    :raises TypeError: If path is not a string or text path-like object.
    :raises TargetError: The file is unreadable, oversized, undecodable, empty,
        or contains whitespace inside the token.
    """  # noqa: DOC503 - Path construction rejects non-path arguments.
    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size > 4096:
            raise TargetError("credential_file_unavailable")
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise TargetError("credential_file_unavailable") from exc
    if not value or any(character.isspace() for character in value):
        raise TargetError("credential_file_invalid")
    return value


def _token_source(
    path: Optional[Path],
    names: Sequence[str],
    store: str,
    *,
    clear_names: Optional[Sequence[str]] = None,
) -> CredentialSource:
    """Select a token file, an environment variable, or the native CLI store.

    :param path: Explicit token file; ``None`` enables environment/store selection.
    :param names: Nonempty ordered sequence of accepted credential variable names.
    :param store: Reportable store identifier when no file or variable is selected.
    :param clear_names: Variables to clear from child environments; ``None`` uses
        the accepted credential names. The selected token is always exported.
    :returns: Source reference and environment overrides for the selected token.
    :raises TargetError: If the selected file is invalid or a selected environment
        variable is empty; no fallback is attempted after that failure.
    :raises IndexError: If a token file is selected without any accepted variable names.
    """  # noqa: DOC503 - Selecting the first variable can raise IndexError.
    selected: Optional[str] = None
    value: Optional[str] = None
    if path is not None:
        selected = names[0]
        value = read_token_file(path)
        reference = f"file:{path.resolve()}"
    else:
        for name in names:
            if name in os.environ:
                selected = name
                value = os.environ[name]
                if not value:
                    raise TargetError(f"{name} is empty")
                reference = f"env:{name}"
                break
        else:
            reference = store
    overrides: MutableMapping[str, Optional[str]] = {
        name: None for name in (clear_names if clear_names is not None else names)
    }
    if selected is not None:
        overrides[selected] = value
    return CredentialSource(reference, overrides)


def _kubernetes_source(target: Target) -> tuple[CredentialSource, tuple[Path, ...]]:
    """Resolve and verify the configured kubeconfig search path.

    :param target: Runtime target containing Kubernetes configuration.
    :returns: Credential-source record exporting ``KUBECONFIG`` and its ordered
        tuple of resolved paths.
    :raises TargetError: If Kubernetes is absent or a selected kubeconfig is empty,
        unreadable, missing, or located inside the installed skill.
    """
    if target.kubernetes is None:
        raise TargetError("kubernetes_target_missing")
    if target.kubernetes.kubeconfigs:
        # A declared search path needs no environment export, so a cold run
        # cannot silently fall through to a different cluster's default
        # kubeconfig.
        paths = tuple(path.resolve() for path in target.kubernetes.kubeconfigs)
        reference = "target:kubernetes.kubeconfigs"
    elif target.kubernetes.kubeconfig is not None:
        paths = (target.kubernetes.kubeconfig.resolve(),)
        reference = target.kubernetes_source_reference or f"file:{paths[0]}"
    elif "KUBECONFIG" in os.environ:
        parts = os.environ["KUBECONFIG"].split(os.pathsep)
        if any(not part for part in parts):
            raise TargetError("kubeconfig_source_empty")
        paths = tuple(Path(part).expanduser().resolve() for part in parts)
        reference = "env:KUBECONFIG"
    else:
        paths = ((Path.home() / ".kube" / "config").resolve(),)
        reference = f"kubectl-default:{paths[0]}"
    if not paths:
        raise TargetError("kubeconfig_source_empty")
    for path in paths:
        assert_external_path(path, SKILL_ROOT, "kubeconfig")
        try:
            if not path.is_file() or not path.stat().st_size:
                raise TargetError(f"kubeconfig_unavailable:{path}")
            with path.open("rb") as handle:
                handle.read(1)
        except OSError as exc:
            raise TargetError(f"kubeconfig_unreadable:{path}") from exc
    paths = tuple(path.resolve() for path in paths)
    return CredentialSource(
        reference, {"KUBECONFIG": os.pathsep.join(str(path) for path in paths)}
    ), paths


def resolve_skill_identity(explicit: Optional[str]) -> SkillIdentity:
    """Identify the installed skill bytes and claimed source revision.

    :param explicit: Optional revision claim supplied by the caller.
    :returns: Provenance mapping containing the content digest and revision claim.
    :raises TargetError: If installed provenance cannot be resolved or verified.
    """
    try:
        return skill_identity(SKILL_ROOT, explicit)
    except ProvenanceError as exc:
        raise TargetError(str(exc)) from exc


def kubeconfig_digests(paths: Sequence[Path]) -> tuple[str, ...]:
    """Hash kubeconfig contents in their configured search order.

    :param paths: Ordered kubeconfig paths to read.
    :returns: Tuple of hexadecimal SHA-256 digests in the same order.
    :raises TargetError: If any selected kubeconfig cannot be read.
    """
    digests: MutableSequence[str] = []
    for path in paths:
        try:
            digests.append(hashlib.sha256(path.read_bytes()).hexdigest())
        except OSError as exc:
            raise TargetError(f"kubeconfig_unreadable:{path}") from exc
    return tuple(digests)


def assert_kubeconfig_unchanged(sources: Optional[Sources]) -> None:
    """Reject kubeconfig changes after source binding.

    :param sources: Bound source record; None means the target has not been bound.
        A record with no digests has no pinned files to check.
    :raises TargetError: If a pinned file is unreadable or its digest changed.

    The check detects changes before command construction; it does not make
    subsequent file access atomic.
    """
    if sources is None or not sources.kubeconfig_digests:
        return
    current = kubeconfig_digests(sources.kubeconfig_files)
    if current != sources.kubeconfig_digests:
        raise TargetError("kubeconfig_changed_during_invocation")


def bind_sources(target: Target, *, revision: Optional[str] = None) -> Target:
    """Freeze active provider credentials and skill identity for an invocation.

    :param target: Runtime target declaring the surfaces required by the command.
    :param revision: Optional claimed revision of the installed skill.
    :returns: Copy of the target containing selected sources, kubeconfig digests,
        execution host, and skill provenance.
    :raises TargetError: If an active surface, selected credential, kubeconfig,
        or installed skill identity cannot be resolved.
    """
    github = None
    if "github" in target.active_surfaces:
        if target.github is None:
            raise TargetError("github_target_missing")
        github = _token_source(
            target.github.token_file,
            github_variables(target.github.host),
            f"gh-credential-store:{target.github.host}",
            clear_names=GITHUB_TOKEN_VARIABLES,
        )
    gitlab = None
    if "gitlab" in target.active_surfaces:
        if target.gitlab is None:
            raise TargetError("gitlab_target_missing")
        gitlab = _token_source(
            target.gitlab.token_file,
            GITLAB_VARIABLES,
            f"glab-credential-store:{target.gitlab.host}",
        )
    kube, files = (
        _kubernetes_source(target)
        if "kubernetes" in target.active_surfaces
        else (None, ())
    )
    sources = Sources(
        github,
        gitlab,
        kube,
        files,
        socket.getfqdn(),
        kubeconfig_digests(files),
    )
    identity = resolve_skill_identity(revision)
    return replace(
        target,
        sources=sources,
        skill=identity,
        tested_revision=identity["revision"]["value"],
    )


def _glab_store_token(host: str) -> str:
    """Read one exact-host glab token without prompting.

    :param host: Provider hostname whose native CLI credential entry is selected.
    :returns: Nonempty stored token, retained only in process memory.
    :raises TargetError: If glab cannot return a valid token within ten seconds.
    """
    environment = os.environ.copy()
    for name in (*GITLAB_VARIABLES, "CI_JOB_TOKEN", "GLAB_ENABLE_CI_AUTOLOGIN"):
        environment.pop(name, None)
    environment["GITLAB_HOST"] = host
    environment["GLAB_NO_PROMPT"] = "1"
    try:
        result = subprocess.run(
            ["glab", "config", "get", "token", "--host", host],
            cwd="/",
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TargetError("credential_store_unpinnable") from exc
    if result.returncode:
        raise TargetError("credential_store_unpinnable")
    value = result.stdout.strip()
    if not value or len(value) > 4096 or any(char.isspace() for char in value):
        raise TargetError("credential_store_unpinnable")
    return value


def bind_gitlab_session(
    target: GitLabOperationTarget,
    *,
    target_kind: str,
    target_reference: str,
    target_source: str,
    revision: Optional[str] = None,
) -> BoundGitLabSession:
    """Bind the selected target through the shared authentication interface.

    :param target: Validated GitLab host and credential configuration.
    :param target_kind: Exact GitLab target kind, project or group.
    :param target_reference: Exact project or group reference.
    :param target_source: Reportable source of the selected target.
    :param revision: Explicit skill revision for an installed copy.
    :returns: Bound session for access read-back and API operations.
    :raises TargetError: If target selection or credential binding fails.
    """  # noqa: DOC502 - ConfiguredGitLabAuthentication.bind validates inputs.
    return ConfiguredGitLabAuthentication(target).bind(
        target_kind=target_kind,
        target_reference=target_reference,
        target_source=target_source,
        revision=revision,
    )


MAX_REGISTRY_CREDENTIAL_BYTES: Final = 64 * 1024


@dataclass(frozen=True)
class RegistryCredential:
    """Registry identity and secret retained only in memory.

    :param username: Selected registry username or robot-account name.
    :param password: Selected secret, excluded from representation and comparison.
    :param source: Reportable credential-file reference.

    .. attribute :: username
        :type: str

        Selected registry username or robot-account name.

    .. attribute :: password
        :type: str

        Selected secret, excluded from representation and comparison.

    .. attribute :: source
        :type: str

        Reportable credential-file reference.
    """

    username: str
    password: str = field(repr=False, compare=False)
    source: str

    def dockerconfig(self, host: str) -> bytes:
        """Encode native registry credentials for a private temporary file.

        :param host: Exact configured registry host.
        :returns: Docker config bytes; callers must never emit these bytes.
        """
        auth = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return json.dumps({"auths": {host: {"auth": auth}}}).encode()


def resolve_harbor_credential(target: HarborTarget) -> RegistryCredential:
    """Read the selected Harbor files without rewriting their stored format.

    :param target: Endpoint and target-relative file selection.
    :returns: Registry identity, secret, and reportable source.
    :raises TargetError: If the selected files are unavailable or malformed.
    """
    if target.credential_file is None:
        if target.username_file is None or target.password_file is None:
            raise TargetError("harbor_credential_selection_invalid")
        return RegistryCredential(
            read_token_file(target.username_file),
            read_token_file(target.password_file),
            f"file:{target.username_file} -> file:{target.password_file}",
        )
    path = target.credential_file
    try:
        if not path.is_file() or path.stat().st_size > MAX_REGISTRY_CREDENTIAL_BYTES:
            raise TargetError("credential_file_unavailable")
        raw = path.read_text(encoding="utf-8").strip()
        if raw.startswith("{"):
            value = json.loads(raw)
            if "auths" in value:
                entries = value["auths"]
                selected = entries.get(target.host, entries.get(target.url))
                if not isinstance(selected, dict):
                    raise TargetError("registry_credential_host_missing")
                if "auth" in selected:
                    raw = base64.b64decode(selected["auth"], validate=True).decode()
                else:
                    raw = f"{selected['username']}:{selected['password']}"
            else:
                raw = f"{value['username']}:{value['password']}"
        username, separator, password = raw.partition(":")
        if (
            not separator
            or not username
            or not password
            or any(char.isspace() for char in raw)
        ):
            raise TargetError("credential_file_invalid")
    except (
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
        TypeError,
        binascii.Error,
    ) as exc:
        raise TargetError("credential_file_invalid") from exc
    return RegistryCredential(username, password, f"file:{path}")
