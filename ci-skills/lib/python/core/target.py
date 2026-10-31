"""Validate explicit, nonsecret host and cluster targets for diagnostic commands.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping, MutableSequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union
from urllib.parse import urlsplit

from lib.python.core.catalog import AUTHORITIES, DEFAULT_AUTHORITIES
from lib.python.core.endpoints import TARGET_CONTRACT
from lib.python.core.paths import SKILL_ROOT

if TYPE_CHECKING:
    from lib.python.access.sources import Sources
    from lib.python.core.provenance import SkillIdentity


class TargetError(ValueError):
    """A target file cannot safely identify the requested authorities."""


@dataclass(frozen=True)
class GitHubTarget:
    """Select a GitHub repository and its configured authentication source.

    :param host: GitHub HTTPS authority verified for API requests.
    :param repository: Owner/repository path selected by the target file.
    :param token_file: Existing token file, or ``None`` to use runtime credential resolution.
    :param required_checks: Operator-declared protection checks to verify.

    .. attribute :: host
        :type: str

        GitHub HTTPS authority verified for API requests.

    .. attribute :: repository
        :type: str

        Owner/repository path selected by the target file.

    .. attribute :: token_file
        :type: Optional[Path]

        Existing token file, or ``None`` to use runtime credential resolution.

    .. attribute :: required_checks
        :type: tuple[str, ...]

        Operator-declared protection checks to verify.
    """

    host: str
    repository: str
    token_file: Optional[Path] = None
    # Which status checks protection must require. Declared by the operator,
    # because the expected check name is project-specific and this skill is
    # deliberately project-neutral.
    required_checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class HarborTarget:
    """Select a registry project and existing credential file references.

    :param url: Harbor HTTPS origin used for API requests.
    :param host: Network authority extracted from the origin.
    :param project: Registry project selected by the target file.
    :param credential_file: Combined credential file, or ``None`` when unconfigured.
    :param username_file: Separate username file, or ``None`` when unconfigured.
    :param password_file: Separate password file, or ``None`` when unconfigured.

    .. attribute :: url
        :type: str

        Harbor HTTPS origin used for API requests.

    .. attribute :: host
        :type: str

        Network authority extracted from the origin.

    .. attribute :: project
        :type: str

        Registry project selected by the target file.

    .. attribute :: credential_file
        :type: Optional[Path]

        Combined credential file, or ``None`` when unconfigured.

    .. attribute :: username_file
        :type: Optional[Path]

        Separate username file, or ``None`` when unconfigured.

    .. attribute :: password_file
        :type: Optional[Path]

        Separate password file, or ``None`` when unconfigured.
    """

    url: str
    host: str
    project: str
    credential_file: Optional[Path] = None
    username_file: Optional[Path] = None
    password_file: Optional[Path] = None


@dataclass(frozen=True)
class GitLabTarget:
    """Select a GitLab origin and configured operation scopes.

    :param url: GitLab HTTPS origin used for API requests.
    :param host: Network authority extracted from the origin.
    :param token_file: Existing token file, or ``None`` to use runtime credential resolution.
    :param project: Project path, or ``None`` when no project is selected.
    :param group: Group path, or ``None`` when no group is selected.
    :param runner_id: Positive runner ID, or ``None`` when no runner is selected.
    :param profile: Explicit named GitLab profile, or ``None`` for the flat table.

    .. attribute :: url
        :type: str

        GitLab HTTPS origin used for API requests.

    .. attribute :: host
        :type: str

        Network authority extracted from the origin.

    .. attribute :: token_file
        :type: Optional[Path]

        Existing token file, or ``None`` to use runtime credential resolution.

    .. attribute :: project
        :type: Optional[str]

        Project path, or ``None`` when no project is selected.

    .. attribute :: group
        :type: Optional[str]

        Group path, or ``None`` when no group is selected.

    .. attribute :: runner_id
        :type: Optional[int]

        Positive runner ID, or ``None`` when no runner is selected.

    .. attribute :: profile
        :type: Optional[str]

        Exact named GitLab selection; absent for the flat table.
    """

    url: str
    host: str
    token_file: Optional[Path] = None
    project: Optional[str] = None
    group: Optional[str] = None
    runner_id: Optional[int] = None
    profile: Optional[str] = None

    def selection_source(self, source: str) -> str:
        """Bind a named profile to existing report and confirmation provenance.

        :param source: File or binding selection provenance, kept unchanged by default.
        :returns: The source with an explicit profile selector when one was selected.
        """
        return (
            f"{source};argv:--gitlab-profile={json.dumps(self.profile)}"
            if self.profile is not None
            else source
        )


@dataclass(frozen=True)
class GitLabOperationTarget:
    """GitLab-only target selection for project and group operations.

    :param gitlab: Selected GitLab origin, scope, and token reference.
    :param source_file: Target TOML file from which this selection was loaded.

    .. attribute :: gitlab
        :type: GitLabTarget

        Selected GitLab origin, scope, and token reference.

    .. attribute :: source_file
        :type: Path

        Target TOML file from which this selection was loaded.
    """

    gitlab: GitLabTarget
    source_file: Path


@dataclass(frozen=True)
class NodePodRoute:
    """Existing Pod route selected for nonmutating node-local reads.

    :param namespace: Namespace in which the existing Pod is selected.
    :param selector: Label selector used to find the Pod.
    :param container: Container name used for bounded Pod exec.

    .. attribute :: namespace
        :type: str

        Namespace in which the existing Pod is selected.

    .. attribute :: selector
        :type: str

        Label selector used to find the Pod.

    .. attribute :: container
        :type: str

        Container name used for bounded Pod exec.
    """

    namespace: str
    selector: str
    container: str


@dataclass(frozen=True)
class JournalPodRoute:
    """Existing Pod route that exposes the selected host journal path.

    :param pod: Namespace, selector, and container of the existing Pod.
    :param directory: Journal directory visible inside that container.
    :param host_path: Expected host path verified against the Pod volume.

    .. attribute :: pod
        :type: NodePodRoute

        Namespace, selector, and container of the existing Pod.

    .. attribute :: directory
        :type: str

        Journal directory visible inside that container.

    .. attribute :: host_path
        :type: str

        Expected host path verified against the Pod volume.
    """

    pod: NodePodRoute
    directory: str
    host_path: str


@dataclass(frozen=True)
class NodeDiagnosticsTarget:
    """Selected node with optional existing Pod routes for diagnostics.

    :param node: Kubernetes node name checked before Pod exec.
    :param cilium: Optional existing Cilium Pod route on the node.
    :param journal: Optional existing journal Pod and mount route.

    .. attribute :: node
        :type: str

        Kubernetes node name checked before Pod exec.

    .. attribute :: cilium
        :type: Optional[NodePodRoute]

        Optional existing Cilium Pod route on the node.

    .. attribute :: journal
        :type: Optional[JournalPodRoute]

        Optional existing journal Pod and mount route.
    """

    node: str
    cilium: Optional[NodePodRoute]
    journal: Optional[JournalPodRoute]


@dataclass(frozen=True)
class KubernetesTarget:
    """Select a Kubernetes context, expected server, and credential search path.

    :param context: Explicit kubeconfig context used for each native command.
    :param server: Expected API origin checked against the selected context.
    :param kubeconfig: Single selected file, or ``None`` for the resolved search path.
    :param kubeconfigs: Kubeconfig files in native precedence order.
    :param node_diagnostics: Existing node/Pod routes, or ``None`` when not configured.

    .. attribute :: context
        :type: str

        Explicit kubeconfig context used for each native command.

    .. attribute :: server
        :type: str

        Expected API origin checked against the selected context.

    .. attribute :: kubeconfig
        :type: Optional[Path]

        Single selected file, or ``None`` for the resolved search path.

    .. attribute :: kubeconfigs
        :type: tuple[Path, ...]

        Kubeconfig files in native precedence order.

    .. attribute :: node_diagnostics
        :type: Optional[NodeDiagnosticsTarget]

        Existing node/Pod routes, or ``None`` when not configured.
    """

    context: str
    server: str
    kubeconfig: Optional[Path]
    # The full search path, in kubectl's own order. A credential and the context
    # that selects it often live in different files -- a CA-verified overlay
    # plus the file holding the token, for instance -- and requiring the
    # operator to export KUBECONFIG for that is a trap: the default kubeconfig
    # is usually a DIFFERENT cluster, so a cold run silently aims elsewhere.
    kubeconfigs: tuple[Path, ...] = ()
    node_diagnostics: Optional[NodeDiagnosticsTarget] = None


@dataclass(frozen=True)
class Target:
    """Retain selected authorities and their runtime source and skill provenance.

    :param github: Selected GitHub authority, or ``None`` when not required.
    :param gitlab: Selected GitLab authority, or ``None`` when not required.
    :param kubernetes: Selected Kubernetes authority, or ``None`` when not required.
    :param active_surfaces: Authorities requested for this invocation.
    :param sources: Bound runtime credential sources, or ``None`` before binding.
    :param tested_revision: Verified source commit, or ``None`` before identity binding.
    :param source_file: Target TOML path, or ``None`` before source resolution.
    :param source_kind: Target-source classification, or ``None`` before resolution.
    :param skill: Installed identity, or ``None`` before identity binding.
    :param kubernetes_source_reference: Kubeconfig provenance, or ``None`` when unbound.
    :param target_reference: Target provenance, or ``None`` before source resolution.
    :param harbor: Selected registry authority, or ``None`` when not required.

    .. attribute :: github
        :type: Optional[GitHubTarget]

        Selected GitHub authority, or ``None`` when not required.

    .. attribute :: gitlab
        :type: Optional[GitLabTarget]

        Selected GitLab authority, or ``None`` when not required.

    .. attribute :: kubernetes
        :type: Optional[KubernetesTarget]

        Selected Kubernetes authority, or ``None`` when not required.

    .. attribute :: active_surfaces
        :type: tuple[str, ...]

        Authorities requested for this invocation.

    .. attribute :: sources
        :type: Optional[Sources]

        Bound runtime credential sources, or ``None`` before binding.

    .. attribute :: tested_revision
        :type: Optional[str]

        Verified source commit, or ``None`` before identity binding.

    .. attribute :: source_file
        :type: Optional[Path]

        Target TOML path, or ``None`` before source resolution.

    .. attribute :: source_kind
        :type: Optional[str]

        Target-source classification, or ``None`` before resolution.

    .. attribute :: skill
        :type: Optional[SkillIdentity]

        Installed identity, or ``None`` before identity binding.

    .. attribute :: kubernetes_source_reference
        :type: Optional[str]

        Kubeconfig provenance, or ``None`` when unbound.

    .. attribute :: target_reference
        :type: Optional[str]

        Target provenance, or ``None`` before source resolution.

    .. attribute :: harbor
        :type: Optional[HarborTarget]

        Selected registry authority, or ``None`` when not required.
    """

    github: Optional[GitHubTarget]
    gitlab: Optional[GitLabTarget]
    kubernetes: Optional[KubernetesTarget]
    active_surfaces: tuple[str, ...] = DEFAULT_AUTHORITIES
    sources: Optional[Sources] = field(default=None, repr=False, compare=False)
    tested_revision: Optional[str] = None
    # Which declared source supplied this target, so a report can say where it
    # came from rather than leaving the reader to guess the search order.
    source_file: Optional[Path] = None
    source_kind: Optional[str] = None
    skill: Optional[SkillIdentity] = None
    kubernetes_source_reference: Optional[str] = None
    target_reference: Optional[str] = None
    harbor: Optional[HarborTarget] = None

    def selection_source(self, source: str) -> str:
        """Include the selected GitLab profile in provider-selection provenance.

        :param source: Existing file or binding provenance.
        :returns: Profile-bound provenance when GitLab is selected, otherwise the input.
        """
        return self.gitlab.selection_source(source) if self.gitlab else source

    def require_github(self) -> GitHubTarget:
        """Select the concrete github settings required by a caller.

        :returns: This target's configured github settings.
        :raises TargetError: The optional github section is absent.
        """
        if self.github is None:
            raise TargetError("github_target_required")
        return self.github

    def require_gitlab(self) -> GitLabTarget:
        """Select the concrete gitlab settings required by a caller.

        :returns: This target's configured gitlab settings.
        :raises TargetError: The optional gitlab section is absent.
        """
        if self.gitlab is None:
            raise TargetError("gitlab_target_required")
        return self.gitlab

    def require_kubernetes(self) -> KubernetesTarget:
        """Select the concrete kubernetes settings required by a caller.

        :returns: This target's configured kubernetes settings.
        :raises TargetError: The optional kubernetes section is absent.
        """
        if self.kubernetes is None:
            raise TargetError("kubernetes_target_required")
        return self.kubernetes

    def require_harbor(self) -> HarborTarget:
        """Select the concrete harbor settings required by a caller.

        :returns: This target's configured harbor settings.
        :raises TargetError: The optional harbor section is absent.
        """
        if self.harbor is None:
            raise TargetError("harbor_target_required")
        return self.harbor


def _table(value: object, name: str, keys: frozenset[str]) -> Mapping[str, object]:
    """Validate one authority table against its declared keys.

    :param value: Parsed TOML value expected to be a table.
    :param name: Table name used in validation diagnostics.
    :param keys: Names permitted directly in this table.
    :returns: Original table after shape and field validation.
    :raises TargetError: The value is not a table or contains undeclared fields.
    """
    if not isinstance(value, dict):
        raise TargetError(f"{name} must be a TOML table")
    unknown = set(value) - keys
    if unknown:
        raise TargetError(
            f"{name} contains unsupported fields: {', '.join(sorted(unknown))}"
        )
    return value


def _string(table: Mapping[str, object], key: str, name: str) -> str:
    """Read a required nonempty string from a target table.

    :param table: Parsed authority table.
    :param key: Required field name.
    :param name: Table name qualifying the field in validation diagnostics.
    :returns: Field value with surrounding whitespace removed.
    :raises TargetError: The field is absent, is not text, or is blank.
    """
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TargetError(f"{name}.{key} must be a nonempty string")
    return value.strip()


def _https_url(value: str, key: str) -> tuple[str, str]:
    """Validate an HTTPS origin and extract its authority.

    :param value: Candidate URL without a resource path, query, or fragment.
    :param key: Target field name used in error messages.
    :returns: URL without a trailing slash and lowercased network authority.
    :raises TargetError: The URL has a malformed authority, lacks HTTPS, a full
        hostname, or a valid port, or includes credentials, a resource path,
        query, or fragment.
    """
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise TargetError(f"{key} has an invalid authority") from exc
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
    """Label a Kubernetes target with its context and API server.

    :param target: Validated target containing Kubernetes settings.
    :returns: Context and server identifying the selected cluster.
    :raises TargetError: The optional Kubernetes section is absent.
    """  # noqa: DOC502 - target selection validates the required section.
    kubernetes = target.require_kubernetes()
    return f"{kubernetes.context} -> {kubernetes.server}"


def assert_external_path(path: Path, skill_root: Path, key: str) -> None:
    """Reject a target or credential path inside the installed skill.

    :param path: Selected file path, resolved before checking its location.
    :param skill_root: Installed skill directory excluded from file selections.
    :param key: Setting name included in a rejected selection's diagnostic.
    :raises TargetError: If the canonical path is inside the installed skill.
    """
    if path.resolve().is_relative_to(skill_root.resolve()):
        raise TargetError(f"{key} must be stored outside the installed skill")


def _optional_files(
    table: Mapping[str, object], key: str, skill_root: Path
) -> tuple[Path, ...]:
    """Resolve an optional ordered list of external absolute file paths.

    :param table: Parsed target table containing the optional list.
    :param key: List field name.
    :param skill_root: Installed skill directory excluded from selections.
    :returns: Canonical unique paths in declared order, or an empty tuple when
        the field is absent.
    :raises TargetError: The list is empty or malformed, contains a relative or
        duplicate path, or selects a path inside the skill.
    """
    value = table.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        raise TargetError(f"{key} must be a nonempty list when supplied")
    resolved: MutableSequence[Path] = []
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


def _optional_names(table: Mapping[str, object], key: str) -> tuple[str, ...]:
    """Read an optional list of distinct exact names.

    :param table: Parsed target table containing the optional list.
    :param key: List field name.
    :returns: Nonempty names with surrounding whitespace removed, or an empty
        tuple when the field is absent.
    :raises TargetError: The list is empty, malformed, or contains duplicates.
    """
    value = table.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        raise TargetError(f"{key} must be a nonempty list when supplied")
    names: MutableSequence[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise TargetError(f"{key} entries must be nonempty strings")
        names.append(item.strip())
    if len(set(names)) != len(names):
        raise TargetError(f"{key} entries must be unique")
    return tuple(names)


def _optional_file(
    table: Mapping[str, object],
    key: str,
    skill_root: Path,
    *,
    target_directory: Optional[Path] = None,
) -> Optional[Path]:
    """Resolve an optional file, using its target directory when supplied.

    :param table: Parsed authority settings containing the optional path.
    :param key: File setting to resolve.
    :param skill_root: Installed skill directory excluded from file selections.
    :param target_directory: Directory containing the selected target file.
    :returns: Canonical file path, or None when the setting is absent.
    :raises TargetError: If the path is empty, invalid, or inside the skill.
    """
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TargetError(f"{key} must be a nonempty path when supplied")
    selected = Path(value).expanduser()
    if target_directory is not None and not selected.is_absolute():
        selected = target_directory / selected
    path = selected.resolve()
    assert_external_path(path, skill_root, key)
    return path


def _optional_reference(table: Mapping[str, object], key: str) -> Optional[str]:
    """Normalize a GitLab project or group selector.

    :param table: Table containing the optional selector.
    :param key: ``project`` or ``group`` field name for diagnostics.
    :returns: Positive numeric ID as text or a relative path, or ``None`` when
        absent.
    :raises TargetError: The selector is invalid, absolute, empty, or contains
        whitespace or a URL scheme.
    """
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


def _parse_gitlab(
    value: object,
    skill_root: Path,
    *,
    target_directory: Path,
    profile: Optional[str] = None,
) -> GitLabTarget:
    """Parse GitLab settings and resolve their token beside the target file.

    :param value: Parsed GitLab authority table, including optional named profiles.
    :param skill_root: Installed skill directory excluded from file selections.
    :param target_directory: Directory containing the selected target file.
    :param profile: Exact named profile, or ``None`` for the flat GitLab table.
    :returns: Validated GitLab settings with a canonical token path if supplied.
    :raises TargetError: If a GitLab setting or token path is invalid.
    """
    fields = TARGET_CONTRACT.names("gitlab")
    if not isinstance(value, dict):
        raise TargetError("gitlab must be a table")
    profiles = set(value) - fields
    invalid = {
        name
        for name in profiles
        if not name.strip() or not isinstance(value[name], dict)
    }
    if invalid:
        raise TargetError(
            "gitlab contains unsupported fields: " + ", ".join(sorted(invalid))
        )
    if profile is None:
        gitlab = {key: item for key, item in value.items() if key in fields}
    else:
        if not isinstance(profile, str) or not profile.strip():
            raise TargetError("gitlab_profile_name_invalid")
        if profile not in profiles:
            raise TargetError("gitlab_profile_unknown")
        gitlab = _table(
            value[profile], TARGET_CONTRACT.dotted_path(("gitlab", profile)), fields
        )
    url, host = _https_url(_string(gitlab, "url", "gitlab"), "gitlab.url")
    runner_id = gitlab.get("runner_id")
    if runner_id is not None and (
        isinstance(runner_id, bool) or not isinstance(runner_id, int) or runner_id <= 0
    ):
        raise TargetError("gitlab.runner_id must be a positive numeric ID")
    return GitLabTarget(
        url=url,
        host=host,
        token_file=_optional_file(
            gitlab, "token_file", skill_root, target_directory=target_directory
        ),
        project=_optional_reference(gitlab, "project"),
        group=_optional_reference(gitlab, "group"),
        runner_id=runner_id,
        profile=profile,
    )


def _read_target_data(source: Path, skill_root: Path) -> Mapping[str, object]:
    """Read exactly one selected TOML target outside the installed skill.

    :param source: Selected target file path; no fallback search is performed.
    :param skill_root: Installed skill directory excluded from selections.
    :returns: Parsed target TOML mapping.
    :raises TargetError: The file is inside the skill, unreadable, or invalid TOML.
    """
    assert_external_path(source, skill_root, "target file")
    try:
        with source.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise TargetError(f"target file is unavailable or invalid: {source}") from exc
    return data


def load_gitlab_target(
    path: Union[str, Path], *, gitlab_profile: Optional[str] = None
) -> GitLabOperationTarget:
    """Load GitLab settings with relative tokens beside the selected target.

    Other declared authority tables are permitted but are not validated here.

    :param path: Selected TOML target file, including an optional home prefix.
    :param gitlab_profile: Exact named profile; omission uses the flat GitLab table.
    :returns: GitLab settings and the canonical source-file path.
    :raises TargetError: If the target is unreadable, invalid, inside the skill,
        or contains invalid GitLab settings or an internal credential path.
    """  # noqa: DOC502 - Shared table and field parsers raise TargetError.
    source = Path(path).expanduser()
    skill_root = SKILL_ROOT
    data = _read_target_data(source, skill_root)
    _select_surfaces(data, ("gitlab",), ())
    selected = _parse_gitlab(
        data["gitlab"],
        skill_root,
        target_directory=source.parent,
        profile=gitlab_profile,
    )
    return GitLabOperationTarget(selected, source.resolve())


def select_gitlab_reference(
    target: GitLabOperationTarget,
    *,
    project: Optional[str] = None,
    group: Optional[str] = None,
) -> tuple[str, str]:
    """Select one GitLab project or group, applying an explicit override.

    :param target: Loaded GitLab settings with optional default selectors.
    :param project: Explicit project path or positive numeric ID, if supplied.
    :param group: Explicit group path or positive numeric ID, if supplied.
    :returns: Target kind and its selected project or group reference.
    :raises TargetError: If selectors conflict, are invalid, or do not identify
        exactly one target kind.
    """
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


def _node_diagnostics(value: object) -> Optional[NodeDiagnosticsTarget]:
    """Parse optional node and existing Pod routes for diagnostic reads.

    :param value: Parsed ``kubernetes.node_diagnostics`` table, or ``None``.
    :returns: Validated node and optional Cilium/journal Pod routes, or ``None``.
    :raises TargetError: A route, node name, or journal directory is invalid.
    """
    if value is None:
        return None
    name = "kubernetes.node_diagnostics"
    table = _table(value, name, TARGET_CONTRACT.names(name))
    node = _string(table, "node", name)
    if any(character.isspace() for character in node):
        raise TargetError(
            "kubernetes.node_diagnostics.node must not contain whitespace"
        )
    cilium = _node_pod_route(table["cilium"], "cilium") if "cilium" in table else None
    journal = None
    if "journal" in table:
        journal_table = _table(
            table["journal"],
            f"{name}.journal",
            TARGET_CONTRACT.names(f"{name}.journal"),
        )
        journal = JournalPodRoute(
            pod=_node_pod_route(journal_table, "journal"),
            directory=_absolute_path(
                _string(journal_table, "directory", f"{name}.journal"), "directory"
            ),
            host_path=_absolute_path(
                _string(journal_table, "host_path", f"{name}.journal"), "host_path"
            ),
        )
    return NodeDiagnosticsTarget(node=node, cilium=cilium, journal=journal)


def _node_pod_route(value: object, kind: str) -> NodePodRoute:
    """Validate one existing Pod route declared for node diagnostics.

    :param value: Parsed Cilium or journal route table.
    :param kind: Route kind used to select its declared fields.
    :returns: Namespace, label selector, and container route.
    :raises TargetError: Required route fields are absent, unsupported, or invalid.
    """
    name = f"kubernetes.node_diagnostics.{kind}"
    table = _table(value, name, TARGET_CONTRACT.names(name))
    namespace = _string(table, "namespace", name)
    selector = _string(table, "selector", name)
    container = _string(table, "container", name)
    if any(character.isspace() for character in namespace + container):
        raise TargetError(f"kubernetes.node_diagnostics.{kind} has invalid name")
    return NodePodRoute(namespace=namespace, selector=selector, container=container)


def _absolute_path(value: str, key: str) -> str:
    """Validate an absolute journal directory without traversal segments.

    :param value: Candidate directory path.
    :param key: Journal field name used in an invalid-path diagnostic.
    :returns: Original absolute path string.
    :raises TargetError: The path is relative, root-only, or contains ``..``.
    """
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or value == "/":
        raise TargetError(
            f"kubernetes.node_diagnostics.journal.{key} must be an absolute directory"
        )
    return value


def _parse_github(
    value: object, skill_root: Path, *, target_directory: Path
) -> GitHubTarget:
    """Validate only the github table selected by the command.

    :param value: Parsed ``github`` TOML table.
    :param skill_root: Installed skill directory excluded from runtime inputs.
    :param target_directory: Selected target directory anchoring relative credential paths.
    :returns: Concrete github configuration with its declared optional fields.
    :raises TargetError: A required field, selector, path, or table entry is invalid.
    """  # noqa: DOC503 - Shared field parsers validate their declared contracts.
    github = _table(value, "github", TARGET_CONTRACT.names("github"))
    github_host = _string(github, "host", "github").lower()
    if "." not in github_host or "/" in github_host or ":" in github_host:
        raise TargetError("github.host must be a full hostname")
    repository = _string(github, "repository", "github")
    if len(repository.split("/")) != 2 or any(
        not part for part in repository.split("/")
    ):
        raise TargetError("github.repository must be owner/repository")
    return GitHubTarget(
        host=github_host,
        repository=repository,
        token_file=_optional_file(
            github, "token_file", skill_root, target_directory=target_directory
        ),
        required_checks=_optional_names(github, "required_checks"),
    )


def _parse_harbor(
    value: object, skill_root: Path, *, target_directory: Path
) -> HarborTarget:
    """Validate only the harbor table selected by the command.

    :param value: Parsed ``harbor`` TOML table.
    :param skill_root: Installed skill directory excluded from runtime inputs.
    :param target_directory: Selected target directory anchoring relative credential paths.
    :returns: Concrete harbor configuration with its declared optional fields.
    :raises TargetError: A required field, selector, path, or table entry is invalid.
    """  # noqa: DOC503 - Shared field parsers validate their declared contracts.
    harbor = _table(value, "harbor", TARGET_CONTRACT.names("harbor"))
    url, host = _https_url(_string(harbor, "url", "harbor"), "harbor.url")
    files = {
        key: _optional_file(harbor, key, skill_root, target_directory=target_directory)
        for key in ("credential_file", "username_file", "password_file")
    }
    combined = files["credential_file"] is not None
    paired = files["username_file"] is not None and files["password_file"] is not None
    if combined == paired or (
        combined and any(files[key] for key in ("username_file", "password_file"))
    ):
        raise TargetError("harbor_credential_selection_invalid")
    return HarborTarget(url, host, _string(harbor, "project", "harbor"), **files)


def _parse_kubernetes(value: object, skill_root: Path) -> KubernetesTarget:
    """Validate only the kubernetes table selected by the command.

    :param value: Parsed ``kubernetes`` TOML table.
    :param skill_root: Installed skill directory excluded from runtime inputs.
    :returns: Concrete kubernetes configuration with its declared optional fields.
    :raises TargetError: A required field, selector, path, or table entry is invalid.
    """  # noqa: DOC503 - Shared field parsers validate their declared contracts.
    kubernetes = _table(value, "kubernetes", TARGET_CONTRACT.names("kubernetes"))
    server, _ = _https_url(
        _string(kubernetes, "server", "kubernetes"), "kubernetes.server"
    )
    kubeconfig = _optional_file(kubernetes, "kubeconfig", skill_root)
    kubeconfigs = _optional_files(kubernetes, "kubeconfigs", skill_root)
    if kubeconfig and kubeconfigs:
        raise TargetError("declare kubernetes.kubeconfig or kubeconfigs, not both")
    return KubernetesTarget(
        context=_string(kubernetes, "context", "kubernetes"),
        server=server,
        kubeconfig=kubeconfig,
        kubeconfigs=kubeconfigs,
        node_diagnostics=_node_diagnostics(kubernetes.get("node_diagnostics")),
    )


def _select_surfaces(
    data: Mapping[str, object],
    required: tuple[str, ...],
    optional: tuple[str, ...],
) -> tuple[str, ...]:
    """Select required authorities and configured optional authorities once.

    :param data: Target tables from the selected TOML file.
    :param required: Authority names that must be configured for this invocation.
    :param optional: Additional supported authorities selected when their table exists.
    :returns: Distinct selected authorities in requirement and declaration order.
    :raises TargetError: Unknown tables, absent required tables, or no selected authority.
    """
    unknown = set(data) - set(AUTHORITIES)
    if unknown:
        raise TargetError(
            "target contains unsupported tables: " + ", ".join(sorted(unknown))
        )
    missing = set(required) - set(data)
    if missing:
        raise TargetError(
            "target is missing required authority tables: " + ", ".join(sorted(missing))
        )
    selected = tuple(
        dict.fromkeys((*required, *(name for name in optional if name in data)))
    )
    if not selected:
        raise TargetError("target has no configured authority tables for this command")
    return selected


def load_target(
    path: Union[str, Path],
    *,
    required_surfaces: tuple[str, ...] = DEFAULT_AUTHORITIES,
    optional_surfaces: tuple[str, ...] = (),
    gitlab_profile: Optional[str] = None,
) -> Target:
    """Load requested authorities with relative tokens beside the target file.

    Only requested authority tables are validated. Token paths may be relative,
    absolute, or home-relative; other file settings retain their own rules.

    :param path: Selected TOML target file, including an optional home prefix.
    :param required_surfaces: Distinct declared authorities the command needs.
    :param optional_surfaces: Supported authorities selected only when configured.
    :param gitlab_profile: Exact named profile; omission uses the flat GitLab table.
    :returns: Validated settings for the requested authorities.
    :raises TargetError: If the requested authorities or target data are invalid,
        unreadable, missing, or select a target or credential inside the skill.
    """
    if not (required_surfaces or optional_surfaces) or any(
        len(set(surfaces)) != len(surfaces) or set(surfaces) - set(AUTHORITIES)
        for surfaces in (required_surfaces, optional_surfaces)
    ):
        raise TargetError("required_target_surfaces_invalid")
    source = Path(path).expanduser()
    skill_root = SKILL_ROOT
    data = _read_target_data(source, skill_root)
    selected = _select_surfaces(data, required_surfaces, optional_surfaces)
    if gitlab_profile is not None and "gitlab" not in selected:
        raise TargetError("gitlab_profile_requires_gitlab_authority")

    github_target = (
        _parse_github(data["github"], skill_root, target_directory=source.parent)
        if "github" in selected
        else None
    )
    gitlab_target = (
        _parse_gitlab(
            data["gitlab"],
            skill_root,
            target_directory=source.parent,
            profile=gitlab_profile,
        )
        if "gitlab" in selected
        else None
    )
    harbor_target = (
        _parse_harbor(data["harbor"], skill_root, target_directory=source.parent)
        if "harbor" in selected
        else None
    )
    kubernetes_target = (
        _parse_kubernetes(data["kubernetes"], skill_root)
        if "kubernetes" in selected
        else None
    )
    return Target(
        github=github_target,
        gitlab=gitlab_target,
        kubernetes=kubernetes_target,
        active_surfaces=selected,
        harbor=harbor_target,
    )
