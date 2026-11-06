"""Validate explicit, nonsecret host and cluster targets for diagnostic commands.

GitLab is read from one instance table, ``[gitlab_private]`` or
``[gitlab_public]``, chosen by ``--gitlab private|public``, else by the optional
``[gitlab_routing].default``, else ``GITLAB_DEFAULT_INSTANCE``. When GitLab is
selected, a command that requires Kubernetes or Harbor reads the tables the
selected ``[gitlab_routing]`` row names; an optional authority reads its own table. The legacy ``[gitlab]`` table and its
``[gitlab.NAME]`` profiles are read for one release and write one
``gitlab_target_deprecated`` event.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import tomllib
from collections.abc import Mapping, MutableSequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Optional, Self, Union
from urllib.parse import urlsplit

from lib.python.core.catalog import (
    AUTHORITIES,
    DEFAULT_AUTHORITIES,
    GITLAB_PROFILE_OPTION,
    GITLAB_TARGET_OPTION,
)
from lib.python.core.endpoints import (
    GITHUB_AUTHORITY,
    GITLAB_AUTHORITY,
    GITLAB_DEFAULT_INSTANCE,
    GITLAB_DEPRECATION_EVENT,
    GITLAB_INSTANCE_REQUIRED,
    GITLAB_LEGACY_PUBLIC_KEY,
    GITLAB_PRIVATE_DECLARED_TWICE,
    GITLAB_PRIVATE_TABLE,
    GITLAB_PROFILE_NAME_INVALID,
    GITLAB_PROFILE_UNKNOWN,
    GITLAB_PUBLIC_TABLE,
    GITLAB_ROUTED_AUTHORITIES,
    GITLAB_ROUTING_DEFAULT_KEY,
    GITLAB_ROUTING_DEFAULT_UNKNOWN,
    GITLAB_ROUTING_PAIRING_UNKNOWN,
    GITLAB_ROUTING_ROW_MISSING,
    GITLAB_ROUTING_ROW_UNKNOWN,
    GITLAB_ROUTING_TABLE,
    GITLAB_SELECTION_REQUIRES_GITLAB,
    GITLAB_SELECTOR_CONFLICT,
    GITLAB_SELECTOR_UNKNOWN,
    GITLAB_SELECTORS,
    HARBOR_AUTHORITY,
    KUBERNETES_AUTHORITY,
    ROUTING_ARGV_PREFIX,
    ROUTING_SELECTED_BY_DEFAULT,
    ROUTING_SELECTED_BY_KEY,
    ROUTING_SELECTED_BY_LEGACY,
    TARGET_CONTRACT,
)
from lib.python.core.logging import bound_log_settings, log_event
from lib.python.core.paths import SKILL_ROOT

if TYPE_CHECKING:
    from lib.python.access.sources import Sources
    from lib.python.core.json_types import JSONObject
    from lib.python.core.provenance import SkillIdentity

GITLAB_DEPRECATION_STATUS: Final = "DEPRECATED"
GITLAB_DEPRECATION_COMPONENT: Final = "target"
# Detail keys of the deprecation event: the legacy table read and its replacement.
GITLAB_DEPRECATION_TABLE_KEY: Final = "table"
GITLAB_DEPRECATION_REPLACEMENT_KEY: Final = "replacement"
# Parsed-namespace attributes of the two selector options.
GITLAB_TARGET_DEST: Final = GITLAB_TARGET_OPTION.removeprefix("--").replace("-", "_")
GITLAB_PROFILE_DEST: Final = GITLAB_PROFILE_OPTION.removeprefix("--").replace("-", "_")
MISSING_AUTHORITY_TABLES: Final = "target is missing required authority tables: "
# A dotted header such as [gitlab.com] is a nested table, not a host name.
NESTED_TABLE_DIAGNOSTIC: Final = (
    "[{path}] is table {child} nested in [{parent}]: TOML reads a dot in a "
    "table name as nesting; {advice}"
)
GITLAB_INSTANCE_ADVICE: Final = (
    "an instance table holds no nested table; name each GitLab "
    + " or ".join(f"[{table}]" for table in GITLAB_SELECTORS.values())
)
GITLAB_REPLACEMENT: Final = (
    " or ".join(f"[{table}]" for table in GITLAB_SELECTORS.values())
    + f"; select with {GITLAB_TARGET_OPTION} {'|'.join(GITLAB_SELECTORS)}"
)


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


@dataclass(frozen=True, slots=True)
class GitLabSelection:
    """Caller choice of GitLab instance, from ``--gitlab`` or ``--gitlab-profile``.

    :param selector: ``GITLAB_SELECTORS`` key from ``--gitlab``, or ``None``.
    :param profile: Legacy ``[gitlab.NAME]`` name from ``--gitlab-profile``, or ``None``.

    .. attribute :: selector
        :type: Optional[str]

        ``GITLAB_SELECTORS`` key from ``--gitlab``, or ``None``.

    .. attribute :: profile
        :type: Optional[str]

        Legacy ``[gitlab.NAME]`` name from ``--gitlab-profile``, or ``None``.
    """

    selector: Optional[str] = None
    profile: Optional[str] = None

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> Self:
        """Read both selector options from a parsed command namespace.

        :param args: Parsed namespace; absent options select nothing.
        :returns: Selection carrying the caller's options.
        """
        return cls(
            getattr(args, GITLAB_TARGET_DEST, None),
            getattr(args, GITLAB_PROFILE_DEST, None),
        )

    @property
    def requested(self) -> bool:
        """Report whether the caller named a GitLab instance or profile.

        :returns: True when either option was supplied.
        """
        return self.selector is not None or self.profile is not None

    @property
    def selected_by(self) -> Optional[str]:
        """Render the caller's option as selection provenance.

        :returns: ``argv:--gitlab=NAME`` or ``argv:--gitlab-profile="NAME"``,
            or ``None`` when the caller named nothing.
        """
        if self.selector is not None:
            return f"{ROUTING_ARGV_PREFIX}{GITLAB_TARGET_OPTION}={self.selector}"
        if self.profile is not None:
            return f"{ROUTING_ARGV_PREFIX}{GITLAB_PROFILE_OPTION}={json.dumps(self.profile)}"
        return None

    def validate(self) -> None:
        """Reject two selectors at once and a selector outside the contract.

        :raises TargetError: Both options were given, or the selector is unknown.
        """
        if self.selector is not None and self.profile is not None:
            raise TargetError(GITLAB_SELECTOR_CONFLICT)
        if self.selector is not None and self.selector not in GITLAB_SELECTORS:
            raise TargetError(GITLAB_SELECTOR_UNKNOWN)


@dataclass(frozen=True, slots=True)
class GitLabRoute:
    """The GitLab table read and the Kubernetes and Harbor tables paired with it.

    :param gitlab: Table read: an instance table, or legacy ``gitlab``/``gitlab.NAME``.
    :param kubernetes: Paired Kubernetes table, or ``None`` when not routed.
    :param harbor: Paired Harbor table, or ``None`` when not routed.
    :param selected_by: ``default``, ``legacy``, or the caller's ``argv:`` option.

    .. attribute :: gitlab
        :type: str

        Table read: an instance table, or legacy ``gitlab``/``gitlab.NAME``.

    .. attribute :: kubernetes
        :type: Optional[str]

        Paired Kubernetes table, or ``None`` when not routed.

    .. attribute :: harbor
        :type: Optional[str]

        Paired Harbor table, or ``None`` when not routed.

    .. attribute :: selected_by
        :type: str

        ``default``, ``legacy``, or the caller's ``argv:`` option.
    """

    gitlab: str
    kubernetes: Optional[str]
    harbor: Optional[str]
    selected_by: str

    @property
    def legacy(self) -> bool:
        """Report whether the route reads the deprecated ``[gitlab]`` shape.

        :returns: True unless the table read is a declared instance table.
        """
        return self.gitlab not in GITLAB_SELECTORS.values()

    def table_for(self, authority: str) -> Optional[str]:
        """Name the table this route pairs with a routed authority.

        :param authority: One of ``GITLAB_ROUTED_AUTHORITIES``.
        :returns: Paired table name, or ``None`` when the row omits it.
        :raises KeyError: ``authority`` is not a routed authority.
        """  # noqa: DOC502 - The mapping lookup raises KeyError.
        return {KUBERNETES_AUTHORITY: self.kubernetes, HARBOR_AUTHORITY: self.harbor}[
            authority
        ]

    def as_dict(self) -> JSONObject:
        """Render the result ``routing`` object.

        :returns: Table names, ``null`` for unrouted tables, and provenance.
        """
        return {
            GITLAB_AUTHORITY: self.gitlab,
            KUBERNETES_AUTHORITY: self.kubernetes,
            HARBOR_AUTHORITY: self.harbor,
            ROUTING_SELECTED_BY_KEY: self.selected_by,
        }

    def selection_source(self, source: str) -> str:
        """Bind an explicit caller selector to report and confirmation provenance.

        :param source: File or binding selection provenance.
        :returns: ``source`` with the caller's option appended when one was given.
        """
        if self.selected_by.startswith(ROUTING_ARGV_PREFIX):
            return f"{source};{self.selected_by}"
        return source


@dataclass(frozen=True, slots=True)
class GitLabRouting:
    """The parsed ``[gitlab_routing]`` table: default instance and one route each.

    :param default: Instance read when the caller names none.
    :param routes: Route per declared instance, keyed by instance name; each
        route names the table it reads and has ``selected_by`` ``default``.

    .. attribute :: default
        :type: str

        Instance read when the caller names none.

    .. attribute :: routes
        :type: Mapping[str, GitLabRoute]

        Route per declared instance, keyed by instance name.
    """

    default: str
    routes: Mapping[str, GitLabRoute]

    @classmethod
    def parse(cls, data: Mapping[str, object], instances: Mapping[str, str]) -> Self:
        """Validate ``[gitlab_routing]`` against the instances a target declares.

        Without the table, the default is ``GITLAB_DEFAULT_INSTANCE`` and no
        instance pairs a Kubernetes or Harbor table. ``default`` is optional.

        :param data: Complete parsed target file.
        :param instances: Instance name mapped to the table that answers it.
        :returns: Default instance and the route of each declared instance.
        :raises TargetError: The table is malformed, its default or a row names an
            undeclared instance, a declared instance has no row, or a row names an
            undeclared Kubernetes or Harbor table.
        """  # noqa: DOC502 - Shared table parser validates each row.
        if GITLAB_ROUTING_TABLE not in data:
            routes = {
                name: GitLabRoute(table, None, None, ROUTING_SELECTED_BY_DEFAULT)
                for name, table in instances.items()
            }
            return cls(GITLAB_DEFAULT_INSTANCE, MappingProxyType(routes))
        table = data[GITLAB_ROUTING_TABLE]
        if not isinstance(table, dict):
            raise TargetError(f"{GITLAB_ROUTING_TABLE} must be a TOML table")
        default = table.get(GITLAB_ROUTING_DEFAULT_KEY, GITLAB_DEFAULT_INSTANCE)
        if GITLAB_ROUTING_DEFAULT_KEY in table and (
            not isinstance(default, str) or default not in instances
        ):
            raise TargetError(GITLAB_ROUTING_DEFAULT_UNKNOWN)
        rows = {
            name: row
            for name, row in table.items()
            if name != GITLAB_ROUTING_DEFAULT_KEY
        }
        if set(rows) - set(instances):
            raise TargetError(GITLAB_ROUTING_ROW_UNKNOWN)
        if set(instances) - set(rows):
            raise TargetError(GITLAB_ROUTING_ROW_MISSING)
        routes: dict[str, GitLabRoute] = {}
        for instance, source in instances.items():
            name = TARGET_CONTRACT.dotted_path((GITLAB_ROUTING_TABLE, instance))
            row = _table(rows[instance], name, TARGET_CONTRACT.names(name))
            paired: dict[str, Optional[str]] = {}
            for authority in GITLAB_ROUTED_AUTHORITIES:
                value = row.get(authority)
                if value is not None and (
                    not isinstance(value, str)
                    or TARGET_CONTRACT.authority_of(value) != authority
                    or value not in data
                ):
                    raise TargetError(cls.pairing_unknown(authority))
                paired[authority] = value
            routes[instance] = GitLabRoute(
                gitlab=source, selected_by=ROUTING_SELECTED_BY_DEFAULT, **paired
            )
        return cls(str(default), MappingProxyType(routes))

    def select(self, selection: GitLabSelection) -> GitLabRoute:
        """Choose the caller's instance, or the default when the caller names none.

        :param selection: Validated caller selection without a legacy profile.
        :returns: Route of the selected instance with its provenance.
        :raises TargetError: The selected or default instance is not declared.
        """
        instance = (
            self.default
            if selection.selector is None
            else GITLAB_SELECTORS[selection.selector]
        )
        if instance not in self.routes:
            raise TargetError(self.instance_required(instance))
        route = self.routes[instance]
        selected_by = selection.selected_by or (
            ROUTING_SELECTED_BY_LEGACY if route.legacy else ROUTING_SELECTED_BY_DEFAULT
        )
        return replace(route, selected_by=selected_by)

    def route_for(self, table: str) -> Optional[GitLabRoute]:
        """Find the route of the instance a given table answers.

        :param table: Table path, such as ``gitlab.com``.
        :returns: That instance's route, or ``None`` when no instance reads it.
        """
        return next(
            (route for route in self.routes.values() if route.gitlab == table), None
        )

    @staticmethod
    def instance_required(table: str) -> str:
        """Name the reason for a selected instance table the target lacks.

        :param table: Instance table name, such as ``gitlab_public``.
        :returns: Reason token, for example ``gitlab_public_target_required``.
        """
        return GITLAB_INSTANCE_REQUIRED.format(table=table)

    @staticmethod
    def pairing_unknown(authority: str) -> str:
        """Name the reason for a routing row that names an undeclared table.

        :param authority: Routed authority whose value is invalid.
        :returns: Reason token, for example ``gitlab_routing_kubernetes_unknown``.
        """
        return GITLAB_ROUTING_PAIRING_UNKNOWN.format(
            routing=GITLAB_ROUTING_TABLE, authority=authority
        )


@dataclass(frozen=True)
class GitLabTarget:
    """Select a GitLab origin and configured operation scopes.

    :param url: GitLab HTTPS origin used for API requests.
    :param host: Network authority extracted from the origin.
    :param token_file: Existing token file, or ``None`` to use runtime credential resolution.
    :param project: Project path, or ``None`` when no project is selected.
    :param group: Group path, or ``None`` when no group is selected.
    :param runner_id: Positive runner ID, or ``None`` when no runner is selected.
    :param route: Table read and its pairing, or ``None`` for a constructed target.

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

    .. attribute :: route
        :type: Optional[GitLabRoute]

        Table read and its pairing, or ``None`` for a constructed target.
    """

    url: str
    host: str
    token_file: Optional[Path] = None
    project: Optional[str] = None
    group: Optional[str] = None
    runner_id: Optional[int] = None
    route: Optional[GitLabRoute] = None

    def selection_source(self, source: str) -> str:
        """Bind an explicit caller selector to report and confirmation provenance.

        :param source: File or binding selection provenance, kept unchanged by default.
        :returns: The source with the caller's selector when one was given.
        """
        return self.route.selection_source(source) if self.route else source


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

    @property
    def routing(self) -> Optional[GitLabRoute]:
        """Return the selected GitLab route.

        :returns: Route of the selected GitLab, or ``None`` for a constructed target.
        """
        return self.gitlab.route


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

    @property
    def routing(self) -> Optional[GitLabRoute]:
        """Return the selected GitLab route, shared by every GitLab result.

        :returns: Route of the selected GitLab, or ``None`` when GitLab is unselected.
        """
        return self.gitlab.route if self.gitlab else None

    def selection_source(self, source: str) -> str:
        """Include the selected GitLab instance in provider-selection provenance.

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
    name: Optional[str] = None,
) -> Optional[Path]:
    """Resolve an optional file, using its target directory when supplied.

    :param table: Parsed authority settings containing the optional path.
    :param key: File setting to resolve.
    :param skill_root: Installed skill directory excluded from file selections.
    :param target_directory: Directory containing the selected target file.
    :param name: Table that qualifies the key in diagnostics, or ``None``.
    :returns: Canonical file path, or None when the setting is absent.
    :raises TargetError: If the path is empty, invalid, or inside the skill.
    """
    label = f"{name}.{key}" if name else key
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TargetError(f"{label} must be a nonempty path when supplied")
    selected = Path(value).expanduser()
    if target_directory is not None and not selected.is_absolute():
        selected = target_directory / selected
    path = selected.resolve()
    assert_external_path(path, skill_root, label)
    return path


