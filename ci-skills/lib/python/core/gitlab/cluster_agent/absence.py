"""Prove the logical Agent scope is empty after its recovery record is gone."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Final

from lib.python.core.cli_api import RequestDeadline
from lib.python.core.gitlab.cluster_agent.provider import AgentProject, AgentProvider
from lib.python.core.gitlab.cluster_agent.recovery import agent_recovery_name
from lib.python.core.gitlab.cluster_agent.secret import agent_secret_name
from lib.python.core.gitlab.cluster_agent.selection import (
    ConnectSelection,
    release_name,
)
from lib.python.core.gitlab.gitlab_session import GitLabService
from lib.python.core.helm.native import HelmRelease
from lib.python.core.helm.storage import helm_storage_name
from lib.python.core.kubernetes.gitlab_agent import read_agent_cluster_uid
from lib.python.core.kubernetes.kubernetes import KubernetesError, KubernetesService
from lib.python.core.kubernetes.resources import OPERATION_LABEL
from lib.python.core.kubernetes.run_scope import RUN_ID_LABEL
from lib.python.core.runtime import execution_trace

_CHART_KINDS: Final = (
    "pods",
    "deployments",
    "replicasets",
    "services",
    "serviceaccounts",
    "rolebindings",
    "clusterrolebindings",
)
_RUN_KINDS: Final = ("namespaces", "pods", "configmaps", "secrets", *_CHART_KINDS[1:])
_RUN_PRESENT: Final = "agent_absence_run_markers_present"
_RELEASE_PRESENT: Final = "agent_absence_release_present"
_CHART_PRESENT: Final = "agent_absence_chart_markers_present"


@dataclass(frozen=True, slots=True)
class LogicalAgentAbsence:
    """Current empty logical scope proved without any previous native ID claims.

    .. attribute :: project
        :type: AgentProject

        Native GitLab configuration project that was read.

    .. attribute :: release
        :type: str

        Logical release selector under which no resources were found.

    .. attribute :: cluster_uid
        :type: str

        Independently read UID of the current target cluster.
    """

    project: AgentProject
    release: str
    cluster_uid: str


def _empty(
    kubernetes: KubernetesService,
    deadline: RequestDeadline,
    kinds: str,
    filter_flag: str,
    selector: str,
    present: str,
) -> None:
    # A native error can include the retrieved object even with metadata output.
    with execution_trace(None):
        result = deadline.call(
            kubernetes.command,
            "get",
            kinds,
            "--all-namespaces",
            filter_flag,
            selector,
            "-o",
            "name",
            timeout=deadline.remaining(),
        )
    if result.returncode or result.stderr.strip():
        raise KubernetesError("agent_absence_kubernetes_read_unconfirmed")
    if result.stdout.strip():
        raise KubernetesError(present)


def verify_agent_logical_absence(
    gitlab: GitLabService,
    kubernetes: KubernetesService,
    selection: ConnectSelection,
    *,
    timeout: float,
) -> LogicalAgentAbsence:
    """Verify a repeat delete's exact logical selector without any mutation.

    :param gitlab: Credential-bound GitLab service for the selected project.
    :param kubernetes: Credential-bound Kubernetes service for the selected cluster.
    :param selection: Exact caller project, Agent, run and derived release.
    :param timeout: Maximum remaining verification time, in seconds.
    :returns: Current project, logical release, and cluster identity.
    :raises KubernetesError: The logical scope still exists or cannot be verified.
    :raises GitLabAPIError: The selected native GitLab scope cannot be read.
    :raises HelmError: The release does not have a valid native storage name.
    :raises APIError: The shared operation deadline expires.
    """  # noqa: DOC503 - Native reads, storage naming and the deadline propagate errors.
    if not math.isfinite(timeout) or timeout <= 0:
        raise KubernetesError("agent_absence_timeout")
    if selection.release != release_name(selection.scope, selection.agent_name):
        raise KubernetesError("agent_absence_release_selector_mismatch")
    deadline = RequestDeadline(time.monotonic() + timeout, time.monotonic)
    if gitlab.target_id != selection.project.id:
        raise KubernetesError("agent_absence_project_mismatch")
    provider = AgentProvider(gitlab)
    project = deadline.call(provider.project)
    if project != selection.project:
        raise KubernetesError("agent_absence_project_mismatch")
    if deadline.call(provider.find_agent, selection.agent_name) is not None:
        raise KubernetesError("agent_absence_registration_present")

    release = selection.release
    storage = helm_storage_name(
        HelmRelease(release, selection.scope.namespace, 1, "deployed")
    )
    run_kinds = ",".join(_RUN_KINDS)
    run_id = selection.scope.run_id
    probes = (
        (run_kinds, "-l", f"{RUN_ID_LABEL}={run_id}", _RUN_PRESENT),
        (run_kinds, "-l", f"{OPERATION_LABEL}={run_id}", _RUN_PRESENT),
        ("secrets", "-l", f"owner=helm,name={release}", _RELEASE_PRESENT),
        ("secrets", "--field-selector", f"metadata.name={storage}", _RELEASE_PRESENT),
        (
            "secrets",
            "--field-selector",
            f"metadata.name={agent_secret_name(selection)}",
            _RUN_PRESENT,
        ),
        (
            "configmaps",
            "--field-selector",
            f"metadata.name={agent_recovery_name(selection)}",
            _RUN_PRESENT,
        ),
        (
            ",".join(_CHART_KINDS),
            "-l",
            f"app.kubernetes.io/instance={release}",
            _CHART_PRESENT,
        ),
    )
    for kinds, flag, selector, present in probes:
        _empty(kubernetes, deadline, kinds, flag, selector, present)
    cluster_uid = deadline.call(read_agent_cluster_uid, kubernetes)
    return LogicalAgentAbsence(project, release, cluster_uid)
