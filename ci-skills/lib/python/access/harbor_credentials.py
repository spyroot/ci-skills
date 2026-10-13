"""Read Harbor credentials in their declared native format.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from typing import Final

from lib.python.access.sources import (
    read_token_file,
)
from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import json_object, json_string
from lib.python.core.target import (
    HarborTarget,
    TargetError,
)

MAX_REGISTRY_CREDENTIAL_BYTES: Final = 64 * 1024


@dataclass(frozen=True)
class RegistryCredential:
    """Registry identity and secret retained only in memory.

    :param username: Selected registry username or robot-account name.
    :param password: Selected secret, excluded from representation and comparison.
    :param source: Reportable credential-file reference.

    .. attribute :: username
        :type: str

        Selected registry username or robot-account name.

    .. attribute :: password
        :type: str

        Selected secret, excluded from representation and comparison.

    .. attribute :: source
        :type: str

        Reportable credential-file reference.
    """

    username: str
    password: str = field(repr=False, compare=False)
    source: str

    def dockerconfig(self, host: str) -> bytes:
        """Encode native registry credentials for a private temporary file.

        :param host: Exact configured registry host.
        :returns: Docker config bytes; callers must never emit these bytes.
        """
        auth = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return json.dumps({"auths": {host: {"auth": auth}}}).encode()


def resolve_harbor_credential(target: HarborTarget) -> RegistryCredential:
    """Read the selected Harbor files without rewriting their stored format.

    :param target: Endpoint and target-relative file selection.
    :returns: Registry identity, secret, and reportable source.
    :raises TargetError: If the selected files are unavailable or malformed.
    """
    if target.credential_file is None:
        if target.username_file is None or target.password_file is None:
            raise TargetError("harbor_credential_selection_invalid")
        return RegistryCredential(
            read_token_file(target.username_file),
            read_token_file(target.password_file),
            f"file:{target.username_file} -> file:{target.password_file}",
        )
    path = target.credential_file
    try:
        if not path.is_file() or path.stat().st_size > MAX_REGISTRY_CREDENTIAL_BYTES:
            raise TargetError("credential_file_unavailable")
        raw = path.read_text(encoding="utf-8").strip()
        if raw.startswith("{"):
            value = json_object(response_decoder().decode(raw))
            if "auths" in value:
                entries = json_object(value["auths"])
                selected = entries.get(target.host, entries.get(target.url))
                if not isinstance(selected, dict):
                    raise TargetError("registry_credential_host_missing")
                if "auth" in selected:
                    raw = base64.b64decode(
                        json_string(selected["auth"]), validate=True
                    ).decode()
                else:
                    raw = f"{selected['username']}:{selected['password']}"
            else:
                raw = f"{value['username']}:{value['password']}"
        username, separator, password = raw.partition(":")
        if (
            not separator
            or not username
            or not password
            or any(char.isspace() for char in raw)
        ):
            raise TargetError("credential_file_invalid")
    except (
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
        TypeError,
        binascii.Error,
    ) as exc:
        raise TargetError("credential_file_invalid") from exc
    return RegistryCredential(username, password, f"file:{path}")
