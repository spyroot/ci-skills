"""Resolve effective CLI credential sources once for a diagnostic invocation."""

from __future__ import annotations

import hashlib
import os
import re
import socket
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

from .target import Target, TargetError, assert_external_path


@dataclass(frozen=True)
class CredentialSource:
    reference: str
    environment: dict[str, str | None] = field(repr=False)


@dataclass(frozen=True)
class Sources:
    github: CredentialSource
    gitlab: CredentialSource
    kubernetes: CredentialSource
    kubeconfig_files: tuple[Path, ...]
    kubeconfig_hashes: tuple[str, ...]
    execution_host: str


def github_token_names(host: str) -> tuple[str, str]:
    """Match gh's documented environment precedence for the selected host."""
    return (
        ("GH_TOKEN", "GITHUB_TOKEN")
        if host == "github.com" or host.endswith(".ghe.com")
        else ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
    )


def _file_digest(path: Path) -> str:
    """Hash credential configuration without exposing its bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def skill_digest(skill_root: Path | None = None) -> str:
    """Fingerprint the executable skill bytes for independent CI read-back."""
    root = skill_root or Path(__file__).resolve().parents[2]
    paths = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and (
            path.name == "SKILL.md"
            or path.suffix == ".py"
            or path.parent.name == "references"
            and path.suffix == ".md"
        )
        and "__pycache__" not in path.parts
    )
    if not paths:
        raise TargetError("skill_files_unavailable")
    digest = hashlib.sha256()
    for path in paths:
        if path.is_symlink():
            raise TargetError("skill_symlink_unexpected")
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(bytes.fromhex(_file_digest(path)))
    return digest.hexdigest()


def verify_kubeconfig_unchanged(target: Target) -> None:
    """Block if a selected kubeconfig changed during this invocation."""
    sources = target.sources if isinstance(target.sources, Sources) else None
    if sources is None:
        return
    try:
        current = tuple(_file_digest(path) for path in sources.kubeconfig_files)
    except OSError as exc:
        raise TargetError("kubeconfig_changed") from exc
    if current != sources.kubeconfig_hashes:
        raise TargetError("kubeconfig_changed")


def verify_skill_unchanged(target: Target) -> None:
    """Reject source replacement between binding and report publication."""
    if target.skill_sha256 and skill_digest() != target.skill_sha256:
        raise TargetError("skill_changed")


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
    path: Path | None, names: tuple[str, ...], store: str
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
    overrides = {name: (value if name == selected else None) for name in names}
    return CredentialSource(reference, overrides)


def _kubernetes_source(target: Target) -> tuple[CredentialSource, tuple[Path, ...]]:
    """Pin the effective kubeconfig path set without assuming an auth type."""
    if target.kubernetes.kubeconfig is not None:
        paths = (target.kubernetes.kubeconfig.resolve(),)
        reference = f"file:{paths[0]}"
    elif "KUBECONFIG" in os.environ:
        parts = os.environ["KUBECONFIG"].split(os.pathsep)
        if any(not part for part in parts):
            raise TargetError("kubeconfig_source_empty")
        paths = tuple(Path(part).expanduser().resolve() for part in parts)
        reference = "env:KUBECONFIG"
    else:
        paths = (Path.home() / ".kube" / "config",)
        reference = f"kubectl-default:{paths[0].resolve()}"
    if not paths:
        raise TargetError("kubeconfig_source_empty")
    for path in paths:
        assert_external_path(
            path.resolve(), Path(__file__).resolve().parents[2], "kubeconfig"
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


def resolve_revision(explicit: str | None) -> str:
    """Use the skill source HEAD or an explicit installed-skill revision."""
    if explicit:
        if not re.fullmatch(r"[0-9a-fA-F]{40}", explicit):
            raise TargetError("tested_revision must be a full commit SHA")
        return explicit.lower()
    skill_root = Path(__file__).resolve().parents[2]
    repo_root = skill_root.parents[1]
    if (
        repo_root / ".git"
    ).exists() and repo_root / "skills" / skill_root.name == skill_root:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode == 0 and re.fullmatch(
            r"[0-9a-fA-F]{40}", result.stdout.strip()
        ):
            return result.stdout.strip().lower()
    raise TargetError(
        "tested_revision unavailable; pass --revision with the exact source commit"
    )


def bind_sources(target: Target, *, revision: str | None = None) -> Target:
    """Freeze effective source selection for access checks and collectors."""
    github_names = github_token_names(target.github.host)
    github = _token_source(
        target.github.token_file,
        github_names,
        f"gh-credential-store:{target.github.host}",
    )
    gitlab = _token_source(
        target.gitlab.token_file,
        ("GITLAB_TOKEN", "GITLAB_ACCESS_TOKEN", "OAUTH_TOKEN"),
        f"glab-credential-store:{target.gitlab.host}",
    )
    kube, files = _kubernetes_source(target)
    sources = Sources(
        github,
        gitlab,
        kube,
        files,
        tuple(_file_digest(path) for path in files),
        socket.getfqdn(),
    )
    consumer = os.environ.get("CI_COMMIT_SHA") or os.environ.get("GITHUB_SHA")
    if consumer and not re.fullmatch(r"[0-9a-fA-F]{40}", consumer):
        consumer = None
    return replace(
        target,
        sources=sources,
        tested_revision=resolve_revision(revision),
        skill_sha256=skill_digest(),
        consumer_revision=consumer,
    )
