"""Bind GitLab credentials and sessions for a selected target.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Optional

from lib.python.access.sources import (
    _token_source,
    resolve_skill_identity,
)
from lib.python.core.abstract.skill_state import SkillState
from lib.python.core.abstract.spec import load_spec
from lib.python.core.catalog import (
    GITLAB_VARIABLES,
)
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
from lib.python.core.target import (
    GitLabOperationTarget,
    TargetError,
)


@dataclass(frozen=True)
class ResolvedGitLabCredential:
    """GitLab token retained in memory with its reportable origin.

    :param source: Credential source identifier without the token value.
    :param token: Selected secret, excluded from representation and comparison.

    .. attribute :: source
        :type: str

        Credential source identifier without the token value.

    .. attribute :: token
        :type: str

        Selected secret, excluded from representation and comparison.
    """

    source: str
    token: str = field(repr=False, compare=False)


class GitLabAuthentication(ABC):
    """Bind one GitLab target through a selectable credential source."""

    def __init__(self, target: GitLabOperationTarget):
        """Retain the selected GitLab target for this authentication attempt.

        :param target: Validated host and target configuration to bind.
        """
        self._target = target

    @abstractmethod
    def resolve(self) -> ResolvedGitLabCredential:
        """Resolve the selected credential without exposing it in a report.

        :returns: Token and reportable source for the exact target host.
        :raises TargetError: If the selected source is empty or unreadable.
        """

    def bind(
        self,
        *,
        target_kind: str,
        target_reference: str,
        target_source: str,
        revision: Optional[str] = None,
    ) -> BoundGitLabSession:
        """Bind credential, host, target, and skill identity for one operation.

        :param target_kind: Exact GitLab target kind, project or group.
        :param target_reference: Exact project or group reference.
        :param target_source: Reportable source of the selected target.
        :param revision: Explicit skill revision for an installed copy.
        :returns: Session used for access read-back and later API operations.
        :raises TargetError: If target selection or credential binding fails.
        """
        if target_kind not in {"project", "group"} or not target_reference:
            raise TargetError("gitlab_target_missing")
        credential = self.resolve()
        identity = resolve_skill_identity(revision)
        environment: Mapping[str, Optional[str]] = {
            **dict.fromkeys(GITLAB_VARIABLES),
            "CI_JOB_TOKEN": None,
            "GLAB_ENABLE_CI_AUTOLOGIN": None,
            "GITLAB_HOST": self._target.gitlab.host,
            "GLAB_NO_PROMPT": "1",
            "GITLAB_TOKEN": credential.token,
        }
        return BoundGitLabSession(
            origin=self._target.gitlab.url,
            host=self._target.gitlab.host,
            target_kind=target_kind,
            target_reference=target_reference,
            target_source=target_source,
            target_file=self._target.source_file,
            credential_source=credential.source,
            credential_digest=(
                "sha256:" + hashlib.sha256(credential.token.encode("utf-8")).hexdigest()
            ),
            execution_host=socket.getfqdn(),
            skill=identity,
            environment=MappingProxyType(environment),
        )


class ConfiguredGitLabAuthentication(GitLabAuthentication):
    """Resolve the declared file, environment, or exact-host glab store."""

    def resolve(self) -> ResolvedGitLabCredential:
        """Select one credential without falling through after a bad source.

        :returns: Token and source selected by the target configuration.
        :raises TargetError: If the selected token cannot be read or pinned.
        """
        source = _token_source(
            self._target.gitlab.token_file,
            GITLAB_VARIABLES,
            f"glab-credential-store:{self._target.gitlab.host}",
        )
        if source.reference.startswith("glab-credential-store:"):
            token = _glab_store_token(self._target.gitlab.host)
        else:
            selected = [
                value for value in source.environment.values() if value is not None
            ]
            if len(selected) != 1:
                raise TargetError("credential_source_unresolved")
            token = selected[0]
        return ResolvedGitLabCredential(source.reference, token)


def _glab_store_token(host: str) -> str:
    """Read one exact-host glab token without prompting.

    :param host: Provider hostname whose native CLI credential entry is selected.
    :returns: Nonempty stored token, retained only in process memory.
    :raises TargetError: If glab cannot return a valid token within ten seconds.
    """
    environment = os.environ.copy()
    for name in (*GITLAB_VARIABLES, "CI_JOB_TOKEN", "GLAB_ENABLE_CI_AUTOLOGIN"):
        environment.pop(name, None)
    environment["GITLAB_HOST"] = host
    environment["GLAB_NO_PROMPT"] = "1"
    try:
        result = subprocess.run(
            ["glab", "config", "get", "token", "--host", host],
            cwd="/",
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TargetError("credential_store_unpinnable") from exc
    if result.returncode:
        raise TargetError("credential_store_unpinnable")
    value = result.stdout.strip()
    if not value or len(value) > 4096 or any(char.isspace() for char in value):
        raise TargetError("credential_store_unpinnable")
    return value


def bind_gitlab_session(
    target: GitLabOperationTarget,
    *,
    target_kind: str,
    target_reference: str,
    target_source: str,
    revision: Optional[str] = None,
    invocation: SkillState | None = None,
) -> BoundGitLabSession:
    """Bind the selected target through the shared authentication interface.

    :param target: Validated GitLab host and credential configuration.
    :param target_kind: Exact GitLab target kind, project or group.
    :param target_reference: Exact project or group reference.
    :param target_source: Reportable source of the selected target.
    :param revision: Explicit skill revision for an installed copy.
    :param invocation: Existing workflow state; otherwise start a standalone invocation.
    :returns: Bound session for access read-back and API operations.
    :raises TargetError: If target selection or credential binding fails.
    :raises OSError: The required default specification cannot be read.
    :raises ValueError: The required specification is invalid JSON.
    :raises TypeError: The required specification has an invalid object shape.
    """  # noqa: DOC502 - ConfiguredGitLabAuthentication.bind validates inputs.
    state = invocation if invocation is not None else SkillState(spec=load_spec())
    return replace(
        ConfiguredGitLabAuthentication(target).bind(
            target_kind=target_kind,
            target_reference=target_reference,
            target_source=target_source,
            revision=revision,
        ),
        invocation=state,
    )
