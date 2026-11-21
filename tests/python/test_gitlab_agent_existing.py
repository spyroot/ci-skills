"""Verify that the original connected Agent installation is proved without writes."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Final
from unittest.mock import Mock, create_autospec

import pytest
import test_gitlab_agent_connect as INSTALL_TEST
import test_gitlab_agent_connection as CONNECT_TEST
import test_gitlab_agent_installation as CHART_TEST
import yaml
from conftest import import_script_module
from test_gitlab_agent_connection import (
    installed_ownership as installed_ownership,  # noqa: PLC0414 - shared pytest fixture
)
from test_gitlab_agent_recovery import (
    recovery_baseline as recovery_baseline,  # noqa: PLC0414 - shared pytest fixture
)

EXISTING = import_script_module("lib.python.core.gitlab.cluster_agent.existing")
SECRET = import_script_module("lib.python.core.gitlab.cluster_agent.secret")
REGISTRATION = import_script_module("lib.python.core.gitlab.cluster_agent.registration")
RECOVERY_TEST = CONNECT_TEST.RECOVERY_TEST
RECOVERY = CONNECT_TEST.RECOVERY
CONNECTION = CONNECT_TEST.CONNECTION
PROVIDER = CONNECT_TEST.PROVIDER
KUBERNETES = CONNECT_TEST.KUBERNETES
HELM = INSTALL_TEST.HELM

SELECTION: Final = INSTALL_TEST.SELECTED
NAMESPACE: Final = SELECTION.scope.namespace
TIMEOUT: Final = INSTALL_TEST.TIMEOUT
CONNECTED: Final = CONNECT_TEST.CONNECTED
VERIFIED: Final = "VERIFIED"
STATE: Final = "state"
RESOURCES: Final = "resources"
RESOURCE_UID: Final = "uid"
SECRET_NAME: Final = "credential_resource_name"
SECRET_UID: Final = "credential_resource_uid"
RECORD_MISMATCH: Final = "agent_existing_record_mismatch"
OTHER_UID: Final = "replacement-native-uid"
SECOND_REVISION: Final = 2
TOKEN_ID: Final = INSTALL_TEST.TOKEN_ID
OTHER_TOKEN_ID: Final = TOKEN_ID + 1
UID_ROLE: Final = "agent-token"
ACTIVE: Final = (
    CONNECTION.ConnectedAgentPod(CONNECT_TEST.NATIVE, CONNECT_TEST.OBSERVATION),
)
RENDER: Final = yaml.safe_dump_all(
    json.loads(CHART_TEST.BASELINE)[CHART_TEST.Stage.RENDER]
)


class Boundary(StrEnum):
    """Native observation whose value is changed by one rejection case."""

    RECORD = "record"
    RELEASE = "release"
    STORAGE = "storage"
    VALUES = "values"
    RESOURCE = "resource"
    TOKEN = "token"
    TOKEN_SET = "token-set"
    SECRET = "secret"
    CONNECTION = "connection"
    JOURNAL = "journal"


@pytest.fixture
def existing(
    monkeypatch: pytest.MonkeyPatch, installed_ownership: str, tmp_path: Path
) -> SimpleNamespace:
    """Reuse the original install and Pod identities with independent native reads."""
    document = json.loads(installed_ownership)
    document[STATE] = CONNECTED
    document[RESOURCES].append(
        REGISTRATION.observed_resource(
            "Pod", CONNECT_TEST.POD_NAME, NAMESPACE, CONNECT_TEST.POD_UID, created=True
        )
    )
    secret_name = SELECTION.scope.bounded_name(
        UID_ROLE, limit=RECOVERY_TEST.SCOPE.DNS_LABEL_LIMIT, prefix=SELECTION.agent_name
    )
    for resource in document[RESOURCES]:
        if resource[RESOURCE_UID] == INSTALL_TEST.SECRET_UID:
            resource["name"] = secret_name
    document[SECRET_NAME] = secret_name
    record = RECOVERY.RecoveryRecord(
        CONNECT_TEST.RECORD_UID, CONNECT_TEST.NEW_VERSION, document
    )
    token = PROVIDER.AgentToken(
        TOKEN_ID, document["registration_credential_name"], RECOVERY_TEST.AGENT_ID
    )
    image = CONNECT_TEST.OCI.ImageManifestIdentity(
        INSTALL_TEST.IMAGE_DIGEST,
        frozenset((INSTALL_TEST.IMAGE_DIGEST, CONNECT_TEST.PLATFORM_DIGEST)),
    )
    chart = HELM.VerifiedChart(
        tmp_path / INSTALL_TEST.CHART_ARCHIVE, document["chart_sha256"]
    )
    observed = tuple(
        CHART_TEST.INSTALL.ObservedChartResource(
            resource, f"{resource.kind.lower()}-uid"
        )
        for resource in CHART_TEST.INSTALL.agent_chart_inventory(RENDER, SELECTION)
    )

    helm = create_autospec(HELM.HelmClient, instance=True)
    helm.status.return_value = INSTALL_TEST.DEPLOYED
    helm.values_match.return_value = True
    helm.render.return_value = RENDER
    secret = create_autospec(SECRET.AgentSecretStore, instance=True)
    secret.name = secret_name
    secret.read.return_value = INSTALL_TEST.SECRET_UID
    provider = create_autospec(PROVIDER.AgentProvider, instance=True)
    provider.project.return_value = SELECTION.project
    provider.read_agent.return_value = CONNECT_TEST.AGENT
    provider.read_token.return_value = token
    provider.list_tokens.return_value = (token,)
    store = create_autospec(RECOVERY.AgentRecoveryStore, instance=True)
    store.name = RECOVERY_TEST.RECORD_NAME
    store.read.return_value = deepcopy(record)
    gitlab = SimpleNamespace(
        target_id=SELECTION.project.id,
        session=SimpleNamespace(origin=RECOVERY_TEST.ORIGIN),
    )
    kube = create_autospec(KUBERNETES.KubernetesService, instance=True)
    storage = Mock(return_value=INSTALL_TEST.STORAGE_IDENTITY)
    resources = Mock(return_value=observed)
    connectivity = Mock(return_value=ACTIVE)
    for name, reply in (
        ("HelmClient", helm),
        ("AgentSecretStore", secret),
        ("AgentProvider", provider),
        ("AgentRecoveryStore", store),
    ):
        monkeypatch.setattr(EXISTING, name, Mock(return_value=reply))
    for name, probe in (
        ("read_helm_storage", storage),
        ("observe_chart_resources", resources),
        ("read_agent_connectivity", connectivity),
    ):
        monkeypatch.setattr(EXISTING, name, probe)
    reads = {
        Boundary.RELEASE: helm.status,
        Boundary.STORAGE: storage,
        Boundary.VALUES: helm.values_match,
        Boundary.RESOURCE: resources,
        Boundary.TOKEN: provider.read_token,
        Boundary.TOKEN_SET: provider.list_tokens,
        Boundary.SECRET: secret.read,
        Boundary.CONNECTION: connectivity,
        Boundary.JOURNAL: store.read,
    }
    return SimpleNamespace(
        record=record,
        token=token,
        observed=observed,
        gitlab=gitlab,
        kube=kube,
        chart=chart,
        image=image,
        helm=helm,
        provider=provider,
        secret=secret,
        store=store,
        reads=reads,
        connectivity=connectivity,
    )


def _verify(case: SimpleNamespace) -> CONNECTION.AgentConnectivity:
    """Verify the selected installation with the unchanged chart and OCI pins."""
    return EXISTING.verify_existing_agent(
        case.gitlab,
        case.kube,
        SELECTION,
        case.record,
        INSTALL_TEST.KAS,
        case.chart,
        case.image,
        timeout=TIMEOUT,
    )


def _assert_read_only(case: SimpleNamespace) -> None:
    """Prove that rejecting or confirming an existing run performs no mutation."""
    case.helm.install_fresh.assert_not_called()
    case.provider.create_agent.assert_not_called()
    case.provider.create_token.assert_not_called()
    case.provider.delete_agent.assert_not_called()
    case.provider.delete_token.assert_not_called()
    case.secret.create.assert_not_called()
    case.store.create_pending.assert_not_called()
    case.store.replace.assert_not_called()
    assert case.kube.mock_calls == []


@pytest.mark.parametrize("state", (CONNECTED, VERIFIED))
def test_existing_agent_reads_exact_release_and_ownership_without_writes(
    existing: SimpleNamespace, state: str
) -> None:
    """Prove retained ownership and native readiness for both accepted states."""
    case = existing
    case.record.document[STATE] = state
    case.store.read.return_value = deepcopy(case.record)
    before = deepcopy(case.record)

    assert _verify(case) == CONNECTION.AgentConnectivity(before, ACTIVE)

    assert case.record == before
    case.helm.status.assert_called_once()
    assert case.helm.status.call_args.args == (SELECTION.release, NAMESPACE)
    case.helm.values_match.assert_called_once()
    release, values = case.helm.values_match.call_args.args
    assert release == INSTALL_TEST.DEPLOYED
    assert values["config"]["secretName"] == before.document[SECRET_NAME]
    assert values["config"]["kasAddress"] == INSTALL_TEST.KAS.address
    assert values["image"]["tag"].endswith("@" + case.image.digest)
    case.helm.render.assert_called_once()
    assert case.helm.render.call_args.args == (
        SELECTION.release,
        NAMESPACE,
        case.chart,
        values,
    )
    case.reads[Boundary.STORAGE].assert_called_once_with(
        case.kube, INSTALL_TEST.DEPLOYED
    )
    case.provider.read_token.assert_called_once_with(RECOVERY_TEST.AGENT_ID, TOKEN_ID)
    case.provider.list_tokens.assert_called_once_with(RECOVERY_TEST.AGENT_ID)
    case.secret.read.assert_called_once_with()
    case.connectivity.assert_called_once()
    assert case.connectivity.call_args.args == (
        case.gitlab,
        case.kube,
        SELECTION,
        before,
        case.image,
    )
    assert case.connectivity.call_args.kwargs.get("wait", False) is False
    case.store.read.assert_called_once_with()
    _assert_read_only(case)


@pytest.mark.parametrize(
    ("boundary", "changed", "reason"),
    (
        (
            Boundary.RECORD,
            lambda _: {STATE: CONNECT_TEST.PENDING},
            RECORD_MISMATCH,
        ),
        (
            Boundary.RECORD,
            lambda _: {SECRET_UID: OTHER_UID},
            RECORD_MISMATCH,
        ),
        (
            Boundary.RELEASE,
            lambda _: replace(INSTALL_TEST.DEPLOYED, revision=SECOND_REVISION),
            "agent_existing_release_mismatch",
        ),
        (
            Boundary.STORAGE,
            lambda _: replace(INSTALL_TEST.STORAGE_IDENTITY, uid=OTHER_UID),
            "agent_existing_storage_mismatch",
        ),
        (
            Boundary.VALUES,
            lambda _: False,
            "agent_existing_values_mismatch",
        ),
        (
            Boundary.RESOURCE,
            lambda case: (
                replace(case.observed[0], uid=OTHER_UID),
                *case.observed[1:],
            ),
            "agent_existing_inventory_mismatch",
        ),
        (Boundary.TOKEN, lambda _: None, "agent_existing_token_mismatch"),
        (
            Boundary.TOKEN_SET,
            lambda case: (case.token, replace(case.token, id=OTHER_TOKEN_ID)),
            "agent_existing_token_mismatch",
        ),
        (Boundary.SECRET, lambda _: OTHER_UID, "agent_existing_credential_mismatch"),
        (
            Boundary.CONNECTION,
            lambda _: KUBERNETES.KubernetesError(CONNECT_TEST.POD_MISMATCH),
            CONNECT_TEST.POD_MISMATCH,
        ),
        (
            Boundary.JOURNAL,
            lambda case: replace(
                case.record, resource_version=RECOVERY_TEST.NEXT_VERSION
            ),
            "agent_existing_journal_changed",
        ),
    ),
    ids=(
        "pending-intent",
        "inconsistent-credential-journal",
        "changed-helm-revision",
        "replaced-helm-storage",
        "changed-stored-values",
        "replaced-chart-object",
        "missing-original-token",
        "extra-native-token",
        "replaced-credential-secret",
        "changed-connection-pod",
        "journal-moved-during-read",
    ),
)
def test_existing_agent_rejects_one_changed_identity_without_writes(
    existing: SimpleNamespace,
    boundary: Boundary,
    changed: Callable[[SimpleNamespace], object],
    reason: str,
) -> None:
    """One changed read blocks all later proof stages and every mutation."""
    case = existing
    deviation = changed(case)
    if boundary is Boundary.RECORD:
        assert isinstance(deviation, dict)
        case.record.document.update(deviation)
    elif isinstance(deviation, Exception):
        case.reads[boundary].side_effect = deviation
    else:
        case.reads[boundary].return_value = deviation

    with pytest.raises(KUBERNETES.KubernetesError, match=f"^{reason}$"):
        _verify(case)

    order = tuple(Boundary)
    position = order.index(boundary)
    for later in order[position + 1 :]:
        case.reads[later].assert_not_called()
    _assert_read_only(case)
