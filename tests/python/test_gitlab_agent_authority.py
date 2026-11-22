"""Exercise Agent authority read-back without provider mutation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Final
from unittest.mock import Mock

import pytest
from conftest import import_script_module

AUTHORITY = import_script_module("lib.python.core.gitlab.cluster_agent.authority")
PROVIDER = import_script_module("lib.python.core.gitlab.cluster_agent.provider")
BINDING = import_script_module("lib.python.core.project_binding")
ENDPOINTS = import_script_module("lib.python.core.endpoints")
TARGET = import_script_module("lib.python.core.target")

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
UNRESOLVED: Final = "agent_target_unresolved"
# Source kinds project_binding.resolve_target_file reports for each route.
ARGV_TARGET_SOURCE: Final = "argv:--target"
ENV_TARGET_SOURCE: Final = f"env:{BINDING.TARGET_ENV}"
# The caller selection appended to the source when it passed --gitlab private.
PRIVATE_SELECTION: Final = (
    f"{ENDPOINTS.ROUTING_ARGV_PREFIX}{ENDPOINTS.GITLAB_TARGET_OPTION}"
    f"={ENDPOINTS.GITLAB_PRIVATE_SELECTOR}"
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


@pytest.mark.parametrize(
    "selector",
    (None, ENDPOINTS.GITLAB_PRIVATE_SELECTOR),
    ids=("routing-default", "gitlab-private"),
)
@pytest.mark.parametrize(
    "from_environment", (False, True), ids=("target-argument", "target-environment")
)
def test_reported_target_source_names_the_route_and_selector_without_the_path(
    target_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    selector: str | None,
    from_environment: bool,
) -> None:
    """The reported source is the source kind plus the caller's GitLab selector.

    The command result carries this value as ``target_source``. It names how the
    target was found and the ``--gitlab`` value the caller passed, never the
    target file path that ``target_reference`` keeps.
    """
    monkeypatch.chdir(target_file.parent)
    monkeypatch.delenv(BINDING.BINDING_ENV, raising=False)
    monkeypatch.delenv(BINDING.TARGET_ENV, raising=False)
    if from_environment:
        monkeypatch.setenv(BINDING.TARGET_ENV, str(target_file))
    selected = AUTHORITY.resolve_agent_target(
        None if from_environment else str(target_file),
        None,
        PROJECT,
        TARGET.GitLabSelection(selector),
        dry_run=True,
    )

    reported = AUTHORITY.reported_target_source(selected.target)

    source = ENV_TARGET_SOURCE if from_environment else ARGV_TARGET_SOURCE
    assert reported == (source if selector is None else f"{source};{PRIVATE_SELECTION}")
    assert target_file.name not in reported
    assert str(target_file.resolve()) in selected.target.target_reference


def test_reported_target_source_refuses_a_target_without_a_source_kind(
    target_file: Path,
) -> None:
    """A target that skipped source resolution is not reported with a guessed source.

    ``load_target`` alone returns a Target with no ``source_kind``; only
    ``resolve_target`` classifies it.
    """
    selected = AUTHORITY.resolve_agent_target(
        str(target_file), None, PROJECT, None, dry_run=True
    )

    with pytest.raises(AUTHORITY.TargetError) as refused:
        AUTHORITY.reported_target_source(replace(selected.target, source_kind=None))

    assert str(refused.value) == UNRESOLVED
