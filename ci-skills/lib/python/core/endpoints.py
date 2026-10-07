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
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    """What one target key is for."""

    # Which host, project or cluster ci-skills points at.
    ENDPOINT = "endpoint"
    # Where the credential for that endpoint lives: a file path, never a value.
    ACCESS = "access"
    # A value a command could read from the live system. Accepted only because a
    # current command still reads it; the gate refuses any new one.
    VALUE = "value"


@dataclass(frozen=True)
class Field:
    name: str
    role: Role

    @classmethod
    def of(cls, role: Role, *names: str) -> tuple[Field, ...]:
        """Several keys that share one role, in declaration order."""
        return tuple(cls(name, role) for name in names)


@dataclass(frozen=True)
class Table:
    """One TOML table of the target format and the tables nested in it."""

    name: str
    fields: tuple[Field, ...] = ()
    tables: tuple[Table, ...] = ()

    def names(self) -> frozenset[str]:
        """The field and nested-table names this table accepts."""
        return frozenset(item.name for item in self.fields) | frozenset(
            table.name for table in self.tables
        )

    def walk(self, prefix: str = "") -> Iterator[tuple[str, Table | Field]]:
        """Yield every dotted path under this table, the table itself first."""
        path = f"{prefix}{self.name}"
        yield path, self
        for item in self.fields:
            yield f"{path}.{item.name}", item
        for table in self.tables:
            yield from table.walk(f"{path}.")


class TargetContract:
    """The allowed tables and keys of a target file, by dotted path."""

    def __init__(self, authorities: tuple[Table, ...]) -> None:
        self._authorities = authorities
        self._paths: dict[str, Table | Field] = {}
        for authority in authorities:
            self._paths.update(authority.walk())

    @property
    def authorities(self) -> tuple[str, ...]:
        return tuple(table.name for table in self._authorities)

    def names(self, path: str) -> frozenset[str]:
        """What the table at `path` accepts; `core/target.py` rejects the rest."""
        table = self._paths.get(path)
        if not isinstance(table, Table):
            raise KeyError(f"no table {path!r} in the target contract")
        return table.names()

    def declares(self, path: str) -> bool:
        return path in self._paths

    def role(self, path: str) -> Role | None:
        """The role of a declared key; None for a table or an undeclared path."""
        item = self._paths.get(path)
        return item.role if isinstance(item, Field) else None

    def paths(self) -> tuple[str, ...]:
        return tuple(self._paths)

    def value_keys(self) -> tuple[str, ...]:
        return tuple(path for path in self._paths if self.role(path) is Role.VALUE)

    def unpaired(self) -> tuple[str, ...]:
        """Authorities that do not declare both an endpoint and its access."""
        missing: list[str] = []
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
            # The checks branch protection must require; also declared by
            # `tests/acceptance/expected.toml`.
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
