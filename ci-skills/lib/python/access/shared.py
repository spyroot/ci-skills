"""Shared access results and decoding.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from os import PathLike
from typing import Optional, Union

from lib.python.access.sources import read_token_file
from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
)
from lib.python.core.runtime import CommandResult, error_class
from lib.python.sys.status import BLOCKED


@dataclass(frozen=True)
class Surface:
    """Observed access state for one selected authority.

    :param name: Authority identifier used in the access report.
    :param status: PASS or BLOCKED result of the access probes.
    :param identity: Authenticated identity, or ``None`` when unverified.
    :param target: Selected authority or resource identifier.
    :param observed_capability: Capabilities verified before the result was returned.
    :param next_step: Suggested recovery action, or ``None`` on success.
    :param reason: Classified failure, or ``None`` on success.
    :param credential_source: Reportable credential origin, when bound.
    :param details: Additional verified access facts, when available.

    .. attribute :: name
        :type: str

        Authority identifier used in the access report.

    .. attribute :: status
        :type: str

        PASS or BLOCKED result of the access probes.

    .. attribute :: identity
        :type: Optional[str]

        Authenticated identity, or ``None`` when unverified.

    .. attribute :: target
        :type: str

        Selected authority or resource identifier.

    .. attribute :: observed_capability
        :type: Sequence[str]

        Capabilities verified before the result was returned.

    .. attribute :: next_step
        :type: Optional[str]

        Suggested recovery action, or ``None`` on success.

    .. attribute :: reason
        :type: Optional[str]

        Classified failure, or ``None`` on success.

    .. attribute :: credential_source
        :type: Optional[str]

        Reportable credential origin, when bound.

    .. attribute :: details
        :type: Optional[JSONObject]

        Additional verified access facts, when available.
    """

    name: str
    status: str
    identity: Optional[str]
    target: str
    observed_capability: Sequence[str]
    next_step: Optional[str]
    reason: Optional[str] = None
    credential_source: Optional[str] = None
    details: Optional[JSONObject] = None

    def as_dict(self) -> JSONObject:
        """Expose the access result as report fields.

        :returns: Mapping of this surface's fields and their current values.
        """
        return {
            "name": self.name,
            "status": self.status,
            "identity": self.identity,
            "target": self.target,
            "observed_capability": self.observed_capability,
            "next_step": self.next_step,
            "reason": self.reason,
            "credential_source": self.credential_source,
            "details": self.details,
        }


def _token_file(
    path: Optional[Union[str, PathLike[str]]], variable: str
) -> Optional[Mapping[str, str]]:
    """Create a child-process credential override when a file is selected.

    :param path: Selected token filename, or ``None`` to leave native CLI
        authentication unchanged.
    :param variable: Environment variable under which the token is supplied.
    :returns: Mapping from the variable name to the token, or ``None`` when
        no credential file is configured.
    :raises TargetError: If the selected file cannot provide a valid token.
    :raises TypeError: If a nonempty selection is not a text path-like value.
    """  # noqa: DOC502 - read_token_file validates and reads the path.
    return None if path is None else {variable: read_token_file(path)}


def _json(result: CommandResult) -> JSONValue:
    """Decode successful native-command output for an access probe.

    :param result: Command exit status, stdout, and stderr.
    :returns: JSON value decoded from stdout.
    :raises ValueError: If the command failed or stdout is not valid JSON.
    """
    if result.returncode:
        raise ValueError(error_class(result))
    try:
        return response_decoder().decode(result.stdout)
    except (ValueError, RecursionError) as exc:
        raise ValueError("invalid_json") from exc


def _blocked(
    name: str,
    target: str,
    observed: Sequence[str],
    reason: str,
    next_step: str,
    credential_source: Optional[str] = None,
) -> Surface:
    """Create a failed access result with the observations already collected.

    :param name: Authority identifier used in the report.
    :param target: Selected authority or resource identifier.
    :param observed: Capabilities verified before the failure.
    :param reason: Classified failure reason.
    :param next_step: Suggested recovery action for the operator.
    :param credential_source: Reportable credential origin, when available.
    :returns: BLOCKED surface result without an authenticated identity.
    """
    return Surface(
        name, BLOCKED, None, target, observed, next_step, reason, credential_source
    )


def access_evidence(gate: JSONObject) -> JSONObject:
    """Extract the access facts attached to a collector report.

    :param gate: Access-gate receipt produced for the current invocation.
    :returns: Mapping of status, identities, selected targets, credential-source
        references, provenance, and a SHA-256 correlator of the supplied receipt.
    :raises TypeError: If the surfaces field or one of its entries is not an object.

    The correlator hashes the in-memory receipt, not a separately redacted file.
    """
    body = json.dumps(gate, sort_keys=True, default=str).encode("utf-8")
    surfaces = gate.get("surfaces") or {}
    if not isinstance(surfaces, Mapping) or any(
        not isinstance(surface, Mapping) for surface in surfaces.values()
    ):
        raise TypeError("invalid_access_surfaces")
    return {
        "profile": gate.get("profile"),
        "status": gate.get("status"),
        "captured_at": gate.get("captured_at"),
        "execution_host": gate.get("execution_host"),
        # Which declared location the target came from. Without it a collector
        # report names the authorities but not what selected them, so a reader
        # cannot tell a project target from the user default -- and the
        # protocol's promise is that every report names its own source.
        "target_file": gate.get("target_file"),
        "target_source": gate.get("target_source"),
        "skill": gate.get("skill"),
        "consuming_project": gate.get("consuming_project"),
        "credential_sources": gate.get("credential_sources"),
        "target_selection": gate.get("target_selection"),
        "targets": gate.get("targets"),
        "identities": {
            name: surface.get("identity")
            for name, surface in surfaces.items()
            if isinstance(surface, Mapping)
        },
        "receipt_sha256": hashlib.sha256(body).hexdigest(),
    }
