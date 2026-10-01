"""Validate explicit, nonsecret host and cluster targets for diagnostic commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import tomllib


class TargetError(ValueError):
    """A target file cannot safely identify the requested authorities."""


@dataclass(frozen=True)
class GitHubTarget:
    host: str
    repository: str
    token_file: Path | None = None
    # Which status checks protection must require. Declared by the operator,
    # because the expected check name is project-specific and this skill is
    # deliberately project-neutral.
    required_checks: tuple[str, ...] = ()


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
    sources: object | None = field(default=None, repr=False, compare=False)
    tested_revision: str | None = None
    skill: dict[str, Any] | None = None
    kubernetes_source_reference: str | None = None
    target_reference: str | None = None


def _table(value: object, name: str, keys: set[str]) -> dict[str, object]:
    """Return one table after rejecting undeclared fields, including credentials."""
    if not isinstance(value, dict):
        raise TargetError(f"{name} must be a TOML table")
    unknown = set(value) - keys
    if unknown:
        raise TargetError(
            f"{name} contains unsupported fields: {', '.join(sorted(unknown))}"
        )
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


def kubernetes_label(target: Target) -> str:
    """Name a Kubernetes target by context AND server.

    A context name alone does not say which cluster was read, so a report
    labelled with it cannot be checked against the verified target.
    """
    return f"{target.kubernetes.context} -> {target.kubernetes.server}"


def _optional_names(table: dict[str, object], key: str) -> tuple[str, ...]:
    """Read an optional list of exact, nonempty names."""
    value = table.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        raise TargetError(f"{key} must be a nonempty list when supplied")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise TargetError(f"{key} entries must be nonempty strings")
        names.append(item.strip())
    if len(set(names)) != len(names):
        raise TargetError(f"{key} entries must be unique")
    return tuple(names)


def _optional_file(table: dict[str, object], key: str, skill_root: Path) -> Path | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TargetError(f"{key} must be a nonempty path when supplied")
    selected = Path(value).expanduser()
    if key == "token_file" and not selected.is_absolute():
        raise TargetError("token_file must be an absolute path")
    path = selected.resolve()
    if path.is_relative_to(skill_root):
        raise TargetError(f"{key} must be stored outside the installed skill")
    return path


def _selected_kubeconfig(
    value: object, *, context: str, server: str, skill_root: Path
) -> tuple[Path, str]:
    """Select the sole declared file whose context resolves to the exact API."""
    if not isinstance(value, list) or not value:
        raise TargetError("kubeconfigs must be a nonempty list")
    try:
        import yaml
    except ImportError as exc:
        raise TargetError("yaml_dependency_missing") from exc
    matches: list[tuple[int, Path]] = []
    seen: set[Path] = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, str) or not Path(item).expanduser().is_absolute():
            raise TargetError("kubeconfigs entries must be absolute paths")
        path = Path(item).expanduser().resolve()
        if path in seen or path.is_relative_to(skill_root):
            raise TargetError("kubeconfigs contains duplicate or skill-local path")
        seen.add(path)
        try:
            if not path.is_file() or not path.stat().st_size:
                raise TargetError("kubeconfig_candidate_unavailable")
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise TargetError("kubeconfig_candidate_invalid") from exc
        if not isinstance(payload, dict):
            raise TargetError("kubeconfig_candidate_invalid")
        contexts = payload.get("contexts")
        clusters = payload.get("clusters")
        if not isinstance(contexts, list) or not isinstance(clusters, list):
            raise TargetError("kubeconfig_candidate_invalid")
        selected = [
            entry
            for entry in contexts
            if isinstance(entry, dict) and entry.get("name") == context
        ]
        if len(selected) > 1:
            raise TargetError("kubeconfig_context_ambiguous")
        if not selected:
            continue
        cluster_name = selected[0].get("context")
        if not isinstance(cluster_name, dict):
            raise TargetError("kubeconfig_candidate_invalid")
        cluster_name = cluster_name.get("cluster")
        defined = [
            entry
            for entry in clusters
            if isinstance(entry, dict) and entry.get("name") == cluster_name
        ]
        if len(defined) != 1 or not isinstance(defined[0].get("cluster"), dict):
            raise TargetError("kubeconfig_cluster_ambiguous")
        candidate_server = defined[0]["cluster"].get("server")
        if not isinstance(candidate_server, str):
            raise TargetError("kubeconfig_candidate_invalid")
        if candidate_server.rstrip("/") == server:
            matches.append((index, path))
    if len(matches) != 1:
        raise TargetError("kubeconfig_target_not_unique")
    index, path = matches[0]
    return path, f"kubeconfig-list:{index} -> file:{path}"


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
        raise TargetError(
            "target must contain github, gitlab, and kubernetes tables only"
        )

    github = _table(
        data["github"],
        "github",
        {"host", "repository", "token_file", "required_checks"},
    )
    github_host = _string(github, "host").lower()
    if "." not in github_host or "/" in github_host or ":" in github_host:
        raise TargetError("github.host must be a full hostname")
    repository = _string(github, "repository")
    if len(repository.split("/")) != 2 or any(
        not part for part in repository.split("/")
    ):
        raise TargetError("github.repository must be owner/repository")

    gitlab = _table(data["gitlab"], "gitlab", {"url", "token_file"})
    gitlab_url, gitlab_host = _https_url(_string(gitlab, "url"), "gitlab.url")

    kubernetes = _table(
        data["kubernetes"],
        "kubernetes",
        {"context", "server", "kubeconfig", "kubeconfigs"},
    )
    server, _ = _https_url(_string(kubernetes, "server"), "kubernetes.server")
    context = _string(kubernetes, "context")
    if "kubeconfig" in kubernetes and "kubeconfigs" in kubernetes:
        raise TargetError("kubeconfig_source_conflict")
    if "kubeconfigs" in kubernetes:
        kubeconfig, source_reference = _selected_kubeconfig(
            kubernetes["kubeconfigs"],
            context=context,
            server=server,
            skill_root=skill_root,
        )
    else:
        kubeconfig = _optional_file(kubernetes, "kubeconfig", skill_root)
        source_reference = None
    return Target(
        github=GitHubTarget(
            host=github_host,
            repository=repository,
            token_file=_optional_file(github, "token_file", skill_root),
            required_checks=_optional_names(github, "required_checks"),
        ),
        gitlab=GitLabTarget(
            url=gitlab_url,
            host=gitlab_host,
            token_file=_optional_file(gitlab, "token_file", skill_root),
        ),
        kubernetes=KubernetesTarget(
            context=context,
            server=server,
            kubeconfig=kubeconfig,
        ),
        kubernetes_source_reference=source_reference,
    )
