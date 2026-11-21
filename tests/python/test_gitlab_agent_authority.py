"""Exercise Agent authority read-back without provider mutation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock

import pytest
from conftest import import_script_module

AUTHORITY = import_script_module("lib.python.core.gitlab.cluster_agent.authority")
PROVIDER = import_script_module("lib.python.core.gitlab.cluster_agent.provider")

PROJECT = "consumer/project"
PROJECT_ID = 41
CLUSTER_UID = "fixture-cluster-uid"
SKILL = MappingProxyType(
    {"revision": MappingProxyType({"verified": True, "value": "a" * 40})}
)
ACCESS = MappingProxyType(
    {
        "status": "PASS",
        "target": MappingProxyType({"id": PROJECT_ID, "full_path": PROJECT}),
    }
)
KAS = PROVIDER.KASMetadata(
    "18.5.2",
    "18.5.2",
    "wss://kas.example.test",
    "https://kas.example.test/k8s-proxy",
)


@pytest.mark.parametrize(
    ("changed", "reason"),
    (
        (None, None),
        ("installed_revision", "agent_candidate_unverified"),
        ("project_access", "agent_project_access_unverified"),
        ("canonical_path", "agent_project_identity_mismatch"),
        ("bound_skill", "agent_candidate_changed"),
    ),
)
def test_observe_agent_authorities(
    target_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed: str | None,
    reason: str | None,
) -> None:
    """Vary one invariant on an isolated copy of the read-only fixture."""
    selected = AUTHORITY.resolve_agent_target(
        str(target_file), None, PROJECT, None, dry_run=True
    )
    skill = {"revision": dict(SKILL["revision"])}
    access = {"status": ACCESS["status"], "target": dict(ACCESS["target"])}
    project = PROVIDER.AgentProject(PROJECT_ID, PROJECT)
    bound_skill = {"revision": dict(SKILL["revision"])}
    if changed == "installed_revision":
        skill["revision"]["verified"] = False
    elif changed == "project_access":
        access["status"] = "BLOCKED"
    elif changed == "canonical_path":
        project = replace(project, path="consumer/replaced")
    elif changed == "bound_skill":
        bound_skill["revision"]["value"] = "b" * 40

    gitlab_session = SimpleNamespace(skill=skill)
    gitlab_bind = Mock(return_value=gitlab_session)
    gitlab_access = Mock(return_value=access)
    gitlab_client = Mock()
    provider = Mock()
    provider.project.return_value = project
    provider.kas.return_value = KAS
    bound = replace(selected.target, skill=bound_skill)
    kube_bind = Mock(return_value=bound)
    kubernetes = Mock()
    kube_service = Mock(return_value=kubernetes)
    cluster_read = Mock(return_value=CLUSTER_UID)
    monkeypatch.setattr(AUTHORITY, "bind_gitlab_session", gitlab_bind)
    monkeypatch.setattr(AUTHORITY, "check_gitlab_operation_access", gitlab_access)
    monkeypatch.setattr(AUTHORITY, "GlabAPIClient", Mock(return_value=gitlab_client))
    monkeypatch.setattr(AUTHORITY, "AgentProvider", Mock(return_value=provider))
    monkeypatch.setattr(AUTHORITY, "bind_sources", kube_bind)
    monkeypatch.setattr(AUTHORITY, "KubernetesService", kube_service)
    monkeypatch.setattr(AUTHORITY, "read_agent_cluster_uid", cluster_read)
    deadline = AUTHORITY.RequestDeadline(30, monotonic=lambda: 0)

    if reason is not None:
        with pytest.raises((AUTHORITY.TargetError, AUTHORITY.APIError), match=reason):
            AUTHORITY.observe_authorities(selected, revision=None, deadline=deadline)
        cluster_read.assert_not_called()
        kube_service.assert_not_called()
        return

    observed = AUTHORITY.observe_authorities(selected, revision=None, deadline=deadline)
    assert observed.project == project
    assert observed.kas == KAS
    assert observed.cluster_uid == CLUSTER_UID
    assert observed.gitlab.session is gitlab_session
    assert observed.gitlab.target_id == PROJECT_ID
    assert observed.kubernetes is kubernetes
    assert gitlab_bind.call_args.kwargs["target_reference"] == PROJECT
    gitlab_access.assert_called_once_with(gitlab_session, api_client=gitlab_client)
    assert kube_bind.call_args.args[0].active_surfaces == (
        AUTHORITY.KUBERNETES_AUTHORITY,
    )
    kubernetes.verify.assert_called_once_with()
    cluster_read.assert_called_once_with(kubernetes)
