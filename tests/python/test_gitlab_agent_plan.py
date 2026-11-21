"""Verify that native fresh-connect observations are bound to confirmation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from conftest import import_script_module
from test_gitlab_agent_installation import BASELINE, SELECTION_CASE, Stage

PLAN = import_script_module("lib.python.core.gitlab.cluster_agent.plan")
PROVIDER = import_script_module("lib.python.core.gitlab.cluster_agent.provider")
OCI = import_script_module("lib.python.core.oci")

CLUSTER_UID = "fixture-cluster"
NAMESPACE_UID = "fixture-namespace"
REVISION = "a" * 40
IMAGE_DIGEST = "sha256:" + "a" * 64
PLATFORM_DIGEST = "sha256:" + "b" * 64
KAS = PROVIDER.KASMetadata(
    "18.5.2", "18.5.2", "wss://kas.example.test", "https://kas.example.test/k8s-proxy"
)
STALE = "confirm_plan_mismatch_rerun_live_plan"


@pytest.mark.parametrize(
    ("changed", "reason"),
    (
        (None, None),
        ("candidate", STALE),
        ("namespace", STALE),
        ("kas", STALE),
        ("image", STALE),
        ("cluster", "agent_plan_cluster_changed"),
        ("chart", "agent_plan_chart_pin_mismatch"),
        ("chart_occupied", "agent_chart_resource_collision"),
        ("namespace_replaced", "agent_plan_namespace_changed"),
        ("owned_namespace_occupied", "agent_plan_namespace_collision"),
    ),
)
def test_fresh_connect_confirmation(
    monkeypatch: pytest.MonkeyPatch, changed: str | None, reason: str | None
) -> None:
    """Change one observation on an isolated copy and require fresh confirmation."""
    wire = json.loads(BASELINE)
    chart = PLAN.VerifiedChart(Path("fixture-agent.tgz"), PLAN.CHART_SHA256)
    image = OCI.ImageManifestIdentity(
        IMAGE_DIGEST, frozenset((IMAGE_DIGEST, PLATFORM_DIGEST))
    )
    selection = SELECTION_CASE
    kubernetes = Mock()
    kubernetes.read.return_value = None
    kubernetes.configuration = SimpleNamespace(
        context="fixture", server="https://kube.example.test"
    )
    session = SimpleNamespace(
        origin="https://gitlab.example.test",
        skill={"revision": {"value": REVISION}, "functional_digest": "c" * 64},
    )
    authorities = PLAN.AgentAuthorities(
        SimpleNamespace(session=session),
        Mock(),
        kubernetes,
        selection.project,
        KAS,
        CLUSTER_UID,
    )
    read_scope = Mock(return_value=(CLUSTER_UID, NAMESPACE_UID))
    absence = Mock(return_value=SimpleNamespace(cluster_uid=CLUSTER_UID))
    helm = Mock()
    helm.render.return_value = yaml.safe_dump_all(wire[Stage.RENDER])
    monkeypatch.setattr(PLAN, "read_agent_cluster_scope", read_scope)
    monkeypatch.setattr(PLAN, "verify_agent_logical_absence", absence)
    monkeypatch.setattr(PLAN, "HelmClient", Mock(return_value=helm))
    deadline = PLAN.RequestDeadline(30, monotonic=lambda: 0)

    expected = PLAN.prepare_fresh_connect(
        authorities, selection, chart, image, deadline=deadline
    )
    assert expected.namespace_uid == NAMESPACE_UID
    assert len(expected.digest) == 64
    if changed == "candidate":
        session.skill["revision"]["value"] = "b" * 40
    elif changed == "namespace":
        read_scope.return_value = (CLUSTER_UID, "new-namespace")
    elif changed == "kas":
        authorities = replace(authorities, kas=replace(KAS, version="19.0.0"))
    elif changed == "image":
        next_digest = "sha256:" + "d" * 64
        image = OCI.ImageManifestIdentity(next_digest, frozenset((next_digest,)))
    elif changed == "cluster":
        absence.return_value = SimpleNamespace(cluster_uid="different-cluster")
    elif changed == "chart":
        chart = replace(chart, sha256="d" * 64)
    elif changed == "chart_occupied":
        kubernetes.read.return_value = wire[Stage.AFTER][0]
    elif changed == "namespace_replaced":
        read_scope.side_effect = (
            (CLUSTER_UID, NAMESPACE_UID),
            (CLUSTER_UID, "replacement-namespace"),
        )
    elif changed == "owned_namespace_occupied":
        selection = replace(
            selection, scope=replace(selection.scope, owns_namespace=True)
        )
        kubernetes.read.return_value = {"metadata": {"uid": NAMESPACE_UID}}

    if reason is None:
        current = PLAN.prepare_fresh_connect(
            authorities, selection, chart, image, deadline=deadline
        )
        current.require_confirmation(expected.digest)
        assert current == expected
    else:
        with pytest.raises((PLAN.KubernetesError, ValueError), match=reason):
            current = PLAN.prepare_fresh_connect(
                authorities, selection, chart, image, deadline=deadline
            )
            current.require_confirmation(expected.digest)
    kubernetes.apply.assert_not_called()
    helm.install_fresh.assert_not_called()


@pytest.mark.parametrize(
    ("kind", "changed"),
    (("connect", False), ("connect", True), ("delete", False), ("delete", True)),
)
def test_repeat_confirmation_uses_native_proof_without_writes(
    monkeypatch: pytest.MonkeyPatch, kind: str, changed: bool
) -> None:
    """Recheck authoritative facts for each repeat operation before accepting its digest."""
    selection = SELECTION_CASE
    kubernetes = Mock()
    kubernetes.configuration = SimpleNamespace(
        context="fixture", server="https://kube.example.test"
    )
    session = SimpleNamespace(
        origin="https://gitlab.example.test",
        skill={"revision": {"value": REVISION}, "functional_digest": "c" * 64},
    )
    authorities = PLAN.AgentAuthorities(
        SimpleNamespace(session=session),
        Mock(),
        kubernetes,
        selection.project,
        KAS,
        CLUSTER_UID,
    )
    journal = PLAN.RecoveryRecord("journal", "5", {"state": "CONNECTED"})
    image = OCI.ImageManifestIdentity(IMAGE_DIGEST, frozenset((IMAGE_DIGEST,)))
    chart = PLAN.VerifiedChart(Path("fixture.tgz"), PLAN.CHART_SHA256)
    current = PLAN.AgentConnectivity(journal, ())
    existing = Mock(return_value=current)
    absent = Mock(
        return_value=PLAN.LogicalAgentAbsence(
            selection.project, selection.release, CLUSTER_UID
        )
    )
    monkeypatch.setattr(PLAN, "verify_existing_agent", existing)
    monkeypatch.setattr(PLAN, "verify_agent_logical_absence", absent)
    deadline = PLAN.RequestDeadline(30, monotonic=lambda: 0)
    if kind == "connect":
        operation = PLAN.observe_repeat_connect
        parameters = (authorities, selection, journal, chart, image)
        expected = existing
    else:
        operation = PLAN.observe_repeat_delete
        parameters = (authorities, selection)
        expected = absent

    digest, observation = operation(*parameters, deadline=deadline)
    assert observation == expected.return_value
    if changed:
        session.skill["revision"]["value"] = "b" * 40
        with pytest.raises(ValueError, match=STALE):
            operation(*parameters, confirmation=digest, deadline=deadline)
    else:
        assert operation(*parameters, confirmation=digest, deadline=deadline) == (
            digest,
            observation,
        )
    assert expected.call_count == 2
    kubernetes.apply.assert_not_called()
    kubernetes.delete.assert_not_called()
