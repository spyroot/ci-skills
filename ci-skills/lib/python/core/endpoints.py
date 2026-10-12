"""Declare what a target file may hold.

A target file answers, per authority, which endpoint ci-skills points at (a
host, a project, a cluster) and where the access to it lives (a token file or a
kubeconfig). `~/.ci-skills/target.toml` is the default; a project's
`.ci-skills/target.toml` (selected by `catalog.TARGET_PROTOCOL`) overrides the
same keys with project-specific values. Everything else a command needs --
pods, nodes, namespaces, pipelines, milestones, merge requests -- it reads from
the live system at run time.

`TARGET_CONTRACT` is the one declaration of the allowed keys. `core/target.py`
accepts exactly these and `catalog.AUTHORITIES` is derived from it. The
repository gate that checks the tracked documents against it,
`gates/gate-ci-skills-endpoints.py`, lives outside the skill
(`tools/skillkit/endpoint_gate.py`). Adding an authority (a registry, say) is one
`Table` here, with its endpoint and its access.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping, MutableSequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Union


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

    .. attribute :: name
        :type: str

        Table segment in a dotted target path.

    .. attribute :: fields
        :type: tuple[Field, ...]

        Keys accepted directly under the table.

    .. attribute :: tables
        :type: tuple[Table, ...]

        Nested tables accepted directly under the table.
    """

    name: str
    fields: tuple[Field, ...] = ()
    tables: tuple[Table, ...] = ()

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

    def __init__(self, authorities: tuple[Table, ...]):
        """Index target authority declarations for path and role lookup.

        :param authorities: Top-level authority tables allowed in a target file.
        """
        self._authorities = authorities
        self._paths: MutableMapping[str, Union[Table, Field]] = {}
        for authority in authorities:
            self._paths.update(authority.walk())

    @property
    def authorities(self) -> tuple[str, ...]:
        """Return declared top-level authority names in definition order.

        :returns: Tuple of authority table names.
        """
        return tuple(table.name for table in self._authorities)

    def names(self, path: str) -> frozenset[str]:
        """Return direct keys accepted beneath a declared target table.

        :param path: Dotted path of the table to inspect.
        :returns: Field and immediate nested-table names.
        :raises KeyError: ``path`` does not identify a declared table.
        """
        table = self._paths.get(path)
        if not isinstance(table, Table):
            raise KeyError(f"no table {path!r} in the target contract")
        return table.names()

    def declares(self, path: str) -> bool:
        """Check whether a dotted path is declared by the target contract.

        :param path: Dotted field or table path.
        :returns: Whether the path exists in this contract.
        """
        return path in self._paths

    def role(self, path: str) -> Optional[Role]:
        """Read the role assigned to a target field.

        :param path: Dotted field or table path.
        :returns: Field role, or ``None`` for a table or undeclared path.
        """
        item = self._paths.get(path)
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
            "github",
            Field.of(Role.ENDPOINT, "host", "repository")
            + Field.of(Role.ACCESS, "token_file")
            # The selected target declares which checks branch protection
            # must require; publication setup verifies their live read-back.
            + Field.of(Role.VALUE, "required_checks"),
        ),
        Table(
            "gitlab",
            Field.of(Role.ENDPOINT, "url", "project", "group")
            + Field.of(Role.ACCESS, "token_file")
            # Readable from the runners API.
            + Field.of(Role.VALUE, "runner_id"),
        ),
        Table(
            "harbor",
            Field.of(Role.ENDPOINT, "url", "project")
            + Field.of(
                Role.ACCESS, "credential_file", "username_file", "password_file"
            ),
        ),
        Table(
            "kubernetes",
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
    )
)
