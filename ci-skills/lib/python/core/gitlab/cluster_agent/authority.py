"""Bind and independently read the two authorities selected for a GitLab Agent."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import ceil

from lib.python.access.credentials import bind_sources
from lib.python.access.gitlab import check_gitlab_operation_access
from lib.python.access.gitlab_credentials import bind_gitlab_session
from lib.python.core.cli_api import APIError, RequestDeadline
from lib.python.core.endpoints import GITLAB_AUTHORITY, KUBERNETES_AUTHORITY
from lib.python.core.gitlab.cluster_agent.provider import (
    AgentProject,
    AgentProvider,
    KASMetadata,
)
from lib.python.core.gitlab.gitlab_api import GlabAPIClient
from lib.python.core.gitlab.gitlab_session import GitLabService
from lib.python.core.json_types import json_integer, json_object
from lib.python.core.kubernetes.gitlab_agent import read_agent_cluster_uid
from lib.python.core.kubernetes.kubernetes import KubernetesService
from lib.python.core.project_binding import resolve_target
from lib.python.core.target import (
    GitLabOperationTarget,
    GitLabSelection,
    Target,
    TargetError,
    select_gitlab_reference,
)
from lib.python.sys.status import PASS


@dataclass(frozen=True, slots=True)
class AgentTarget:
    """Keep the configured pair of authorities and selected project together.

    .. attribute :: target
        :type: Target

        Runtime target with its resolved GitLab-to-Kubernetes route.

    .. attribute :: project
        :type: str

        Explicit or configured GitLab project path or numeric ID.
    """

    target: Target
    project: str


def reported_target_source(target: Target) -> str:
    """Report how the target was selected, without its file path.

    The gitlab_mirror.py command reports the same value: the source kind, such as
    ``env:CI_SKILLS_TARGET`` or ``user``, with ``;argv:--gitlab=...`` appended
    when the caller passed a GitLab selector.

    :param target: Resolved target.
    :returns: Source kind and caller selection for the result ``target_source``.
    :raises TargetError: Source resolution has not classified the target.
    """
    if target.source_kind is None:
        raise TargetError("agent_target_unresolved")
    return target.selection_source(target.source_kind)


def resolve_agent_target(
    target_path: str | None,
    binding: str | None,
    project: str | None,
    gitlab_selection: GitLabSelection | None,
    *,
    dry_run: bool,
) -> AgentTarget:
    """Use the shared route and require an exact GitLab project selection.

    :param target_path: Internal target-file override or normal resolution.
    :param binding: Existing project-binding selector.
    :param project: Explicit project reference or configured default.
    :param gitlab_selection: Existing GitLab instance or profile selector.
    :param dry_run: Suppress executable kubeconfig sources during offline resolution.
    :returns: Selected project reference and routed authorities.
    :raises TargetError: Source resolution fails or the selection is group-only.
    """
    target = resolve_target(
        target_path,
        binding,
        dry_run=dry_run,
        required_surfaces=(GITLAB_AUTHORITY, KUBERNETES_AUTHORITY),
        gitlab_selection=gitlab_selection,
    )
    gitlab = target.require_gitlab()
    if target.source_file is None or target.target_reference is None:
        raise TargetError("agent_target_unresolved")
    kind, reference = select_gitlab_reference(
        GitLabOperationTarget(gitlab, target.source_file), project=project
    )
    if kind != "project":
        raise TargetError("agent_project_required")
    return AgentTarget(target, reference)


@dataclass(frozen=True, slots=True)
class AgentAuthorities:
    """Carry the verified handles and observations for one live invocation.

    .. attribute :: gitlab
        :type: GitLabService

        Access-verified GitLab service using the selected project ID.

    .. attribute :: provider
        :type: AgentProvider

        Agent operations bound to that same service.

    .. attribute :: kubernetes
        :type: KubernetesService

        Credential-bound service after context and identity read-back.

    .. attribute :: project
        :type: AgentProject

        Canonical path and numeric ID read from GitLab.

    .. attribute :: kas
        :type: KASMetadata

        GitLab-advertised KAS address and proxy metadata.

    .. attribute :: cluster_uid
        :type: str

        Independent native identifier of the selected cluster.

    """

    gitlab: GitLabService = field(repr=False)
    provider: AgentProvider = field(repr=False)
    kubernetes: KubernetesService = field(repr=False)
    project: AgentProject
    kas: KASMetadata
    cluster_uid: str


def observe_authorities(
    selected: AgentTarget, *, revision: str | None, deadline: RequestDeadline
) -> AgentAuthorities:
    """Read GitLab project/KAS and Kubernetes identities without writes.

    :param selected: Routed target whose configured project has been selected.
    :param revision: Optional caller claim for installed skill provenance.
    :param deadline: Shared absolute deadline for provider requests.
    :returns: Verified bindings and native identities for planning or read-back.
    :raises TargetError: Required source or installed provenance is unresolved.
    :raises APIError: A provider read fails or the project changes during binding.
    """  # noqa: DOC503 - Shared credentials, reads and deadlines retain native errors.
    target = selected.target
    if target.source_file is None or target.target_reference is None:
        raise TargetError("agent_target_unresolved")
    session = deadline.call(
        bind_gitlab_session,
        GitLabOperationTarget(target.require_gitlab(), target.source_file),
        target_kind="project",
        target_reference=selected.project,
        target_source=reported_target_source(target),
        revision=revision,
    )
    if session.skill["revision"]["verified"] is not True:
        raise TargetError("agent_candidate_unverified")
    client = GlabAPIClient(timeout=ceil(deadline.remaining()), deadline=deadline)
    access = deadline.call(check_gitlab_operation_access, session, api_client=client)
    if access.get("status") != PASS:
        raise APIError("agent_project_access_unverified")
    identity = json_object(access["target"])
    gitlab = GitLabService(client, session, json_integer(identity["id"]), access)
    provider = AgentProvider(gitlab)
    project = deadline.call(provider.project)
    if project.path != identity["full_path"]:
        raise APIError("agent_project_identity_mismatch")
    kas = deadline.call(provider.kas)
    # GitLab uses the bound session above. Bind only the separate kubeconfig
    # authority here so the GitLab credential is resolved once per invocation.
    bound = deadline.call(
        bind_sources,
        replace(target, active_surfaces=(KUBERNETES_AUTHORITY,)),
        revision=revision,
    )
    if bound.skill != session.skill:
        raise TargetError("agent_candidate_changed")
    kubernetes = KubernetesService(bound, ceil(deadline.remaining()))
    deadline.call(kubernetes.verify)
    kubernetes.timeout = ceil(deadline.remaining())
    cluster_uid = deadline.call(read_agent_cluster_uid, kubernetes)
    return AgentAuthorities(gitlab, provider, kubernetes, project, kas, cluster_uid)
