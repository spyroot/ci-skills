"""Bound GitLab operation identity without reportable credential values.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lib.python.core.gitlab.gitlab_api import GlabAPIClient
from lib.python.core.json_types import JSONObject
from lib.python.core.provenance import SkillIdentity


@dataclass(frozen=True)
class BoundGitLabSession:
    """Bind one GitLab host, target, and credential to access and actions.

    :param origin: Verified GitLab HTTPS origin for the API client.
    :param host: Network authority derived from the origin.
    :param target_kind: Selected project or group target category.
    :param target_reference: Caller-selected project or group reference.
    :param target_source: Resolution source for the selected target.
    :param target_file: Target TOML file used for this session.
    :param credential_source: Nonsecret name of the selected credential source.
    :param credential_digest: One-way fingerprint for credential continuity.
    :param execution_host: Runtime host identity recorded in receipts.
    :param skill: Installed skill identity bound to the session.
    :param environment: Private child-process environment; never serialize it.

    .. attribute :: origin
        :type: str

        Verified GitLab HTTPS origin for the API client.

    .. attribute :: host
        :type: str

        Network authority derived from the origin.

    .. attribute :: target_kind
        :type: str

        Selected project or group target category.

    .. attribute :: target_reference
        :type: str

        Caller-selected project or group reference.

    .. attribute :: target_source
        :type: str

        Resolution source for the selected target.

    .. attribute :: target_file
        :type: Path

        Target TOML file used for this session.

    .. attribute :: credential_source
        :type: str

        Nonsecret name of the selected credential source.

    .. attribute :: credential_digest
        :type: str

        One-way fingerprint for credential continuity.

    .. attribute :: execution_host
        :type: str

        Runtime host identity recorded in receipts.

    .. attribute :: skill
        :type: SkillIdentity

        Installed skill identity bound to the session.

    .. attribute :: environment
        :type: Mapping[str, Optional[str]]

        Private child-process environment; never serialize it.
    """

    origin: str
    host: str
    target_kind: str
    target_reference: str
    target_source: str
    target_file: Path
    credential_source: str
    credential_digest: str
    execution_host: str
    skill: SkillIdentity
    environment: Mapping[str, Optional[str]] = field(repr=False, compare=False)


@dataclass(frozen=True)
class GitLabService:
    """Share one verified GitLab session and API client across callbacks.

    :param api: Authenticated bounded GitLab API client.
    :param session: Host, target, and credential identity verified for this run.
    :param target_id: Positive API ID read back for the selected target.
    :param access: Access result fields shared with later operation reports.

    .. attribute :: api
        :type: GlabAPIClient

        Authenticated bounded GitLab API client.

    .. attribute :: session
        :type: BoundGitLabSession

        Host, target, and credential identity verified for this run.

    .. attribute :: target_id
        :type: int

        Positive API ID read back for the selected target.

    .. attribute :: access
        :type: JSONObject

        Access result fields shared with later operation reports.
    """

    api: GlabAPIClient = field(repr=False)
    session: BoundGitLabSession = field(repr=False)
    target_id: int
    access: JSONObject = field(default_factory=dict)