def _optional_reference(
    table: Mapping[str, object], key: str, name: str = GITLAB_AUTHORITY
) -> Optional[str]:
    """Normalize a GitLab project or group selector.

    :param table: Table containing the optional selector.
    :param key: ``project`` or ``group`` field name for diagnostics.
    :param name: Table that qualifies the key in diagnostics.
    :returns: Positive numeric ID as text or a relative path, or ``None`` when
        absent.
    :raises TargetError: The selector is invalid, absolute, empty, or contains
        whitespace or a URL scheme.
    """
    value = table.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TargetError(f"{name}.{key} must be a path or positive numeric ID")
    if isinstance(value, int):
        if value <= 0:
            raise TargetError(f"{name}.{key} must be a positive numeric ID")
        return str(value)
    selected = value.strip()
    if (
        not selected
        or selected.startswith("/")
        or selected.endswith("/")
        or "://" in selected
        or any(character.isspace() for character in selected)
    ):
        raise TargetError(f"{name}.{key} must be a relative path or numeric ID")
    return selected


def _legacy_table(data: Mapping[str, object]) -> Optional[Mapping[str, object]]:
    """Validate the deprecated ``[gitlab]`` table when the target declares it.

    :param data: Complete parsed target file.
    :returns: The legacy table, or ``None`` when absent.
    :raises TargetError: The table is not a table or holds a malformed child.
    """
    value = data.get(GITLAB_AUTHORITY)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TargetError("gitlab must be a table")
    fields = TARGET_CONTRACT.names(GITLAB_AUTHORITY)
    invalid = {
        name
        for name in set(value) - fields
        if not name.strip() or not isinstance(value[name], dict)
    }
    if invalid:
        raise TargetError(
            "gitlab contains unsupported fields: " + ", ".join(sorted(invalid))
        )
    return value


