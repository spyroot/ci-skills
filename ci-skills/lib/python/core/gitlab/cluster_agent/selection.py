"""Select exact Agent install names without changing native project identity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from lib.python.core.gitlab.cluster_agent.provider import AgentProject
from lib.python.core.kubernetes.run_scope import (
    DNS_LABEL_LIMIT,
    RunScope,
    dns_token,
    validate_namespace,
)
from lib.python.core.runner_identity import (
    NameSource,
    RepositoryIdentity,
    RepositorySource,
    resolve_name,
)

CHART_VERSION: Final = "2.20.1"
CHART_ARCHIVE: Final = (
    f"https://gitlab-charts.s3.amazonaws.com/gitlab-agent-{CHART_VERSION}.tgz"
)
CHART_SHA256: Final = "4d2214c73856533f27cefd2de673c7e0abbb7925352c0cb157458287eb591210"
APP_VERSION: Final = "v18.5.2"
IMAGE_TAG: Final = (
    "registry.gitlab.com/gitlab-org/cluster-integration/gitlab-agent/agentk:"
    f"{APP_VERSION}"
)
_RELEASE_NAME_LIMIT: Final = 53
_AGENT_ROLE: Final = "agent"


def release_name(scope: RunScope, agent_name: str) -> str:
    """Derive the same exact Helm release for connect and every delete repeat.

    :param scope: Run identity shared by both operations.
    :param agent_name: Exact GitLab Agent name already selected.
    :returns: Helm-compatible name carrying the full run uniqueness suffix.
    """
    return scope.bounded_name(_AGENT_ROLE, limit=_RELEASE_NAME_LIMIT, prefix=agent_name)


@dataclass(frozen=True, slots=True)
class ConnectSelection:
    """Keep project identity separate from caller-selected object names.

    .. attribute :: project
        :type: AgentProject

        Exact native project selected by the bound GitLab service.

    .. attribute :: agent_name
        :type: str

        Exact provider registration name.

    .. attribute :: scope
        :type: RunScope

        Run and optional borrowed Kubernetes namespace.

    .. attribute :: release
        :type: str

        Stable Helm release name derived from the Agent name and run.
    """

    project: AgentProject
    agent_name: str
    scope: RunScope
    release: str


def select_connect(
    project: AgentProject,
    *,
    project_name: str | None,
    agent_name: str | None,
    namespace: str | None,
    run_id: str | None,
) -> ConnectSelection:
    """Resolve names from explicit selections, override, or actual repository.

    :param project: Native project read back from the verified GitLab authority.
    :param project_name: Optional consumer naming base, distinct from provider path.
    :param agent_name: Explicit registration name, preserved exactly if present.
    :param namespace: Explicit existing namespace, preserved by the shared scope.
    :param run_id: Explicit run identity or absence while obtaining a live plan.
    :returns: One immutable selection for installation and confirmation.
    :raises ValueError: A name or run ID cannot be represented by the native providers.
    """  # noqa: DOC502 - Shared naming and scope validators can reject input.
    repository = RepositoryIdentity(project.path, RepositorySource.PROVIDER)
    registration = resolve_name(
        agent_name, repository, argument="--agent-name", project_name=project_name
    )
    consumer = resolve_name(
        None, repository, argument="--project-name", project_name=project_name
    )
    selected_agent = (
        validate_namespace(registration.value)
        if registration.source == NameSource.EXPLICIT
        else dns_token(registration.value, limit=DNS_LABEL_LIMIT)
    )
    scope = RunScope.new(
        namespace=namespace, run_id=run_id, namespace_prefix=consumer.value
    )
    return ConnectSelection(
        project, selected_agent, scope, release_name(scope, selected_agent)
    )
