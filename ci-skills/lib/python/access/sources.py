"""Shared credential source records and token-file resolution.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Optional, Union

from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.paths import SKILL_ROOT
from lib.python.core.provenance import ProvenanceError, SkillIdentity, skill_identity
from lib.python.core.target import (
    TargetError,
)


@dataclass(frozen=True)
class CredentialSource:
    """Resolved credential source and child-process environment overrides.

    :param reference: Reportable source identifier without credential contents.
    :param environment: Variables to set; ``None`` removes an ambient variable.

    .. attribute :: reference
        :type: str

        Reportable source identifier without credential contents.

    .. attribute :: environment
        :type: Mapping[str, Optional[str]]

        Variables to set; ``None`` removes an ambient variable.
    """

    reference: str
    environment: Mapping[str, Optional[str]] = field(repr=False)


@dataclass(frozen=True)
class Sources:
    """Credential and kubeconfig selections frozen for one invocation.

    :param github: Selected GitHub credential source, or ``None`` if inactive.
    :param gitlab: Selected GitLab credential source, or ``None`` if inactive.
    :param kubernetes: Selected kubeconfig environment, or ``None`` if inactive.
    :param kubeconfig_files: Resolved kubeconfig files in search-path order.
    :param execution_host: Hostname where source selection occurred.
    :param kubeconfig_digests: SHA-256 digests used to detect later file changes.

    .. attribute :: github
        :type: Optional[CredentialSource]

        Selected GitHub credential source, or ``None`` if inactive.

    .. attribute :: gitlab
        :type: Optional[CredentialSource]

        Selected GitLab credential source, or ``None`` if inactive.

    .. attribute :: kubernetes
        :type: Optional[CredentialSource]

        Selected kubeconfig environment, or ``None`` if inactive.

    .. attribute :: kubeconfig_files
        :type: tuple[Path, ...]

        Resolved kubeconfig files in search-path order.

    .. attribute :: execution_host
        :type: str

        Hostname where source selection occurred.

    .. attribute :: kubeconfig_digests
        :type: tuple[str, ...]

        SHA-256 digests used to detect later file changes.
    """

    github: Optional[CredentialSource]
    gitlab: Optional[CredentialSource]
    kubernetes: Optional[CredentialSource]
    kubeconfig_files: tuple[Path, ...]
    execution_host: str
    # Content digests captured at bind time. Pinning the FILENAME is not
    # pinning the target: the gate verifies the server, then every later
    # command reopens the same mutable path and re-resolves the context by
    # name, so a file rewritten in between would redirect the commands with no
    # second comparison.
    kubeconfig_digests: tuple[str, ...] = ()

    def require(self, plugin: SkillPluginType) -> CredentialSource:
        """Select one source already bound for the requested diagnostic plugin.

        :param plugin: Enum identity of the required credential source.
        :returns: Concrete bound source and its private child environment.
        :raises ValueError: No credential source was bound for that plugin.
        """
        selected = {
            SkillPluginType.GITHUB: self.github,
            SkillPluginType.GITLAB: self.gitlab,
            SkillPluginType.KUBERNETES: self.kubernetes,
        }.get(plugin)
        if selected is None:
            raise ValueError("required_credential_source_missing")
        return selected


def read_token_file(path: Union[str, PathLike[str]]) -> str:
    """Read the selected private token file for a child provider client.

    :param path: String or text path-like filename selected by runtime configuration;
        its file must contain UTF-8 text and be at most 4096 bytes.
    :returns: Nonempty token stripped of surrounding whitespace.
    :raises TypeError: If path is not a string or text path-like object.
    :raises TargetError: The file is unreadable, oversized, undecodable, empty,
        or contains whitespace inside the token.
    """  # noqa: DOC503 - Path construction rejects non-path arguments.
    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size > 4096:
            raise TargetError("credential_file_unavailable")
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise TargetError("credential_file_unavailable") from exc
    if not value or any(character.isspace() for character in value):
        raise TargetError("credential_file_invalid")
    return value


@dataclass(frozen=True)
class TokenSelection:
    """The token source a provider call uses, chosen without reading the token.

    ``_token_source`` reads the token from this choice. A dry run reports
    ``reference`` without reading the file or checking the variable's value.

    :param reference: ``file:<absolute path>``, ``env:<NAME>``, or the native
        store identifier.
    :param variable: Variable that receives the token in the child environment,
        or ``None`` when the native store supplies it.

    .. attribute :: reference
        :type: str

        ``file:<absolute path>``, ``env:<NAME>``, or the native store identifier.

    .. attribute :: variable
        :type: Optional[str]

        Variable that receives the token in the child environment, or ``None``
        when the native store supplies it.
    """

    reference: str
    variable: Optional[str]

    @classmethod
    def choose(
        cls, path: Optional[Path], names: Sequence[str], store: str
    ) -> TokenSelection:
        """Choose the token file, else the first set variable, else the store.

        :param path: Token file; ``None`` selects a variable or the native store.
        :param names: Nonempty ordered sequence of accepted credential variable names.
        :param store: Store identifier reported when no file or variable is selected.
        :returns: Reference and receiving variable of the chosen source.
        :raises IndexError: If a token file is selected without any accepted
            variable names.
        """  # noqa: DOC503 - Selecting the first variable can raise IndexError.
        if path is not None:
            return cls(f"file:{path.resolve()}", names[0])
        for name in names:
            if name in os.environ:
                return cls(f"env:{name}", name)
        return cls(store, None)


def _token_source(
    path: Optional[Path],
    names: Sequence[str],
    store: str,
    *,
    clear_names: Optional[Sequence[str]] = None,
) -> CredentialSource:
    """Select a token file, an environment variable, or the native CLI store.

    :param path: Explicit token file; ``None`` enables environment/store selection.
    :param names: Nonempty ordered sequence of accepted credential variable names.
    :param store: Reportable store identifier when no file or variable is selected.
    :param clear_names: Variables to clear from child environments; ``None`` uses
        the accepted credential names. The selected token is always exported.
    :returns: Source reference and environment overrides for the selected token.
    :raises TargetError: If the selected file is invalid or a selected environment
        variable is empty; no fallback is attempted after that failure.
    :raises IndexError: If a token file is selected without any accepted variable names.
    """  # noqa: DOC503 - Selecting the first variable can raise IndexError.
    value = read_token_file(path) if path is not None else None
    selection = TokenSelection.choose(path, names, store)
    if path is None and selection.variable is not None:
        value = os.environ[selection.variable]
        if not value:
            raise TargetError(f"{selection.variable} is empty")
    overrides: MutableMapping[str, Optional[str]] = {
        name: None for name in (clear_names if clear_names is not None else names)
    }
    if selection.variable is not None:
        overrides[selection.variable] = value
    return CredentialSource(selection.reference, overrides)


def resolve_skill_identity(explicit: Optional[str]) -> SkillIdentity:
    """Identify the installed skill bytes and claimed source revision.

    :param explicit: Optional revision claim supplied by the caller.
    :returns: Provenance mapping containing the content digest and revision claim.
    :raises TargetError: If installed provenance cannot be resolved or verified.
    """
    try:
        return skill_identity(SKILL_ROOT, explicit)
    except ProvenanceError as exc:
        raise TargetError(str(exc)) from exc