def _declared_instances(data: Mapping[str, object]) -> Mapping[str, str]:
    """Map each declared GitLab instance to the table that answers it.

    ``[gitlab_private]`` and ``[gitlab_public]`` answer their instances. For one
    release, legacy ``[gitlab]`` fields answer ``gitlab_private`` and the child
    table under ``GITLAB_LEGACY_PUBLIC_KEY`` answers ``gitlab_public`` when
    ``[gitlab_public]`` is absent. These are table keys; no host is inspected.

    :param data: Complete parsed target file.
    :returns: Instance name mapped to the table path it reads.
    :raises TargetError: Legacy ``[gitlab]`` fields and ``[gitlab_private]`` both
        declare ``gitlab_private``.
    """
    legacy = _legacy_table(data)
    fields = TARGET_CONTRACT.names(GITLAB_AUTHORITY)
    instances: dict[str, str] = {}
    if legacy is not None and any(key in fields for key in legacy):
        if GITLAB_PRIVATE_TABLE in data:
            raise TargetError(GITLAB_PRIVATE_DECLARED_TWICE)
        instances[GITLAB_PRIVATE_TABLE] = GITLAB_AUTHORITY
    if GITLAB_PRIVATE_TABLE in data:
        instances[GITLAB_PRIVATE_TABLE] = GITLAB_PRIVATE_TABLE
    if GITLAB_PUBLIC_TABLE in data:
        instances[GITLAB_PUBLIC_TABLE] = GITLAB_PUBLIC_TABLE
    elif legacy is not None and isinstance(legacy.get(GITLAB_LEGACY_PUBLIC_KEY), dict):
        instances[GITLAB_PUBLIC_TABLE] = TARGET_CONTRACT.dotted_path(
            (GITLAB_AUTHORITY, GITLAB_LEGACY_PUBLIC_KEY)
        )
    return MappingProxyType(instances)


