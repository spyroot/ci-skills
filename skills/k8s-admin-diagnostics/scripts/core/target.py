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
    project: str | None = None
    group: str | None = None
    runner_id: int | None = None


@dataclass(frozen=True)
class GitLabOperationTarget:
    """A GitLab-only selection, separate from the three-surface diagnostic target."""

    gitlab: GitLabTarget
    source_file: Path


@dataclass(frozen=True)
class KubernetesTarget:
    context: str
    server: str
    kubeconfig: Path | None
    # The full search path, in kubectl's own order. A credential and the context
    # that selects it often live in different files -- a CA-verified overlay
    # plus the file holding the token, for instance -- and requiring the
    # operator to export KUBECONFIG for that is a trap: the default kubeconfig
    # is usually a DIFFERENT cluster, so a cold run silently aims elsewhere.
    kubeconfigs: tuple[Path, ...] = ()


@dataclass(frozen=True)
class Target:
    github: GitHubTarget
    gitlab: GitLabTarget
    kubernetes: KubernetesTarget
    sources: object | None = field(default=None, repr=False, compare=False)
    tested_revision: str | None = None
    # Which declared source supplied this target, so a report can say where it
    # came from rather than leaving the reader to guess the search order.
    source_file: Path | None = None
    source_kind: str | None = None
    skill: dict[str, Any] | None = None


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


def _optional_files(
    table: dict[str, object], key: str, skill_root: Path
) -> tuple[Path, ...]:
    """Read an optional ordered list of readable paths outside the skill."""
    value = table.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        raise TargetError(f"{key} must be a nonempty list when supplied")
    resolved: list[Path] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise TargetError(f"{key} entries must be nonempty paths")
        selected = Path(item).expanduser()
        if not selected.is_absolute():
            raise TargetError(f"{key} entries must be absolute paths")
        path = selected.resolve()
        if path.is_relative_to(skill_root):
            raise TargetError(f"{key} must be stored outside the installed skill")
        resolved.append(path)
    if len(set(resolved)) != len(resolved):
        raise TargetError(f"{key} entries must be unique")
    return tuple(resolved)


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


def _optional_reference(table: dict[str, object], key: str) -> str | None:
    """Accept an exact numeric ID or relative GitLab project/group path."""
    value = table.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TargetError(f"gitlab.{key} must be a path or positive numeric ID")
    if isinstance(value, int):
        if value <= 0:
            raise TargetError(f"gitlab.{key} must be a positive numeric ID")
        return str(value)
    selected = value.strip()
    if (
        not selected
        or selected.startswith("/")
        or selected.endswith("/")
        or "://" in selected
        or any(character.isspace() for character in selected)
    ):
        raise TargetError(f"gitlab.{key} must be a relative path or numeric ID")
    return selected


def _parse_gitlab(value: object, skill_root: Path) -> GitLabTarget:
    gitlab = _table(
        value,
        "gitlab",
        {"url", "token_file", "project", "group", "runner_id"},
    )
    url, host = _https_url(_string(gitlab, "url"), "gitlab.url")
    runner_id = gitlab.get("runner_id")
    if runner_id is not None and (
        isinstance(runner_id, bool) or not isinstance(runner_id, int) or runner_id <= 0
    ):
        raise TargetError("gitlab.runner_id must be a positive numeric ID")
    return GitLabTarget(
        url=url,
        host=host,
        token_file=_optional_file(gitlab, "token_file", skill_root),
        project=_optional_reference(gitlab, "project"),
        group=_optional_reference(gitlab, "group"),
        runner_id=runner_id,
    )


def _read_target_data(source: Path, skill_root: Path) -> dict[str, object]:
    """Read one selected target file without searching another location."""
    if source.resolve().is_relative_to(skill_root):
        raise TargetError("target file must be stored outside the installed skill")
    try:
        with source.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise TargetError(f"target file is unavailable or invalid: {source}") from exc
    return data


def load_gitlab_target(path: str | Path) -> GitLabOperationTarget:
    """Load only GitLab settings, even when other allowed tables are present."""
    source = Path(path).expanduser()
    skill_root = Path(__file__).resolve().parents[2]
    data = _read_target_data(source, skill_root)
    if "gitlab" not in data or set(data) - {"github", "gitlab", "kubernetes"}:
        raise TargetError("GitLab operations need a gitlab table without extra tables")
    selected = _parse_gitlab(data["gitlab"], skill_root)
    return GitLabOperationTarget(selected, source.resolve())


def select_gitlab_reference(
    target: GitLabOperationTarget,
    *,
    project: str | None = None,
    group: str | None = None,
) -> tuple[str, str]:
    """Select exactly one target kind, with an explicit same-kind override."""
    if project is not None and group is not None:
        raise TargetError("project_and_group_conflict")
    selected_project = _optional_reference({"project": project}, "project")
    selected_group = _optional_reference({"group": group}, "group")
    if selected_project is not None:
        return "project", selected_project
    if selected_group is not None:
        return "group", selected_group
    selected_project = target.gitlab.project
    selected_group = target.gitlab.group
    if bool(selected_project) == bool(selected_group):
        raise TargetError("select_exactly_one_gitlab_project_or_group")
    return (
        ("project", selected_project)
        if selected_project is not None
        else ("group", selected_group or "")
    )


def load_target(path: str | Path) -> Target:
    """Parse one operator-selected TOML file; never search for hidden profiles."""
    source = Path(path).expanduser()
    skill_root = Path(__file__).resolve().parents[2]
    data = _read_target_data(source, skill_root)
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

    gitlab = _parse_gitlab(data["gitlab"], skill_root)

    kubernetes = _table(
        data["kubernetes"],
        "kubernetes",
        {"context", "server", "kubeconfig", "kubeconfigs"},
    )
    server, _ = _https_url(_string(kubernetes, "server"), "kubernetes.server")
    kubeconfig = _optional_file(kubernetes, "kubeconfig", skill_root)
    kubeconfigs = _optional_files(kubernetes, "kubeconfigs", skill_root)
    if kubeconfig and kubeconfigs:
        raise TargetError("declare kubernetes.kubeconfig or kubeconfigs, not both")
    return Target(
        github=GitHubTarget(
            host=github_host,
            repository=repository,
            token_file=_optional_file(github, "token_file", skill_root),
            required_checks=_optional_names(github, "required_checks"),
        ),
        gitlab=gitlab,
        kubernetes=KubernetesTarget(
            context=_string(kubernetes, "context"),
            server=server,
            kubeconfig=kubeconfig,
            kubeconfigs=kubeconfigs,
        ),
    )
