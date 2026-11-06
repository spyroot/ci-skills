"""Declare what a target file may hold.

A target file answers, per authority, which endpoint ci-skills points at (a
host, a project, a cluster) and where the access to it lives (a token file or a
kubeconfig). `~/.ci-skills/target.toml` is the default; a project's
`.ci-skills/target.toml` (selected by `catalog.TARGET_PROTOCOL`) selects its
complete configuration without merging another file. Everything else a command needs --
pods, nodes, namespaces, pipelines, milestones, merge requests -- it reads from
the live system at run time.

`TARGET_CONTRACT` is the one declaration of the allowed keys. `core/target.py`
accepts exactly these, and `core/catalog.py` derives `catalog.AUTHORITIES` from
it. The repository gate that checks the tracked documents against it,
`gates/gate-ci-skills-endpoints.py`, lives outside the skill
(`tools/skillkit/endpoint_gate.py`). Adding an authority (a registry, say) is one
`Table` here, with its endpoint and its access.

GitLab is one authority, `gitlab`, with two instance tables and the optional
`[gitlab_routing]` table; the `GITLAB_*` constants below name them, and
`core/target.py` selects among them. Table names use `_`, never `.`: TOML reads
`[gitlab.com]` as table `com` nested in `[gitlab]`. `core/target.py` reads the
legacy `[gitlab]` table and its `[gitlab.NAME]` profiles for one release (TD-11).

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterator, MutableMapping, MutableSequence, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Optional, Union

BARE_TOML_KEY: Final = re.compile(r"[A-Za-z0-9_-]+\Z")
# Authority names: each is the top-level table of that authority in TARGET_CONTRACT.
GITHUB_AUTHORITY: Final = "github"
HARBOR_AUTHORITY: Final = "harbor"
KUBERNETES_AUTHORITY: Final = "kubernetes"
# The one GitLab authority and its two instance tables. `--gitlab` choices are
# the keys of GITLAB_SELECTORS; each value is the table that choice reads.
GITLAB_AUTHORITY: Final = "gitlab"
GITLAB_PRIVATE_TABLE: Final = "gitlab_private"
GITLAB_PUBLIC_TABLE: Final = "gitlab_public"
GITLAB_PRIVATE_SELECTOR: Final = "private"
GITLAB_PUBLIC_SELECTOR: Final = "public"
# The command option that takes one GITLAB_SELECTORS key.
GITLAB_TARGET_OPTION: Final = "--gitlab"
GITLAB_SELECTORS: Final = MappingProxyType(
    {
        GITLAB_PRIVATE_SELECTOR: GITLAB_PRIVATE_TABLE,
        GITLAB_PUBLIC_SELECTOR: GITLAB_PUBLIC_TABLE,
    }
)
# The instance read when neither `--gitlab` nor `[gitlab_routing].default` names one.
GITLAB_DEFAULT_INSTANCE: Final = GITLAB_PRIVATE_TABLE
# Legacy shape, read for one release: `[gitlab]` fields answer `gitlab_private` and
# the child table under this key answers `gitlab_public`. Matched by key, never by URL.
GITLAB_LEGACY_PUBLIC_KEY: Final = "com"
# `[gitlab_routing]`: optional `default` names the instance read without `--gitlab`;
# one row per declared instance names its paired Kubernetes and Harbor tables.
GITLAB_ROUTING_TABLE: Final = "gitlab_routing"
GITLAB_ROUTING_DEFAULT_KEY: Final = "default"
GITLAB_ROUTED_AUTHORITIES: Final = (KUBERNETES_AUTHORITY, HARBOR_AUTHORITY)
# The result `routing` object: the GitLab table read, each routed table or null,
# and how the instance was selected (ROUTING_SELECTED_BY_* or the caller's option).
ROUTING_RESULT_KEY: Final = "routing"
ROUTING_SELECTED_BY_KEY: Final = "selected_by"
ROUTING_SELECTED_BY_DEFAULT: Final = "default"
ROUTING_SELECTED_BY_LEGACY: Final = "legacy"
ROUTING_ARGV_PREFIX: Final = "argv:"
# Reason tokens of GitLab selection and routing. The two templates take a table
# name, or the routing table and a routed authority.
GITLAB_SELECTOR_CONFLICT: Final = "gitlab_selector_conflict"
GITLAB_SELECTOR_UNKNOWN: Final = "gitlab_selector_unknown"
GITLAB_SELECTION_REQUIRES_GITLAB: Final = "gitlab_profile_requires_gitlab_authority"
GITLAB_PROFILE_NAME_INVALID: Final = "gitlab_profile_name_invalid"
GITLAB_PROFILE_UNKNOWN: Final = "gitlab_profile_unknown"
GITLAB_PRIVATE_DECLARED_TWICE: Final = "gitlab_private_declared_twice"
GITLAB_ROUTING_DEFAULT_UNKNOWN: Final = "gitlab_routing_default_unknown"
GITLAB_ROUTING_ROW_UNKNOWN: Final = "gitlab_routing_row_unknown"
GITLAB_ROUTING_ROW_MISSING: Final = "gitlab_routing_row_missing"
GITLAB_INSTANCE_REQUIRED: Final = "{table}_target_required"
GITLAB_ROUTING_PAIRING_UNKNOWN: Final = "{routing}_{authority}_unknown"
# One stderr diagnostic per load of the legacy shape (TD-11 removes it).
GITLAB_DEPRECATION_EVENT: Final = "gitlab_target_deprecated"
# Values defined only in this module. tools/skillkit/portability_gate.py rejects a
# copy of any of them as a string literal in another Python source.
GITLAB_CONTRACT_LITERALS: Final = (
    *GITLAB_SELECTORS.values(),
    GITLAB_ROUTING_TABLE,
    GITLAB_TARGET_OPTION,
    GITLAB_SELECTOR_CONFLICT,
    GITLAB_SELECTOR_UNKNOWN,
    GITLAB_SELECTION_REQUIRES_GITLAB,
    GITLAB_PROFILE_NAME_INVALID,
    GITLAB_PROFILE_UNKNOWN,
    GITLAB_PRIVATE_DECLARED_TWICE,
    GITLAB_ROUTING_DEFAULT_UNKNOWN,
    GITLAB_ROUTING_ROW_UNKNOWN,
    GITLAB_ROUTING_ROW_MISSING,
    GITLAB_DEPRECATION_EVENT,
    *(
        GITLAB_INSTANCE_REQUIRED.format(table=table)
        for table in GITLAB_SELECTORS.values()
    ),
    *(
        GITLAB_ROUTING_PAIRING_UNKNOWN.format(
            routing=GITLAB_ROUTING_TABLE, authority=authority
        )
        for authority in GITLAB_ROUTED_AUTHORITIES
    ),
)


class Role(StrEnum):
    """Classify a declared target key by the responsibility it carries.

    .. attribute :: ENDPOINT

        Select the host, project, or cluster an operation addresses.

    .. attribute :: ACCESS

        Refer to existing credential material without retaining its value.

    .. attribute :: VALUE

        Preserve a legacy resource value accepted by an existing command.
    """

    # Which host, project or cluster ci-skills points at.
    ENDPOINT = "endpoint"
    # Where the credential for that endpoint lives: a file path, never a value.
    ACCESS = "access"
    # A value a command could read from the live system. Accepted only because a
    # current command still reads it; the gate refuses any new one.
    VALUE = "value"


@dataclass(frozen=True)
class Field:
    """One target-file key and its endpoint, access, or value role.

    :param name: Key accepted directly under a target table.
    :param role: Classification used by parser and documentation gate.

    .. attribute :: name
        :type: str

        Key accepted directly under a target table.

    .. attribute :: role
        :type: Role

        Classification used by parser and documentation gate.
    """

    name: str
    role: Role

    @classmethod
    def of(cls, role: Role, *names: str) -> tuple[Field, ...]:
        """Declare several target fields with one role in input order.

        :param role: Endpoint, access, or value role assigned to each field.
        :param *names: Target key names to declare.
        :returns: One ``Field`` per supplied name, preserving order.
        """
        return tuple(cls(name, role) for name in names)


@dataclass(frozen=True)
class Table:
    """One allowed target TOML table and its immediate declarations.

    :param name: Table segment in a dotted target path.
    :param fields: Keys accepted directly under the table.
    :param tables: Nested tables accepted directly under the table.
    :param named_profiles: Whether one named child may repeat this table's fields.
    :param instances: Other top-level table names that declare this authority.

    .. attribute :: name
        :type: str

        Table segment in a dotted target path.

    .. attribute :: fields
        :type: tuple[Field, ...]

        Keys accepted directly under the table.

    .. attribute :: tables
        :type: tuple[Table, ...]

        Nested tables accepted directly under the table.

    .. attribute :: named_profiles
        :type: bool

        Whether one named child may repeat the declared fields.

    .. attribute :: instances
        :type: tuple[str, ...]

        Other top-level table names that declare this authority.
    """

    name: str
    fields: tuple[Field, ...] = ()
    tables: tuple[Table, ...] = ()
    named_profiles: bool = False
    instances: tuple[str, ...] = ()

    def names(self) -> frozenset[str]:
        """List keys accepted directly beneath this target table.

        :returns: Names of this table's fields and immediate nested tables.
        """
        return frozenset(item.name for item in self.fields) | frozenset(
            table.name for table in self.tables
        )

    def walk(self, prefix: str = "") -> Iterator[tuple[str, Union[Table, Field]]]:
        """Enumerate this target table and its descendants by dotted path.

        :param prefix: Parent dotted path, including its separator when nonempty.
        :yields: This table, then nested fields and tables with their dotted paths.
        :ytype: tuple[str, Union[Table, Field]]
        """
        path = f"{prefix}{self.name}"
        yield path, self
        for item in self.fields:
            yield f"{path}.{item.name}", item
        for table in self.tables:
            yield from table.walk(f"{path}.")


class TargetContract:
    """The allowed tables and keys of a target file, by dotted path."""

    def __init__(self, authorities: tuple[Table, ...], routing: Optional[Table] = None):
        """Index target authority and routing declarations for lookup.

        :param authorities: Top-level authority tables allowed in a target file.
        :param routing: The ``[gitlab_routing]`` table declaration. When the value
            is ``None``, the contract declares no routing table, ``tables`` omits
            it and ``routing_table`` returns ``None``.
        """
        self._authorities = authorities
        self._routing = routing
        self._paths: MutableMapping[str, Union[Table, Field]] = {}
        for table in (*authorities, *((routing,) if routing else ())):
            self._paths.update(table.walk())

    @property
    def authorities(self) -> tuple[str, ...]:
        """Return declared top-level authority names in definition order.

        :returns: Tuple of authority table names.
        """
        return tuple(table.name for table in self._authorities)

    @property
    def tables(self) -> tuple[str, ...]:
        """Return every top-level table name a target file may declare.

        :returns: Authority, instance, and routing table names.
        """
        return (
            *(
                name
                for table in self._authorities
                for name in (table.name, *table.instances)
            ),
            *((self._routing.name,) if self._routing else ()),
        )

    def authority_of(self, table: str) -> Optional[str]:
        """Resolve a top-level table name to the authority it declares.

        :param table: Top-level table name, such as ``gitlab_public``.
        :returns: Authority name, such as ``gitlab`` for ``gitlab_public``;
            ``None`` for ``gitlab_routing`` and for a name the contract does not
            declare.
        """
        for authority in self._authorities:
            if table == authority.name or table in authority.instances:
                return authority.name
        return None

    def routing_table(self) -> Optional[str]:
        """Return the name of the routing table, ``gitlab_routing``.

        :returns: ``gitlab_routing``, or ``None`` when the caller built the
            contract with ``routing=None``.
        """
        return self._routing.name if self._routing else None

    @staticmethod
    def quote_key(name: str) -> str:
        """Preserve exact TOML keys, including quoted dots and spaces.

        :param name: One exact table or field key.
        :returns: Bare or JSON-quoted TOML key spelling.
        """
        return name if BARE_TOML_KEY.fullmatch(name) else json.dumps(name)

    @staticmethod
    def path_parts(path: str) -> tuple[str, ...]:
        """Parse dotted TOML paths without splitting quoted profile names.

        :param path: One dotted TOML table or field path.
        :returns: Exact key segments, or an empty tuple for malformed syntax.
        """
        try:
            value = tomllib.loads(f"[{path}]")
        except tomllib.TOMLDecodeError:
            return ()
        parts: MutableSequence[str] = []
        while isinstance(value, dict) and len(value) == 1:
            key, value = next(iter(value.items()))
            parts.append(key)
        return tuple(parts)

    @classmethod
    def dotted_path(cls, parts: Sequence[str]) -> str:
        """Render an unambiguous path for exact TOML keys.

        :param parts: Exact table and field key segments.
        :returns: Dot-separated path with quoting only where needed.
        """
        return ".".join(cls.quote_key(part) for part in parts)

    def canonical_path(self, path: str) -> str:
        """Resolve an instance or named profile path to its authority declaration.

        :param path: Concrete dotted table or field path.
        :returns: Declared authority path for an instance table or a valid
            profile, otherwise the input.
        """
        if path in self._paths:
            return path
        parts = self.path_parts(path)
        owner = self.authority_of(parts[0]) if parts else None
        if owner is not None and owner != parts[0]:
            return self.dotted_path((owner, *parts[1:]))
        authority = self._paths.get(parts[0]) if parts else None
        if (
            isinstance(authority, Table)
            and authority.named_profiles
            and len(parts) in {2, 3}
            and parts[1] not in authority.names()
            and parts[1].strip()
        ):
            return self.dotted_path((parts[0], *parts[2:]))
        return self.dotted_path(parts) if parts else path

    def names(self, path: str) -> frozenset[str]:
        """Return direct keys accepted beneath a declared target table.

        :param path: Dotted path of the table to inspect.
        :returns: Field and immediate nested-table names.
        :raises KeyError: ``path`` does not identify a declared table.
        """
        table = self._paths.get(self.canonical_path(path))
        if not isinstance(table, Table):
            raise KeyError(f"no table {path!r} in the target contract")
        return table.names()

    def declares(self, path: str, *, table: Optional[bool] = None) -> bool:
        """Check whether a dotted path is declared by the target contract.

        :param path: Dotted field or table path.
        :param table: Optional expected table shape; false requires a field.
        :returns: Whether the path and optional shape exist in this contract.
        """
        item = self._paths.get(self.canonical_path(path))
        return item is not None and (table is None or isinstance(item, Table) == table)

    def role(self, path: str) -> Optional[Role]:
        """Read the role assigned to a target field.

        :param path: Dotted field or table path.
        :returns: Field role, or ``None`` for a table or undeclared path.
        """
        item = self._paths.get(self.canonical_path(path))
        return item.role if isinstance(item, Field) else None

    def paths(self) -> tuple[str, ...]:
        """List all declared dotted paths in declaration order.

        :returns: Tuple containing authority tables, nested tables, and fields.
        """
        return tuple(self._paths)

    def value_keys(self) -> tuple[str, ...]:
        """Identify declared value keys that should be read from live systems.

        :returns: Dotted paths whose role is ``Role.VALUE``.
        """
        return tuple(path for path in self._paths if self.role(path) is Role.VALUE)

    def unpaired(self) -> tuple[str, ...]:
        """Find authorities missing either endpoint or access declarations.

        :returns: Names of top-level authorities without both roles.
        """
        missing: MutableSequence[str] = []
        for authority in self._authorities:
            roles = {item.role for item in authority.fields}
            if not {Role.ENDPOINT, Role.ACCESS} <= roles:
                missing.append(authority.name)
        return tuple(missing)


TARGET_CONTRACT = TargetContract(
    (
        Table(
            GITHUB_AUTHORITY,
            Field.of(Role.ENDPOINT, "host", "repository")
            + Field.of(Role.ACCESS, "token_file")
            # The selected target declares which checks branch protection
            # must require; publication setup verifies their live read-back.
            + Field.of(Role.VALUE, "required_checks"),
        ),
        Table(
            GITLAB_AUTHORITY,
            Field.of(Role.ENDPOINT, "url", "project", "group")
            + Field.of(Role.ACCESS, "token_file")
            # Readable from the runners API.
            + Field.of(Role.VALUE, "runner_id"),
            # Legacy [gitlab.NAME] profiles, read for one release (TD-11).
            named_profiles=True,
            instances=tuple(GITLAB_SELECTORS.values()),
        ),
        Table(
            HARBOR_AUTHORITY,
            Field.of(Role.ENDPOINT, "url", "project")
            + Field.of(
                Role.ACCESS, "credential_file", "username_file", "password_file"
            ),
        ),
        Table(
            KUBERNETES_AUTHORITY,
            Field.of(Role.ENDPOINT, "context", "server")
            + Field.of(Role.ACCESS, "kubeconfig", "kubeconfigs"),
            (
                # Node, Pod and mount values readable from the cluster.
                Table(
                    "node_diagnostics",
                    Field.of(Role.VALUE, "node"),
                    (
                        Table(
                            "cilium",
                            Field.of(Role.VALUE, "namespace", "selector", "container"),
                        ),
                        Table(
                            "journal",
                            Field.of(
                                Role.VALUE,
                                "namespace",
                                "selector",
                                "container",
                                "directory",
                                "host_path",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    ),
    # Each row value names a table of the routed authority, so it selects an
    # endpoint; it is never a value read from a live system.
    Table(
        GITLAB_ROUTING_TABLE,
        Field.of(Role.ENDPOINT, GITLAB_ROUTING_DEFAULT_KEY),
        tuple(
            Table(name, Field.of(Role.ENDPOINT, *GITLAB_ROUTED_AUTHORITIES))
            for name in GITLAB_SELECTORS.values()
        ),
    ),
)