def _table_settings(data: Mapping[str, object], path: str) -> Mapping[str, object]:
    """Read the settings of one GitLab table path with its own diagnostics.

    :param data: Complete parsed target file.
    :param path: ``gitlab``, ``gitlab.NAME``, or an instance table name.
    :returns: Settings of that table.
    :raises TargetError: The table holds a nested or unsupported entry.
    """  # noqa: DOC502 - Shared table parser raises TargetError.
    fields = TARGET_CONTRACT.names(GITLAB_AUTHORITY)
    legacy = data.get(GITLAB_AUTHORITY)
    if path in GITLAB_SELECTORS.values():
        _reject_nested(data[path], path)
        return _table(data[path], path, TARGET_CONTRACT.names(path))
    if not isinstance(legacy, dict):
        raise TargetError("gitlab must be a table")
    if path == GITLAB_AUTHORITY:
        return {key: item for key, item in legacy.items() if key in fields}
    _, child = TARGET_CONTRACT.path_parts(path)
    return _table(legacy[child], path, fields)


def _select_gitlab(
    data: Mapping[str, object], selection: GitLabSelection
) -> tuple[Mapping[str, object], GitLabRoute]:
    """Select one GitLab table and its route from a complete target file.

    :param data: Complete parsed target file.
    :param selection: Caller selection from ``--gitlab`` or ``--gitlab-profile``.
    :returns: The selected table's settings and its route.
    :raises TargetError: Selectors conflict, ``gitlab_private`` is declared
        twice, routing is invalid, or the selected table is absent or malformed.
    """  # noqa: DOC502 - Routing and table parsers raise TargetError.
    selection.validate()
    instances = _declared_instances(data)
    routing = GitLabRouting.parse(data, instances)
    if selection.profile is None:
        route = routing.select(selection)
        return _table_settings(data, route.gitlab), route
    legacy = _legacy_table(data)
    if not selection.profile.strip():
        raise TargetError(GITLAB_PROFILE_NAME_INVALID)
    if (
        legacy is None
        or selection.profile in TARGET_CONTRACT.names(GITLAB_AUTHORITY)
        or not isinstance(legacy.get(selection.profile), dict)
    ):
        raise TargetError(GITLAB_PROFILE_UNKNOWN)
    name = TARGET_CONTRACT.dotted_path((GITLAB_AUTHORITY, selection.profile))
    paired = routing.route_for(name)
    route = GitLabRoute(
        name,
        paired.kubernetes if paired else None,
        paired.harbor if paired else None,
        selection.selected_by or ROUTING_SELECTED_BY_LEGACY,
    )
    return _table_settings(data, name), route


