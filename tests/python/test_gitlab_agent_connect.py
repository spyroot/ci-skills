"""Offline first-install ordering and recovery boundaries for the GitLab Agent."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from enum import IntEnum, auto
from pathlib import Path
from types import SimpleNamespace
from typing import Final
from unittest.mock import ANY, Mock, create_autospec

import pytest
import test_gitlab_agent_installation as CHART_TEST
import test_gitlab_agent_recovery as RECOVERY_TEST
import yaml
from conftest import import_script_module
from test_gitlab_agent_recovery import (
    recovery_baseline as recovery_baseline,  # noqa: PLC0414 - shared pytest fixture
)

CONNECT = import_script_module("lib.python.core.gitlab.cluster_agent.connect")
HELM = CHART_TEST.NATIVE_HELM
HELM_STORAGE = import_script_module("lib.python.core.helm.storage")
INSTALL = CHART_TEST.INSTALL
SELECTION = RECOVERY_TEST.SELECTION
RECORD = RECOVERY_TEST.RECOVERY
KUBERNETES = RECOVERY_TEST.KUBERNETES
PROVIDER = import_script_module("lib.python.core.gitlab.cluster_agent.provider")

SELECTED: Final = RECOVERY_TEST.SELECTION_CASE
NAMESPACE: Final = SELECTED.scope.namespace
RELEASE: Final = SELECTED.release
NAMESPACE_UID: Final = RECOVERY_TEST.NAMESPACE_UID
SCOPE_UIDS: Final = (RECOVERY_TEST.CLUSTER_UID, NAMESPACE_UID)
SECRET_UID: Final = "registered-agent-secret-uid"
RECORD_UID: Final = RECOVERY_TEST.RECORD_UID
STORAGE_UID: Final = "helm-storage-native-uid"
STORAGE: Final = f"sh.helm.release.v1.{RELEASE}.v1"
IMAGE_DIGEST: Final = "sha256:" + "a" * 64
OTHER_IMAGE: Final = "sha256:" + "b" * 64
TIMEOUT: Final = 30.0
TOKEN_ID: Final = 29
CHART_ARCHIVE: Final = "pinned-chart.tgz"
DEPLOYED: Final = HELM.HelmRelease(RELEASE, NAMESPACE, 1, "deployed")
STORAGE_IDENTITY: Final = HELM_STORAGE.HelmStorageIdentity(STORAGE, STORAGE_UID)
KAS: Final = PROVIDER.KASMetadata(
    SELECTION.APP_VERSION,
    SELECTION.APP_VERSION.removeprefix("v"),
    "wss://gitlab.example.test/-/kubernetes-agent/",
    "https://gitlab.example.test/-/kubernetes-agent/k8s-proxy",
)


class Step(IntEnum):
    """Native stages that may each fail independently."""

    PIN = 0
    STATUS = auto()
    FIRST_STORAGE = auto()
    RENDER = auto()
    FIRST_ABSENCE = auto()
    REGISTER = auto()
    INSTALL_ADAPTER = auto()
    SCOPE = auto()
    CREDENTIAL = auto()
    FINAL_STORAGE = auto()
    FINAL_ABSENCE = auto()
    NATIVE_INSTALL = auto()
    INSTALLED_STORAGE = auto()
    PERSIST_STORAGE = auto()
    OBSERVE_RESOURCES = auto()
    PERSIST_RESOURCES = auto()


STEPS: Final = tuple(step for step in Step if step is not Step.PIN)


@pytest.fixture
def installation(
    monkeypatch: pytest.MonkeyPatch, recovery_baseline: str, tmp_path: Path
) -> SimpleNamespace:
    """Reuse exact fixture identities with one independently scripted failure."""
    registered = json.loads(recovery_baseline)[RECOVERY_TEST.UPDATED_ROW]
    pending = json.loads(recovery_baseline)[RECOVERY_TEST.PENDING_ROW]
    rendered = json.loads(CHART_TEST.BASELINE)[CHART_TEST.Stage.RENDER]
    manifest = yaml.safe_dump_all(rendered)
    rendered[0]["metadata"]["name"] = "changed-deployment"
    changed = yaml.safe_dump_all(rendered)
    case = SimpleNamespace(
        pending={**pending, "image_digest": IMAGE_DIGEST},
        registered={
            **registered,
            "image_digest": IMAGE_DIGEST,
            "registration_credential_id": TOKEN_ID,
            "credential_resource_uid": SECRET_UID,
            "created": {
                **registered["created"],
                "registration_credential": True,
                "credential_resource": True,
            },
            "resources": [
                {
                    "api_version": "v1",
                    "kind": kind,
                    "name": name,
                    "namespace": namespace,
                    "uid": uid,
                    "state": "PRESENT",
                    "created": bool(namespace),
                }
                for kind, name, namespace, uid in (
                    ("Namespace", NAMESPACE, "", NAMESPACE_UID),
                    ("ConfigMap", RECOVERY_TEST.RECORD_NAME, NAMESPACE, RECORD_UID),
                    (
                        "Secret",
                        pending["credential_resource_name"],
                        NAMESPACE,
                        SECRET_UID,
                    ),
                )
            ],
        },
        chart=HELM.VerifiedChart(tmp_path / CHART_ARCHIVE, SELECTION.CHART_SHA256),
        documents=[],
        events=[],
        failed=None,
        reply=None,
        later_render=manifest,
        changed_render=changed,
    )
    kube = create_autospec(KUBERNETES.KubernetesService, instance=True)
    gitlab = SimpleNamespace(session=SimpleNamespace(origin=RECOVERY_TEST.ORIGIN))
    helm = create_autospec(HELM.HelmClient, instance=True)
    secret = create_autospec(CONNECT.AgentSecretStore, instance=True)
    secret.name = pending["credential_resource_name"]
    store = create_autospec(RECORD.AgentRecoveryStore, instance=True)
    case.helm, case.kube, case.gitlab, case.store = helm, kube, gitlab, store
    storage_steps = iter(
        (Step.FIRST_STORAGE, Step.FINAL_STORAGE, Step.INSTALLED_STORAGE)
    )
    absence_steps = iter((Step.FIRST_ABSENCE, Step.FINAL_ABSENCE))
    write_steps = iter((Step.PERSIST_STORAGE, Step.PERSIST_RESOURCES))

    def reply(step: Step, normal: object) -> object:
        case.events.append(step)
        result = case.reply if case.failed is step else normal
        if isinstance(result, Exception):
            raise result
        return result

    def install(*_args, verify_absent, **_kwargs) -> object:
        reply(Step.INSTALL_ADAPTER, None)
        verify_absent(case.later_render)
        return reply(Step.NATIVE_INSTALL, DEPLOYED)

    def persist(previous, document) -> object:
        assert previous == case.documents[-1]
        record = RECORD.RecoveryRecord(
            RECORD_UID, str(len(case.documents) + 1), deepcopy(document)
        )
        observed = reply(next(write_steps), record)
        case.documents.append(observed)
        return observed

    def register(_gitlab, _kubernetes, _selection, document, *, deadline) -> object:
        assert document == case.pending
        record = RECORD.RecoveryRecord(RECORD_UID, "1", deepcopy(case.registered))
        result = reply(Step.REGISTER, record)
        case.documents.append(result)
        return result

    def read_storage(kubernetes, release) -> object:
        assert (kubernetes, release) == (case.kube, DEPLOYED)
        stage = next(storage_steps)
        storage = STORAGE_IDENTITY if stage is Step.INSTALLED_STORAGE else None
        return reply(stage, storage)

    def observe(_kubernetes, _selection, inventory) -> object:
        return reply(
            Step.OBSERVE_RESOURCES,
            tuple(
                INSTALL.ObservedChartResource(item, f"{item.kind.lower()}-uid")
                for item in inventory
            ),
        )

    helm.status.side_effect = lambda *_args, **_kwargs: reply(Step.STATUS, None)
    helm.render.side_effect = lambda *_args, **_kwargs: reply(Step.RENDER, manifest)
    helm.install_fresh.side_effect = install
    secret.read.side_effect = lambda: reply(Step.CREDENTIAL, SECRET_UID)
    store.replace.side_effect = persist

    monkeypatch.setattr(CONNECT, "HelmClient", Mock(return_value=helm))
    monkeypatch.setattr(CONNECT, "AgentSecretStore", Mock(return_value=secret))
    monkeypatch.setattr(CONNECT, "AgentRecoveryStore", Mock(return_value=store))
    monkeypatch.setattr(CONNECT, "register_fresh_agent", Mock(side_effect=register))
    monkeypatch.setattr(CONNECT, "read_helm_storage", Mock(side_effect=read_storage))
    monkeypatch.setattr(
        CONNECT,
        "read_agent_cluster_scope",
        Mock(side_effect=lambda *_: reply(Step.SCOPE, SCOPE_UIDS)),
    )
    monkeypatch.setattr(
        CONNECT,
        "verify_chart_absent",
        Mock(side_effect=lambda *_: reply(next(absence_steps), None)),
    )
    monkeypatch.setattr(CONNECT, "observe_chart_resources", Mock(side_effect=observe))
    return case


def _install(case: SimpleNamespace) -> RECORD.RecoveryRecord:
    """Drive the shipped composition with one exact confirmed candidate."""
    return CONNECT.install_fresh_agent(
        case.gitlab,
        case.kube,
        SELECTED,
        case.pending,
        KAS,
        case.chart,
        IMAGE_DIGEST,
        timeout=TIMEOUT,
    )


def test_agent_chart_install_persists_native_storage_before_owned_resource_reads(
    installation: SimpleNamespace,
) -> None:
    """Preserve the borrowed scope and verified native IDs while still PENDING."""
    before = deepcopy(installation.pending)

    result = _install(installation)

    assert tuple(installation.events) == STEPS
    registered, release, final = (record.document for record in installation.documents)
    assert result.document == final
    assert installation.pending == before
    assert registered["release_storage_uid"] is None
    assert release["release_revision"] == DEPLOYED.revision
    assert release["release_storage_name"] == STORAGE
    assert release["release_storage_uid"] == STORAGE_UID
    assert release["created"]["release"] is True
    assert final["state"] == before["state"]
    assert final["resources"][:3] == registered["resources"]
    assert release["resources"][:3] == registered["resources"]
    assert final["resources"][0]["created"] is False
    resources = {item["uid"] for item in final["resources"]}
    assert resources == {
        NAMESPACE_UID,
        RECORD_UID,
        SECRET_UID,
        STORAGE_UID,
        *(f"{kind.lower()}-uid" for _, kind, _, _ in CHART_TEST.IDENTITIES),
    }
    assert final["credential_resource_uid"] == SECRET_UID
    CONNECT.register_fresh_agent.assert_called_once_with(
        installation.gitlab, installation.kube, SELECTED, before, deadline=ANY
    )
    installation.helm.install_fresh.assert_called_once()
    call = installation.helm.install_fresh.call_args
    assert call.args[:3] == (RELEASE, NAMESPACE, installation.chart)
    assert call.args[3]["config"]["secretName"] == before["credential_resource_name"]
    assert call.args[3]["image"]["tag"] == f"{SELECTION.APP_VERSION}@{IMAGE_DIGEST}"
    assert 0 < call.kwargs["timeout"] <= TIMEOUT
    assert installation.kube.mock_calls == []


@pytest.mark.parametrize(
    ("stop", "field", "value", "reason"),
    (
        (Step.PIN, "chart", "e" * 64, "agent_install_chart_pin_mismatch"),
        (Step.PIN, "image", OTHER_IMAGE, "agent_install_image_pin_mismatch"),
        (Step.STATUS, "reply", DEPLOYED, "agent_install_release_already_present"),
        (
            Step.FIRST_STORAGE,
            "reply",
            STORAGE_IDENTITY,
            "agent_install_storage_already_present",
        ),
        (
            Step.FIRST_ABSENCE,
            "reply",
            KUBERNETES.KubernetesError("agent_chart_resource_collision"),
            "agent_chart_resource_collision",
        ),
        (
            Step.SCOPE,
            "reply",
            (SCOPE_UIDS[0], "replaced-namespace-uid"),
            "agent_install_scope_mismatch",
        ),
        (
            Step.CREDENTIAL,
            "reply",
            "new-secret-uid",
            "agent_install_credential_mismatch",
        ),
        (Step.CREDENTIAL, "render", None, "agent_install_render_mismatch"),
        (
            Step.FINAL_STORAGE,
            "reply",
            STORAGE_IDENTITY,
            "agent_install_storage_already_present",
        ),
        (
            Step.FINAL_ABSENCE,
            "reply",
            KUBERNETES.KubernetesError("agent_chart_resource_collision"),
            "agent_chart_resource_collision",
        ),
        (
            Step.NATIVE_INSTALL,
            "reply",
            HELM.HelmError("helm_install_timeout"),
            "helm_install_timeout",
        ),
        (
            Step.NATIVE_INSTALL,
            "reply",
            replace(DEPLOYED, revision=2),
            "agent_install_release_unverified",
        ),
        (Step.INSTALLED_STORAGE, "reply", None, "agent_install_storage_unverified"),
        (
            Step.PERSIST_STORAGE,
            "reply",
            OSError("unconfirmed persist"),
            "agent_registration_persistence_unconfirmed",
        ),
        (
            Step.OBSERVE_RESOURCES,
            "reply",
            KUBERNETES.KubernetesError("agent_chart_resource_owner_mismatch"),
            "agent_chart_resource_owner_mismatch",
        ),
    ),
    ids=(
        "wrong-chart-pin",
        "wrong-image-pin",
        "existing-helm-release",
        "first-revision-storage-present",
        "occupied-preflight-resource",
        "replaced-namespace-before-install",
        "replaced-secret-before-install",
        "render-drift",
        "storage-appeared-before-install",
        "occupied-resource-before-install",
        "uncertain-native-install-no-retry",
        "wrong-native-release-revision",
        "missing-storage-after-install",
        "storage-journal-write-unconfirmed",
        "native-chart-ownership-mismatch",
    ),
)
def test_agent_install_stops_at_one_unverified_dependency(
    installation: SimpleNamespace, stop: Step, field: str, value: object, reason: str
) -> None:
    """Refuse later writes when one independently observed value differs."""
    if field == "chart":
        installation.chart = replace(installation.chart, sha256=value)
    elif field == "image":
        installation.pending["image_digest"] = value
    elif field == "render":
        installation.later_render = installation.changed_render
    else:
        installation.failed = stop
        installation.reply = value

    errors = (KUBERNETES.KubernetesError, HELM.HelmError, ValueError)
    with pytest.raises(errors) as rejected:
        _install(installation)

    assert str(rejected.value) == reason
    assert tuple(installation.events) == STEPS[:stop]
    if stop < Step.NATIVE_INSTALL:
        assert Step.NATIVE_INSTALL not in installation.events
    assert installation.kube.mock_calls == []
