"""Prove logical Agent absence without adopting, deleting, or emitting Secret data."""

from __future__ import annotations

from dataclasses import replace
from enum import IntEnum
from types import SimpleNamespace
from typing import Final
from unittest.mock import Mock, create_autospec

import pytest
import test_gitlab_agent_recovery as RECOVERY_TEST
from conftest import import_script_module

ABSENCE = import_script_module("lib.python.core.gitlab.cluster_agent.absence")
PROVIDER = import_script_module("lib.python.core.gitlab.cluster_agent.provider")
SELECTION = RECOVERY_TEST.SELECTION
KUBERNETES = RECOVERY_TEST.KUBERNETES
RUNTIME = RECOVERY_TEST.RUNTIME

PROJECT: Final = RECOVERY_TEST.SELECTION_CASE.project
SELECTED: Final = SELECTION.select_connect(
    PROJECT,
    project_name=None,
    agent_name=RECOVERY_TEST.AGENT,
    namespace=None,
    run_id=RECOVERY_TEST.RUN_ID,
)
REGISTERED: Final = PROVIDER.AgentRegistration(
    RECOVERY_TEST.AGENT_ID, SELECTED.agent_name, PROJECT.id
)
TIMEOUT: Final = 30.0
EMPTY: Final = RUNTIME.CommandResult((), 0, "", "")
FAILURE: Final = 1
CLUSTER_UID: Final = RECOVERY_TEST.CLUSTER_UID


