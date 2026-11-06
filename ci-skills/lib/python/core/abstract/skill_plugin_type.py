"""Stable plugin identities shared by callbacks and invocation state.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from enum import StrEnum

from lib.python.core.endpoints import (
    GITHUB_AUTHORITY,
    GITLAB_AUTHORITY,
    HARBOR_AUTHORITY,
    KUBERNETES_AUTHORITY,
)


class SkillPluginType(StrEnum):
    """Identify the plugin that owns a callback's configuration and utilities.

    .. attribute :: GITLAB

        GitLab callbacks and their target credentials.

    .. attribute :: GITHUB

        GitHub callbacks and their target credentials.

    .. attribute :: KUBERNETES

        Kubernetes callbacks and their selected kubeconfig.

    .. attribute :: HARBOR

        Harbor callbacks and their registry credentials.

    .. attribute :: LOCAL

        Local actions whose inputs require no provider configuration.
    """

    GITLAB = GITLAB_AUTHORITY
    GITHUB = GITHUB_AUTHORITY
    KUBERNETES = KUBERNETES_AUTHORITY
    HARBOR = HARBOR_AUTHORITY
    LOCAL = "local"
