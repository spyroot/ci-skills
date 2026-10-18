"""Preserve caller-owned OpenShift node input without interpreting its schema.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Optional

from lib.python.core.json_types import JSONObject

NODES_CONFIG_NAME: Final = "nodes-config.yaml"
NETWORK_CONFIG_NAME: Final = "network-config.yaml"


@dataclass(frozen=True)
class NativeNodeFile:
    """Hold an unchanged native input and its container-side filename.

    :param name: Filename selected by the native command's input convention.
    :param content: Exact caller bytes; never included in report output.

    .. attribute :: name
        :type: str

        Native container filename.

    .. attribute :: content
        :type: bytes

        Unchanged caller bytes, excluded from representation and reports.
    """

    name: str
    content: bytes = field(repr=False)

    def evidence(self) -> JSONObject:
        """Identify the supplied bytes without copying their contents to reports.

        :returns: Filename, byte count and SHA-256 of the original input.
        """
        return {
            "name": self.name,
            "size": len(self.content),
            "sha256": hashlib.sha256(self.content).hexdigest(),
        }


@dataclass(frozen=True)
class NativeNodeInput:
    """Describe exactly one native YAML or MAC-flag invocation.

    :param mac_address: Unmodified native flag value, empty for the YAML route.
    :param files: Exact native files to stage in the cluster asset directory.

    .. attribute :: mac_address
        :type: str

        Exact native flag value, empty when using the file route.

    .. attribute :: files
        :type: tuple[NativeNodeFile, ...]

        Native files retained in staging order.
    """

    mac_address: str
    files: tuple[NativeNodeFile, ...]

    @classmethod
    def read(
        cls,
        *,
        spec: Optional[Path] = None,
        mac_address: Optional[str] = None,
        network_config: Optional[Path] = None,
    ) -> NativeNodeInput:
        """Check invocation shape and read selected files without parsing YAML.

        :param spec: Optional native nodes-config.yaml input file.
        :param mac_address: Optional caller-selected MAC flag value.
        :param network_config: Optional native network file for the MAC route.
        :returns: Exact selected values and bytes ready for confirmation binding.
        :raises ValueError: Both input routes, neither route, or YAML plus a
            separate network file were supplied.
        :raises OSError: A selected native file cannot be read.
        """  # noqa: DOC503 - Path.read_bytes propagates file access errors.
        if spec is not None:
            if mac_address is not None or network_config is not None:
                raise ValueError("node_input_choose_spec_or_mac_address")
            return cls("", (NativeNodeFile(NODES_CONFIG_NAME, spec.read_bytes()),))
        if not mac_address:
            raise ValueError("node_input_requires_spec_or_mac_address")
        files = (
            (NativeNodeFile(NETWORK_CONFIG_NAME, network_config.read_bytes()),)
            if network_config is not None
            else ()
        )
        return cls(mac_address, files)

    def arguments(self) -> tuple[str, ...]:
        """Build native argument tokens while preserving the caller's MAC value.

        :returns: MAC and optional network-file flags; YAML is discovered by oc
            from nodes-config.yaml in the selected asset directory. Native file
            arguments are relative to that directory, as required by oc.
        """
        if not self.mac_address:
            return ()
        arguments = ("--mac-address", self.mac_address)
        if self.files:
            arguments += (
                "--network-config-path",
                NETWORK_CONFIG_NAME,
            )
        return arguments

    def evidence(self) -> JSONObject:
        """Return the values that bind plan confirmation to native input bytes.

        :returns: Input route, MAC flag and native file identities.
        """
        return {
            "route": "flags" if self.mac_address else "file",
            "mac_address": self.mac_address,
            "files": [item.evidence() for item in self.files],
        }
