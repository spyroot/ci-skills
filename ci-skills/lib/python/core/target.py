"""Validate explicit, nonsecret host and cluster targets for diagnostic commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import tomllib

from .catalog import AUTHORITIES
from .paths import SKILL_ROOT


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
    """A GitLab-only selection for project and group operations."""

    gitlab: GitLabTarget
    source_file: Path


@dataclass(frozen=True)
class NodePodRoute:
    """One existing Pod selected by namespace, label selector, and node."""

    namespace: str
    selector: str
    container: str


@dataclass(frozen=True)
class JournalPodRoute:
    """Existing Pod whose declared mount exposes the host journal directory."""

    pod: NodePodRoute
    directory: str
    host_path: str


@dataclass(frozen=True)
class NodeDiagnosticsTarget:
    """Exact node and existing Pod routes for nonmutating node reads."""

    node: str
    cilium: NodePodRoute | None
    journal: JournalPodRoute | None


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
    node_diagnostics: NodeDiagnosticsTarget | None = None


@dataclass(frozen=True)
class Target:
    github: GitHubTarget | None
    gitlab: GitLabTarget | None
    kubernetes: KubernetesTarget | None
    active_surfaces: tuple[str, ...] = AUTHORITIES
    sources: object | None = field(default=None, repr=False, compare=False)
    tested_revision: str | None = None
    # Which declared source supplied this target, so a report can say where it
    # came from rather than leaving the reader to guess the search order.
    source_file: Path | None = None
    source_kind: str | None = None
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


def assert_external_path(path: Path, skill_root: Path, key: str) -> None:
    """Keep selected credential and target files outside the installed skill."""
    if path.resolve().is_relative_to(skill_root.resolve()):
        raise TargetError(f"{key} must be stored outside the installed skill")


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
        assert_external_path(path, skill_root, key)
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
    assert_external_path(path, skill_root, key)
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
    assert_external_path(source, skill_root, "target file")
    try:
        with source.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise TargetError(f"target file is unavailable or invalid: {source}") from exc
    return data


def load_gitlab_target(path: str | Path) -> GitLabOperationTarget:
    """Load only GitLab settings, even when other allowed tables are present."""
    source = Path(path).expanduser()
    skill_root = SKILL_ROOT
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


def _node_diagnostics(value: object | None) -> NodeDiagnosticsTarget | None:
    if value is None:
        return None
    table = _table(value, "kubernetes.node_diagnostics", {"node", "cilium", "journal"})
    node = _string(table, "node")
    if any(character.isspace() for character in node):
        raise TargetError(
            "kubernetes.node_diagnostics.node must not contain whitespace"
        )
    cilium = _node_pod_route(table["cilium"], "cilium") if "cilium" in table else None
    journal = None
    if "journal" in table:
        journal_table = _table(
            table["journal"],
            "kubernetes.node_diagnostics.journal",
            {"namespace", "selector", "container", "directory", "host_path"},
        )
        journal = JournalPodRoute(
            pod=_node_pod_route(journal_table, "journal", allow_journal_fields=True),
            directory=_absolute_path(_string(journal_table, "directory"), "directory"),
            host_path=_absolute_path(_string(journal_table, "host_path"), "host_path"),
        )
    return NodeDiagnosticsTarget(node=node, cilium=cilium, journal=journal)


def _node_pod_route(
    value: object, kind: str, *, allow_journal_fields: bool = False
) -> NodePodRoute:
    keys = {"namespace", "selector", "container"}
    if allow_journal_fields:
        keys.update({"directory", "host_path"})
    table = _table(value, f"kubernetes.node_diagnostics.{kind}", keys)
    namespace = _string(table, "namespace")
    selector = _string(table, "selector")
    container = _string(table, "container")
    if any(character.isspace() for character in namespace + container):
        raise TargetError(f"kubernetes.node_diagnostics.{kind} has invalid name")
    return NodePodRoute(namespace=namespace, selector=selector, container=container)


def _absolute_path(value: str, key: str) -> str:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or value == "/":
        raise TargetError(
            f"kubernetes.node_diagnostics.journal.{key} must be an absolute directory"
        )
    return value


def load_target(
    path: str | Path, *, required_surfaces: tuple[str, ...] = AUTHORITIES
) -> Target:
    """Validate only the authorities needed by this command's declared contract."""
    if (
        not required_surfaces
        or len(set(required_surfaces)) != len(required_surfaces)
        or set(required_surfaces) - set(AUTHORITIES)
    ):
        raise TargetError("required_target_surfaces_invalid")
    source = Path(path).expanduser()
    skill_root = SKILL_ROOT
    data = _read_target_data(source, skill_root)
    if set(data) - set(AUTHORITIES) or set(required_surfaces) - set(data):
        raise TargetError(
            "target must contain the selected authority tables only: "
            + ", ".join(required_surfaces)
        )

    github_target = None
    if "github" in required_surfaces:
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
        github_target = GitHubTarget(
            host=github_host,
            repository=repository,
            token_file=_optional_file(github, "token_file", skill_root),
            required_checks=_optional_names(github, "required_checks"),
        )

    gitlab_target = None
    if "gitlab" in required_surfaces:
        gitlab_target = _parse_gitlab(data["gitlab"], skill_root)

    kubernetes_target = None
    if "kubernetes" in required_surfaces:
        kubernetes = _table(
            data["kubernetes"],
            "kubernetes",
            {"context", "server", "kubeconfig", "kubeconfigs", "node_diagnostics"},
        )
        server, _ = _https_url(_string(kubernetes, "server"), "kubernetes.server")
        kubeconfig = _optional_file(kubernetes, "kubeconfig", skill_root)
        kubeconfigs = _optional_files(kubernetes, "kubeconfigs", skill_root)
        if kubeconfig and kubeconfigs:
            raise TargetError("declare kubernetes.kubeconfig or kubeconfigs, not both")
        kubernetes_target = KubernetesTarget(
            context=_string(kubernetes, "context"),
            server=server,
            kubeconfig=kubeconfig,
            kubeconfigs=kubeconfigs,
            node_diagnostics=_node_diagnostics(kubernetes.get("node_diagnostics")),
        )
    return Target(
        github=github_target,
        gitlab=gitlab_target,
        kubernetes=kubernetes_target,
        active_surfaces=required_surfaces,
    )