def _reject_nested(value: object, name: str) -> None:
    """Explain a dotted header that TOML read as a table nested in an instance.

    :param value: Parsed instance table that may hold nested tables.
    :param name: Instance table name, such as ``gitlab_public``.
    :raises TargetError: A nested table is present, named with its parent.
    """
    if not isinstance(value, dict):
        return
    nested = sorted(key for key, item in value.items() if isinstance(item, dict))
    if nested:
        raise TargetError(
            NESTED_TABLE_DIAGNOSTIC.format(
                path=TARGET_CONTRACT.dotted_path((name, nested[0])),
                parent=name,
                child=TARGET_CONTRACT.quote_key(nested[0]),
                advice=GITLAB_INSTANCE_ADVICE,
            )
        )


def _parse_gitlab(
    gitlab: Mapping[str, object],
    route: GitLabRoute,
    skill_root: Path,
    *,
    target_directory: Path,
) -> GitLabTarget:
    """Parse the selected GitLab settings and resolve their token beside the target.

    :param gitlab: Settings of the selected table.
    :param route: Selected table name, pairing, and provenance.
    :param skill_root: Installed skill directory excluded from file selections.
    :param target_directory: Directory containing the selected target file.
    :returns: Validated GitLab settings with a canonical token path if supplied.
    :raises TargetError: If a GitLab setting or token path is invalid; the
        diagnostic names the table read.
    """
    name = route.gitlab
    url, host = _https_url(_string(gitlab, "url", name), f"{name}.url")
    runner_id = gitlab.get("runner_id")
    if runner_id is not None and (
        isinstance(runner_id, bool) or not isinstance(runner_id, int) or runner_id <= 0
    ):
        raise TargetError(f"{name}.runner_id must be a positive numeric ID")
    return GitLabTarget(
        url=url,
        host=host,
        token_file=_optional_file(
            gitlab,
            "token_file",
            skill_root,
            target_directory=target_directory,
            name=name,
        ),
        project=_optional_reference(gitlab, "project", name),
        group=_optional_reference(gitlab, "group", name),
        runner_id=runner_id,
        route=route,
    )


