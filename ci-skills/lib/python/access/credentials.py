"""Bind the credential sources selected by a diagnostic command.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import socket
from dataclasses import replace
from typing import Optional

from lib.python.access.kubernetes_credentials import (
    _kubernetes_source,
    kubeconfig_digests,
)
from lib.python.access.sources import (
    Sources,
    _token_source,
    resolve_skill_identity,
)
from lib.python.core.catalog import (
    GITHUB_TOKEN_VARIABLES,
    GITLAB_VARIABLES,
    github_variables,
)
from lib.python.core.target import (
    Target,
    TargetError,
)


def bind_sources(target: Target, *, revision: Optional[str] = None) -> Target:
    """Freeze active provider credentials and skill identity for an invocation.

    :param target: Runtime target declaring the surfaces required by the command.
    :param revision: Optional claimed revision of the installed skill.
    :returns: Copy of the target containing selected sources, kubeconfig digests,
        execution host, and skill provenance.
    :raises TargetError: If an active surface, selected credential, kubeconfig,
        or installed skill identity cannot be resolved.
    """
    github = None
    if "github" in target.active_surfaces:
        if target.github is None:
            raise TargetError("github_target_missing")
        github = _token_source(
            target.github.token_file,
            github_variables(target.github.host),
            f"gh-credential-store:{target.github.host}",
            clear_names=GITHUB_TOKEN_VARIABLES,
        )
    gitlab = None
    if "gitlab" in target.active_surfaces:
        if target.gitlab is None:
            raise TargetError("gitlab_target_missing")
        gitlab = _token_source(
            target.gitlab.token_file,
            GITLAB_VARIABLES,
            f"glab-credential-store:{target.gitlab.host}",
        )
    kube, files = (
        _kubernetes_source(target)
        if "kubernetes" in target.active_surfaces
        else (None, ())
    )
    sources = Sources(
        github,
        gitlab,
        kube,
        files,
        socket.getfqdn(),
        kubeconfig_digests(files),
    )
    identity = resolve_skill_identity(revision)
    return replace(
        target,
        sources=sources,
        skill=identity,
        tested_revision=identity["revision"]["value"],
    )
