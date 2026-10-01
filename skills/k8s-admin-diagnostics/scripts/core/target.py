"""Validate explicit, nonsecret host and cluster targets for diagnostic commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import tomllib


class TargetError(ValueError):
    """A target file cannot safely identify the requested authorities."""


@dataclass(frozen=True)
class GitHubTarget:
    host: str
    repository: str
    token_file: Path | None = None


@dataclass(frozen=True)
class GitLabTarget:
    url: str
    host: str
    token_file: Path | None = None


@dataclass(frozen=True)
class KubernetesTarget:
    context: str
    server: str
    kubeconfig: Path | None


@dataclass(frozen=True)
class Target:
    github: GitHubTarget
    gitlab: GitLabTarget
    kubernetes: KubernetesTarget


def _table(value: object, name: str, keys: set[str]) -> dict[str, object]:
    """Return one table after rejecting undeclared fields, including credentials."""
    if not isinstance(value, dict):
        raise TargetError(f"{name} must be a TOML table")
    unknown = set(value) - keys
    if unknown:
        raise TargetError(f"{name} contains unsupported fields: {', '.join(sorted(unknown))}")
    return value


def _string(table: dict[str, object], key: str) -> str:
    """Read a required nonempty string without guessing a default."""
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TargetError(f"{key} must be a nonempty string")
    return value.strip()


def _https_url(value: str, key: str) -> tuple[str, str]:
    """Require an HTTPS authority URL with a fully qualified hostname."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or "." not in parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise TargetError(f"{key} must be an HTTPS origin with a full hostname")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise TargetError(f"{key} has an invalid port") from exc
    return value.rstrip("/"), parsed.netloc.lower()


def _optional_file(table: dict[str, object], key: str, skill_root: Path) -> Path | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TargetError(f"{key} must be a nonempty path when supplied")
    path = Path(value).expanduser().resolve()
    if path.is_relative_to(skill_root):
        raise TargetError(f"{key} must be stored outside the installed skill")
    return path


def load_target(path: str | Path) -> Target:
    """Parse one operator-selected TOML file; never search for hidden profiles."""
    source = Path(path).expanduser()
    skill_root = Path(__file__).resolve().parents[2]
    if source.resolve().is_relative_to(skill_root):
        raise TargetError("target file must be stored outside the installed skill")
    try:
        with source.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise TargetError(f"target file is unavailable or invalid: {source}") from exc
    if set(data) != {"github", "gitlab", "kubernetes"}:
        raise TargetError("target must contain github, gitlab, and kubernetes tables only")

    github = _table(data["github"], "github", {"host", "repository", "token_file"})
    github_host = _string(github, "host").lower()
    if "." not in github_host or "/" in github_host or ":" in github_host:
        raise TargetError("github.host must be a full hostname")
    repository = _string(github, "repository")
    if len(repository.split("/")) != 2 or any(not part for part in repository.split("/")):
        raise TargetError("github.repository must be owner/repository")

    gitlab = _table(data["gitlab"], "gitlab", {"url", "token_file"})
    gitlab_url, gitlab_host = _https_url(_string(gitlab, "url"), "gitlab.url")

    kubernetes = _table(data["kubernetes"], "kubernetes", {"context", "server", "kubeconfig"})
    server, _ = _https_url(_string(kubernetes, "server"), "kubernetes.server")
    kubeconfig = _optional_file(kubernetes, "kubeconfig", skill_root)
    return Target(
        github=GitHubTarget(host=github_host, repository=repository,
                            token_file=_optional_file(github, "token_file", skill_root)),
        gitlab=GitLabTarget(url=gitlab_url, host=gitlab_host,
                            token_file=_optional_file(gitlab, "token_file", skill_root)),
        kubernetes=KubernetesTarget(
            context=_string(kubernetes, "context"),
            server=server,
            kubeconfig=kubeconfig,
        ),
    )