def _load_gitlab(
    data: Mapping[str, object],
    selection: GitLabSelection,
    skill_root: Path,
    *,
    target_directory: Path,
) -> GitLabTarget:
    """Select, parse, and report the GitLab table of a loaded target file.

    A target that declares the legacy ``[gitlab]`` table, or a selection made
    with ``--gitlab-profile``, writes one ``gitlab_target_deprecated``
    diagnostic with the invocation's bound log settings.

    :param data: Complete parsed target file.
    :param selection: Caller selection from ``--gitlab`` or ``--gitlab-profile``.
    :param skill_root: Installed skill directory excluded from file selections.
    :param target_directory: Directory containing the selected target file.
    :returns: Validated GitLab settings carrying their route.
    :raises TargetError: Selection, routing, or a GitLab setting is invalid.
    """  # noqa: DOC502 - Selection and field parsers raise TargetError.
    table, route = _select_gitlab(data, selection)
    selected = _parse_gitlab(
        table, route, skill_root, target_directory=target_directory
    )
    if GITLAB_AUTHORITY in data or selection.profile is not None:
        settings = bound_log_settings()
        try:
            log_event(
                settings,
                GITLAB_DEPRECATION_COMPONENT,
                GITLAB_DEPRECATION_EVENT,
                GITLAB_DEPRECATION_STATUS,
                level="warning",
                # Same rule as the command's own events: no log file in dry-run.
                persist=not (
                    getattr(settings, "describe", False)
                    or getattr(settings, "dry_run", False)
                ),
                details={
                    GITLAB_DEPRECATION_TABLE_KEY: (
                        route.gitlab if route.legacy else GITLAB_AUTHORITY
                    ),
                    ROUTING_SELECTED_BY_KEY: route.selected_by,
                    GITLAB_DEPRECATION_REPLACEMENT_KEY: GITLAB_REPLACEMENT,
                },
            )
        except OSError:
            # A diagnostic that cannot be written does not change the selection.
            pass
    return selected


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
    path: Union[str, Path],
    *,
    gitlab: Optional[str] = None,
    gitlab_profile: Optional[str] = None,
) -> GitLabOperationTarget:
    """Load GitLab settings with relative tokens beside the selected target.

    Other declared authority tables are permitted but are not validated here.

    :param path: Selected TOML target file, including an optional home prefix.
    :param gitlab: ``GITLAB_SELECTORS`` key; omission reads the routing default,
        else ``GITLAB_DEFAULT_INSTANCE``.
    :param gitlab_profile: Deprecated legacy ``[gitlab.NAME]`` profile name.
    :returns: GitLab settings and the canonical source-file path.
    :raises TargetError: If the target is unreadable, invalid, inside the skill,
        or contains invalid GitLab settings or an internal credential path.
    """  # noqa: DOC502 - Shared table and field parsers raise TargetError.
    source = Path(path).expanduser()
    skill_root = SKILL_ROOT
    data = _read_target_data(source, skill_root)
    _select_surfaces(data, (GITLAB_AUTHORITY,), ())
    selected = _load_gitlab(
        data,
        GitLabSelection(gitlab, gitlab_profile),
        skill_root,
        target_directory=source.parent,
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
    github = _table(value, GITHUB_AUTHORITY, TARGET_CONTRACT.names(GITHUB_AUTHORITY))
    github_host = _string(github, "host", GITHUB_AUTHORITY).lower()
    if "." not in github_host or "/" in github_host or ":" in github_host:
        raise TargetError("github.host must be a full hostname")
    repository = _string(github, "repository", GITHUB_AUTHORITY)
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
    harbor = _table(value, HARBOR_AUTHORITY, TARGET_CONTRACT.names(HARBOR_AUTHORITY))
    url, host = _https_url(_string(harbor, "url", HARBOR_AUTHORITY), "harbor.url")
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
    return HarborTarget(
        url, host, _string(harbor, "project", HARBOR_AUTHORITY), **files
    )


def _parse_kubernetes(value: object, skill_root: Path) -> KubernetesTarget:
    """Validate only the kubernetes table selected by the command.

    :param value: Parsed ``kubernetes`` TOML table.
    :param skill_root: Installed skill directory excluded from runtime inputs.
    :returns: Concrete kubernetes configuration with its declared optional fields.
    :raises TargetError: A required field, selector, path, or table entry is invalid.
    """  # noqa: DOC503 - Shared field parsers validate their declared contracts.
    kubernetes = _table(
        value, KUBERNETES_AUTHORITY, TARGET_CONTRACT.names(KUBERNETES_AUTHORITY)
    )
    server, _ = _https_url(
        _string(kubernetes, "server", KUBERNETES_AUTHORITY), "kubernetes.server"
    )
    kubeconfig = _optional_file(kubernetes, "kubeconfig", skill_root)
    kubeconfigs = _optional_files(kubernetes, "kubeconfigs", skill_root)
    if kubeconfig and kubeconfigs:
        raise TargetError("declare kubernetes.kubeconfig or kubeconfigs, not both")
    return KubernetesTarget(
        context=_string(kubernetes, "context", KUBERNETES_AUTHORITY),
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
    unknown = set(data) - set(TARGET_CONTRACT.tables)
    if unknown:
        raise TargetError(
            "target contains unsupported tables: " + ", ".join(sorted(unknown))
        )
    present = {TARGET_CONTRACT.authority_of(name) for name in data}
    missing = set(required) - present
    if missing:
        raise TargetError(MISSING_AUTHORITY_TABLES + ", ".join(sorted(missing)))
    selected = tuple(
        dict.fromkeys((*required, *(name for name in optional if name in present)))
    )
    if not selected:
        raise TargetError("target has no configured authority tables for this command")
    return selected


def _legacy_compat(
    data: Mapping[str, object],
    selection: GitLabSelection,
    gitlab_target: Optional[GitLabTarget],
) -> bool:
    """Decide whether a legacy route reads Kubernetes and Harbor tables directly.

    That holds only on a target without ``[gitlab_routing]``, for a caller that
    gave no ``--gitlab``, and for a legacy table that does not answer
    ``gitlab_public``: the public instance is never paired with the person's
    tables by default, whichever option selected it.

    :param data: Complete parsed target file.
    :param selection: Caller's selection.
    :param gitlab_target: Selected GitLab target, or ``None`` when unselected.
    :returns: True when the legacy direct read applies.
    """  # noqa: DOC502 - The instance map validates the legacy table.
    if GITLAB_ROUTING_TABLE in data or selection.selector is not None:
        return False
    if gitlab_target is None:
        return True
    public = _declared_instances(data).get(GITLAB_PUBLIC_TABLE)
    return gitlab_target.route.gitlab != public


def _routed_tables(
    route: Optional[GitLabRoute], required: tuple[str, ...], legacy_compat: bool
) -> Mapping[str, str]:
    """Name the table each routed authority is read from under ``[gitlab_routing]``.

    A command that requires a routed authority reads the table the selected
    route pairs with it. An absent route, an optional authority, or a legacy
    route under ``legacy_compat`` reads the authority's own table.

    :param route: Selected GitLab route, or ``None`` when GitLab is unselected.
    :param required: Authorities the command requires.
    :param legacy_compat: Result of ``_legacy_compat``: a legacy route reads
        tables as before this change.
    :returns: Table name per routed authority.
    :raises TargetError: The selected route pairs no table with one or more
        required routed authorities; the diagnostic names all of them.
    """
    tables = {authority: authority for authority in GITLAB_ROUTED_AUTHORITIES}
    if route is None or (route.legacy and legacy_compat):
        return tables
    missing = []
    for authority in GITLAB_ROUTED_AUTHORITIES:
        if authority not in required:
            continue
        table = route.table_for(authority)
        if table is None:
            missing.append(authority)
        else:
            tables[authority] = table
    if missing:
        raise TargetError(MISSING_AUTHORITY_TABLES + ", ".join(sorted(missing)))
    return tables


def load_target(
    path: Union[str, Path],
    *,
    required_surfaces: tuple[str, ...] = DEFAULT_AUTHORITIES,
    optional_surfaces: tuple[str, ...] = (),
    gitlab: Optional[str] = None,
    gitlab_profile: Optional[str] = None,
) -> Target:
    """Load requested authorities with relative tokens beside the target file.

    Only requested authority tables are validated. Token paths may be relative,
    absolute, or home-relative; other file settings retain their own rules.

    :param path: Selected TOML target file, including an optional home prefix.
    :param required_surfaces: Distinct declared authorities the command needs.
    :param optional_surfaces: Supported authorities selected only when configured.
    :param gitlab: ``GITLAB_SELECTORS`` key; omission reads the routing default,
        else ``GITLAB_DEFAULT_INSTANCE``.
    :param gitlab_profile: Deprecated legacy ``[gitlab.NAME]`` profile name.
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
    selection = GitLabSelection(gitlab, gitlab_profile)
    if selection.requested and GITLAB_AUTHORITY not in selected:
        raise TargetError(GITLAB_SELECTION_REQUIRES_GITLAB)

    github_target = (
        _parse_github(
            data[GITHUB_AUTHORITY], skill_root, target_directory=source.parent
        )
        if GITHUB_AUTHORITY in selected
        else None
    )
    gitlab_target = (
        _load_gitlab(data, selection, skill_root, target_directory=source.parent)
        if GITLAB_AUTHORITY in selected
        else None
    )
    tables = _routed_tables(
        gitlab_target.route if gitlab_target is not None else None,
        required_surfaces,
        _legacy_compat(data, selection, gitlab_target),
    )
    harbor_target = (
        _parse_harbor(
            data[tables[HARBOR_AUTHORITY]], skill_root, target_directory=source.parent
        )
        if HARBOR_AUTHORITY in selected
        else None
    )
    kubernetes_target = (
        _parse_kubernetes(data[tables[KUBERNETES_AUTHORITY]], skill_root)
        if KUBERNETES_AUTHORITY in selected
        else None
    )
    return Target(
        github=github_target,
        gitlab=gitlab_target,
        kubernetes=kubernetes_target,
        active_surfaces=selected,
        harbor=harbor_target,
    )
