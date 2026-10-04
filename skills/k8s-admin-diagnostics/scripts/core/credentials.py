"""Resolve effective CLI credential sources once for a diagnostic invocation."""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .catalog import GITHUB_TOKEN_VARIABLES, GITLAB_VARIABLES, github_variables
from .gitlab_session import BoundGitLabSession
from .provenance import ProvenanceError, skill_identity
from .target import GitLabOperationTarget, Target, TargetError, assert_external_path


@dataclass(frozen=True)
class CredentialSource:
    reference: str
    environment: dict[str, str | None] = field(repr=False)


@dataclass(frozen=True)
class Sources:
    github: CredentialSource | None
    gitlab: CredentialSource | None
    kubernetes: CredentialSource | None
    kubeconfig_files: tuple[Path, ...]
    execution_host: str
    # Content digests captured at bind time. Pinning the FILENAME is not
    # pinning the target: the gate verifies the server, then every later
    # command reopens the same mutable path and re-resolves the context by
    # name, so a file rewritten in between would redirect the commands with no
    # second comparison.
    kubeconfig_digests: tuple[str, ...] = ()


def _file_token(path: Path) -> str:
    """Read one selected private token without returning it to a report."""
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
    path: Path | None,
    names: tuple[str, ...],
    store: str,
    *,
    clear_names: tuple[str, ...] | None = None,
) -> CredentialSource:
    """Pin explicit file, first effective environment variable, or CLI store."""
    selected: str | None = None
    value: str | None = None
    if path is not None:
        selected = names[0]
        value = _file_token(path)
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
    overrides = {
        name: (value if name == selected else None)
        for name in (clear_names if clear_names is not None else names)
    }
    return CredentialSource(reference, overrides)


def _kubernetes_source(target: Target) -> tuple[CredentialSource, tuple[Path, ...]]:
    """Pin the effective kubeconfig path set without assuming an auth type."""
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
        assert_external_path(
            path, Path(__file__).resolve().parents[2], "kubeconfig"
        )
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


def resolve_skill_identity(explicit: str | None) -> dict[str, Any]:
    """Identify the executed skill by digest, with the revision as a claim.

    Replaces format-only revision resolution: a 40-hex string proves nothing
    about the code that ran, and `CI_COMMIT_SHA` belongs to whatever project
    invoked the skill, not to the skill. See `core.provenance`.
    """
    skill_root = Path(__file__).resolve().parents[2]
    try:
        return skill_identity(skill_root, explicit)
    except ProvenanceError as exc:
        raise TargetError(str(exc)) from exc


def kubeconfig_digests(paths: tuple[Path, ...]) -> tuple[str, ...]:
    """Digest each kubeconfig's bytes, in search-path order."""
    digests: list[str] = []
    for path in paths:
        try:
            digests.append(hashlib.sha256(path.read_bytes()).hexdigest())
        except OSError as exc:
            raise TargetError(f"kubeconfig_unreadable:{path}") from exc
    return tuple(digests)


def assert_kubeconfig_unchanged(sources: object) -> None:
    """Refuse a kubeconfig that changed after it was verified.

    Called immediately before each Kubernetes command is built, in the thread
    that builds it, so the refusal propagates out of the collector and blocks.
    Raising it inside the per-resource read path would be caught there and
    degraded into a PARTIAL report with silently missing resources.

    This is detect-and-block, not an atomic pin: a file swapped between this
    check and the command's own open is still possible. It closes the
    non-adversarial case that actually happens -- a login rewriting the
    kubeconfig mid-invocation.
    """
    if not isinstance(sources, Sources) or not sources.kubeconfig_digests:
        return
    current = kubeconfig_digests(sources.kubeconfig_files)
    if current != sources.kubeconfig_digests:
        raise TargetError("kubeconfig_changed_during_invocation")


def bind_sources(target: Target, *, revision: str | None = None) -> Target:
    """Freeze effective source selection for access checks and collectors."""
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
    """Pin one exact-host stored token noninteractively or refuse that source.

    The command runs outside a repository so a local glab profile cannot
    replace the selected host. Its stdout is held only in memory and is never
    included in an exception or report.
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
    revision: str | None = None,
) -> BoundGitLabSession:
    """Freeze the selected GitLab token for all checks and later API calls.

    An explicit file wins over environment and store. An unreadable selected
    source blocks; it never falls through to another credential or host.
    """
    if target_kind not in {"project", "group"} or not target_reference:
        raise TargetError("gitlab_target_missing")
    source = _token_source(
        target.gitlab.token_file,
        GITLAB_VARIABLES,
        f"glab-credential-store:{target.gitlab.host}",
    )
    if source.reference.startswith("glab-credential-store:"):
        token = _glab_store_token(target.gitlab.host)
    else:
        selected = [value for value in source.environment.values() if value is not None]
        if len(selected) != 1:
            raise TargetError("credential_source_unresolved")
        token = selected[0]
    identity = resolve_skill_identity(revision)
    # The controlled child cannot silently substitute another token or CI
    # login. The transport also pins --hostname to this same selected host.
    environment: dict[str, str | None] = {
        **dict.fromkeys(GITLAB_VARIABLES),
        "CI_JOB_TOKEN": None,
        "GLAB_ENABLE_CI_AUTOLOGIN": None,
        "GITLAB_HOST": target.gitlab.host,
        "GLAB_NO_PROMPT": "1",
        "GITLAB_TOKEN": token,
    }
    return BoundGitLabSession(
        origin=target.gitlab.url,
        host=target.gitlab.host,
        target_kind=target_kind,
        target_reference=target_reference,
        target_source=target_source,
        target_file=target.source_file,
        credential_source=source.reference,
        credential_digest="sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest(),
        execution_host=socket.getfqdn(),
        skill=identity,
        environment=MappingProxyType(environment),
    )
