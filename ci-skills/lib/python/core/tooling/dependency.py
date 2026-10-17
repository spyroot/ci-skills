"""Model tool dependencies shared by toolbox builds and prepared execution Pods.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Optional

from lib.python.core.json_types import (
    JSONObject,
    json_object,
    json_string,
    json_strings,
)
from lib.python.core.tooling.fields import SpecificationFields
from lib.python.core.tooling.linux import LinuxInstall


@dataclass(frozen=True)
class Dependency:
    """One dependency with declared command probes and installation fallback.

    :param name: Dependency name from the specification.
    :param commands: Executables used to detect whether it is installed.
    :param apt: Packages selected for an apt-based base image.
    :param yum: Packages selected for a yum-based base image.
    :param linux: Optional explicit Linux installation route.
    :param probe: Declared argv for version or capability read-back; empty means absent.

    .. attribute :: name
        :type: str

        Dependency name from the specification.

    .. attribute :: commands
        :type: tuple[str, ...]

        Executables used to detect whether it is installed.

    .. attribute :: apt
        :type: tuple[str, ...]

        Packages selected for an apt-based base image.

    .. attribute :: yum
        :type: tuple[str, ...]

        Packages selected for a yum-based base image.

    .. attribute :: linux
        :type: Optional[LinuxInstall]

        Optional explicit Linux installation route.

    .. attribute :: probe
        :type: tuple[str, ...]

        Declared version or capability argv; empty when absent.
    """

    name: str
    commands: tuple[str, ...]
    apt: tuple[str, ...]
    yum: tuple[str, ...]
    linux: Optional[LinuxInstall]
    probe: tuple[str, ...] = ()

    @classmethod
    def from_document(cls, value: JSONObject) -> Dependency:
        """Create a dependency from a schema-validated object.

        :param value: Validated dependency object.
        :returns: Typed dependency.
        :raises SpecificationError: A required field is missing or has an invalid shape.
        """  # noqa: DOC502 - field and installation validation errors propagate
        fields = SpecificationFields(value, "spec/dependencies")
        linux = value.get("linux")
        return cls(
            name=fields.read("name", json_string),
            commands=tuple(fields.read("commands", json_strings)),
            apt=tuple(fields.optional_strings("apt")),
            yum=tuple(fields.optional_strings("yum")),
            probe=tuple(fields.optional_strings("probe")),
            linux=(
                LinuxInstall.from_document(fields.read("linux", json_object))
                if linux is not None
                else None
            ),
        )

    def presence_operation(self) -> str:
        """Render read-only executable-presence checks for this dependency.

        :returns: Shell condition succeeding only when every declared command exists.
        """
        return " && ".join(
            f"command -v {shlex.quote(command)} >/dev/null 2>&1"
            for command in self.commands
        )