@pytest.fixture
def logical_absence(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Bind known provider authority and metadata-only Kubernetes evidence."""
    gitlab = SimpleNamespace(target_id=PROJECT.id)
    provider = create_autospec(PROVIDER.AgentProvider, instance=True)
    provider.project.return_value = PROJECT
    provider.find_agent.return_value = None
    kube = create_autospec(KUBERNETES.KubernetesService, instance=True)
    kube.command.return_value = EMPTY
    cluster = Mock(return_value=CLUSTER_UID)
    monkeypatch.setattr(ABSENCE, "AgentProvider", Mock(return_value=provider))
    monkeypatch.setattr(ABSENCE, "read_agent_cluster_uid", cluster)
    trace = Mock()
    trace.attach_mock(provider, "provider")
    trace.attach_mock(kube.command, "kubectl")
    trace.attach_mock(cluster, "cluster")
    return SimpleNamespace(
        gitlab=gitlab, kube=kube, provider=provider, cluster=cluster, trace=trace
    )


def _verify(case: SimpleNamespace) -> ABSENCE.LogicalAgentAbsence:
    """Repeat the same logical selection without relying on an old record."""
    return ABSENCE.verify_agent_logical_absence(
        case.gitlab, case.kube, SELECTED, timeout=TIMEOUT
    )


def _assert_read_only(case: SimpleNamespace) -> None:
    """Reject any native write or broad output projection in a repeat check."""
    case.provider.create_agent.assert_not_called()
    case.provider.create_token.assert_not_called()
    case.provider.delete_agent.assert_not_called()
    case.provider.delete_token.assert_not_called()
    for invocation in case.kube.command.call_args_list:
        assert invocation.args[0] == "get"
        assert invocation.args[-2:] == ("-o", "name")
        assert invocation.kwargs.get("input_data") is None


class Probe(IntEnum):
    """One independently observable read in its required order."""

    PROJECT = -2
    AGENT = -1
    RUN = 0
    OPERATION = 1
    HELM_SELECTOR = 2
    HELM_NAME = 3
    CREDENTIAL_NAME = 4
    JOURNAL_NAME = 5
    CHART_SELECTOR = 6


CHART_TYPES: Final = (
    "pods,deployments,replicasets,services,serviceaccounts,"
    "rolebindings,clusterrolebindings"
)
OWNED_TYPES: Final = (
    "namespaces,pods,configmaps,secrets,deployments,replicasets,services,"
    "serviceaccounts,rolebindings,clusterrolebindings"
)
ALL_NAMESPACES: Final = "--all-namespaces"
FIELD: Final = "--field-selector"
LABEL: Final = "-l"
RUN: Final = SELECTED.scope.run_id
RELEASE: Final = SELECTED.release
STORAGE: Final = f"sh.helm.release.v1.{RELEASE}.v1"
CREDENTIAL: Final = SELECTED.scope.bounded_name(
    "agent-token", limit=RECOVERY_TEST.SCOPE.DNS_LABEL_LIMIT, prefix=SELECTED.agent_name
)
JOURNAL: Final = SELECTED.scope.bounded_name(
    "agent-recovery",
    limit=RECOVERY_TEST.SCOPE.DNS_LABEL_LIMIT,
    prefix=SELECTED.agent_name,
)
COMMANDS: Final = (
    (OWNED_TYPES, LABEL, f"{RECOVERY_TEST.SCOPE.RUN_ID_LABEL}={RUN}"),
    (OWNED_TYPES, LABEL, f"{RECOVERY_TEST.RESOURCES.OPERATION_LABEL}={RUN}"),
    ("secrets", LABEL, f"owner=helm,name={RELEASE}"),
    ("secrets", FIELD, f"metadata.name={STORAGE}"),
    ("secrets", FIELD, f"metadata.name={CREDENTIAL}"),
    ("configmaps", FIELD, f"metadata.name={JOURNAL}"),
    (CHART_TYPES, LABEL, f"app.kubernetes.io/instance={RELEASE}"),
)
NO_MARKERS: Final = (EMPTY,) * len(COMMANDS)
PROJECT_MISMATCH: Final = "agent_absence_project_mismatch"
AGENT_PRESENT: Final = "agent_absence_registration_present"
RUN_PRESENT: Final = "agent_absence_run_markers_present"
RELEASE_PRESENT: Final = "agent_absence_release_present"
CHART_PRESENT: Final = "agent_absence_chart_markers_present"
READ_UNCONFIRMED: Final = "agent_absence_kubernetes_read_unconfirmed"


def test_logical_absence_checks_all_exact_markers_without_writes(
    logical_absence: SimpleNamespace,
) -> None:
    """Prove the logical names and run selectors with only native metadata reads."""
    case = logical_absence
    case.kube.command.side_effect = NO_MARKERS

    assert _verify(case) == ABSENCE.LogicalAgentAbsence(PROJECT, RELEASE, CLUSTER_UID)

    case.provider.project.assert_called_once_with()
    case.provider.find_agent.assert_called_once_with(SELECTED.agent_name)
    case.cluster.assert_called_once_with(case.kube)
    assert [entry[0] for entry in case.trace.mock_calls] == [
        "provider.project",
        "provider.find_agent",
        *(("kubectl",) * len(COMMANDS)),
        "cluster",
    ]
    for observed, (kinds, flag, selector) in zip(
        case.kube.command.call_args_list, COMMANDS, strict=True
    ):
        assert observed.args == (
            "get",
            kinds,
            ALL_NAMESPACES,
            flag,
            selector,
            "-o",
            "name",
        )
        assert 0 < observed.kwargs["timeout"] <= TIMEOUT
    _assert_read_only(case)


@pytest.mark.parametrize(
    ("probe", "changed", "reason"),
    (
        (Probe.PROJECT, replace(PROJECT, id=PROJECT.id + 1), PROJECT_MISMATCH),
        (Probe.AGENT, REGISTERED, AGENT_PRESENT),
        (
            Probe.RUN,
            replace(EMPTY, stdout=f"secret/{CREDENTIAL}\n"),
            RUN_PRESENT,
        ),
        (
            Probe.OPERATION,
            replace(EMPTY, stdout=f"configmap/{JOURNAL}\n"),
            RUN_PRESENT,
        ),
        (
            Probe.HELM_SELECTOR,
            replace(EMPTY, stdout=f"secret/{STORAGE}\n"),
            RELEASE_PRESENT,
        ),
        (
            Probe.HELM_NAME,
            replace(EMPTY, stdout=f"secret/{STORAGE}\n"),
            RELEASE_PRESENT,
        ),
        (
            Probe.CREDENTIAL_NAME,
            replace(EMPTY, stdout=f"secret/{CREDENTIAL}\n"),
            RUN_PRESENT,
        ),
        (
            Probe.JOURNAL_NAME,
            replace(EMPTY, stdout=f"configmap/{JOURNAL}\n"),
            RUN_PRESENT,
        ),
        (
            Probe.CHART_SELECTOR,
            replace(EMPTY, stdout=f"deployment.apps/{RELEASE}\n"),
            CHART_PRESENT,
        ),
        (Probe.RUN, replace(EMPTY, returncode=FAILURE), READ_UNCONFIRMED),
        (
            Probe.RUN,
            replace(EMPTY, stderr="Warning: incomplete discovery\n"),
            READ_UNCONFIRMED,
        ),
    ),
    ids=(
        "different-project-id",
        "agent-still-present",
        "only-a-labeled-secret-remains",
        "only-operation-label-remains",
        "release-storage-label-present",
        "exact-unlabeled-storage-present",
        "exact-unlabeled-credential-present",
        "exact-unlabeled-journal-present",
        "chart-instance-present",
        "failed-native-read",
        "ambiguous-native-read",
    ),
)
def test_logical_absence_rejects_one_changed_observation_without_writes(
    logical_absence: SimpleNamespace,
    probe: Probe,
    changed: object,
    reason: str,
) -> None:
    """Change exactly one native observation and stop before later proof reads."""
    case = logical_absence
    responses = list(NO_MARKERS)
    if probe == Probe.PROJECT:
        case.provider.project.return_value = changed
    elif probe == Probe.AGENT:
        case.provider.find_agent.return_value = changed
    else:
        responses[probe] = changed
    case.kube.command.side_effect = responses

    with pytest.raises(KUBERNETES.KubernetesError, match=f"^{reason}$"):
        _verify(case)

    assert case.kube.command.call_count == max(0, probe + 1)
    if probe == Probe.PROJECT:
        case.provider.find_agent.assert_not_called()
    case.cluster.assert_not_called()
    _assert_read_only(case)
