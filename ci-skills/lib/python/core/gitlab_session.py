"""
Bound GitLab operation identity without reportable credential values.
Mustafa Bayramov mbayramo@cisco.com spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .gitlab_api import GlabAPIClient


@dataclass(frozen=True)
class BoundGitLabSession:
    """One exact host, target, and credential for access and later operations.

    ``environment`` is only for a child API process. Reports must select the
    public fields explicitly; serializing this object would expose a token.
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
    skill: dict[str, Any]
    environment: Mapping[str, str | None] = field(repr=False, compare=False)


@dataclass(frozen=True)
class GitLabService:
    """Existing client and verified session shared by GitLab callbacks."""

    api: GlabAPIClient = field(repr=False)
    session: BoundGitLabSession = field(repr=False)
    target_id: int
    access: dict[str, Any] = field(default_factory=dict)
