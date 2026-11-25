"""Offline tests of the exact Agent Helm inventory and its native ownership."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Final
from unittest.mock import call, create_autospec

import pytest
import yaml
from conftest import import_script_module

INSTALL = import_script_module("lib.python.core.gitlab.cluster_agent.installation")
NATIVE_HELM = import_script_module("lib.python.core.helm.native")
SELECTION = import_script_module("lib.python.core.gitlab.cluster_agent.selection")

PROJECT: Final = "consumer/project"
AGENT_NAME: Final = "consumer-agent"
NAMESPACE: Final = "consumer-agent-system"
ELSEWHERE: Final = "other-agent-system"
WORKLOAD: Final = "agent-workload"
API_VERSION: Final = "apiVersion"
KIND: Final = "kind"
METADATA: Final = "metadata"
ANNOTATIONS: Final = "annotations"
RELEASE_NAME: Final = "meta.helm.sh/release-name"
RELEASE_NAMESPACE: Final = "meta.helm.sh/release-namespace"
MANAGER: Final = "app.kubernetes.io/managed-by"
FIRST_OBJECT: Final = 0
OWNER_MISMATCH: Final = "agent_chart_resource_owner_mismatch"
RUNTIME_INVALID: Final = "agent_chart_resource_invalid"
SELECTION_CASE = SELECTION.ConnectSelection(
    project=SELECTION.AgentProject(42, PROJECT),
    agent_name=AGENT_NAME,
    scope=SELECTION.RunScope("owned-run", NAMESPACE, False),
    release="consumer-agent-release",
)


class Stage(StrEnum):
    """The exact manifest, absence read, and ownership read boundaries."""

    RENDER = "render"
    BEFORE = "before"
    AFTER = "after"


# Name and namespace remain exact while the API tuple names the native type.
IDENTITIES: Final = (
    ("apps/v1", "Deployment", NAMESPACE, "deployment.v1.apps"),
    ("v1", "ServiceAccount", NAMESPACE, "ServiceAccount"),
    ("v1", "Service", NAMESPACE, "Service"),
    (
        "rbac.authorization.k8s.io/v1",
        "ClusterRoleBinding",
        "",
        "clusterrolebinding.v1.rbac.authorization.k8s.io",
    ),
)
READS: Final = tuple(
    call.read(native_kind, WORKLOAD, namespace)
    for _, _, namespace, native_kind in IDENTITIES
)


def _render_document(version: str, kind: str, namespace: str) -> dict[str, object]:
    """Represent an exact object creation and omit a templated Service namespace."""
    metadata = {"name": WORKLOAD}
    if namespace and kind != "Service":
        metadata["namespace"] = namespace
    return {API_VERSION: version, KIND: kind, METADATA: metadata}


def _native_document(version: str, kind: str, namespace: str) -> dict[str, object]:
    """Provide independent UID and Helm owner data on the observed object."""
    return {
        **_render_document(version, kind, namespace),
        METADATA: {
            "name": WORKLOAD,
            **({"namespace": namespace} if namespace else {}),
            "uid": f"native-{kind.lower()}-uid",
            "labels": {MANAGER: "Helm"},
            ANNOTATIONS: {
                RELEASE_NAME: SELECTION_CASE.release,
                RELEASE_NAMESPACE: NAMESPACE,
            },
        },
    }


# All cases start from the same immutable set of render and native responses.
BASELINE: Final = json.dumps(
    {
        Stage.RENDER: [
            _render_document(version, kind, namespace)
            for version, kind, namespace, _ in IDENTITIES
        ],
        Stage.BEFORE: [None] * len(IDENTITIES),
        Stage.AFTER: [
            _native_document(version, kind, namespace)
            for version, kind, namespace, _ in IDENTITIES
        ],
    }
)


def _inventory(wire: dict[str, object]) -> tuple[INSTALL.RenderedResource, ...]:
    """Parse the simulated chart output through the shipped inventory reader."""
    return INSTALL.agent_chart_inventory(
        yaml.safe_dump_all(wire[Stage.RENDER]), SELECTION_CASE
    )


def test_agent_chart_inventory_reconciles_exact_native_helm_owners() -> None:
    """Read exact identities before install and collect the live UID of each owner."""
    wire = json.loads(BASELINE)
    service = create_autospec(INSTALL.KubernetesService, instance=True)
    resources = _inventory(wire)
    service.read.side_effect = [*wire[Stage.BEFORE], *wire[Stage.AFTER]]

    assert resources == tuple(
        INSTALL.RenderedResource(version, kind, WORKLOAD, namespace)
        for version, kind, namespace, _ in IDENTITIES
    )
    INSTALL.verify_chart_absent(service, resources)
    observed = INSTALL.observe_chart_resources(service, SELECTION_CASE, resources)

    assert observed == tuple(
        INSTALL.ObservedChartResource(resource, f"native-{resource.kind.lower()}-uid")
        for resource in resources
    )
    assert tuple(service.mock_calls) == READS + READS


@pytest.mark.parametrize(
    ("stage", "path", "value", "reason"),
    (
        (
            Stage.RENDER,
            (FIRST_OBJECT, METADATA, "namespace"),
            ELSEWHERE,
            "helm_render_resource_namespace_invalid",
        ),
        (
            Stage.RENDER,
            (FIRST_OBJECT, KIND),
            "Job",
            "helm_render_resource_type_unsupported",
        ),
        (
            Stage.RENDER,
            (len(IDENTITIES),),
            None,
            "helm_render_duplicate_resource",
        ),
        (
            Stage.RENDER,
            (FIRST_OBJECT, METADATA, "generateName"),
            f"{WORKLOAD}-",
            "helm_render_generate_name_unsupported",
        ),
        (
            Stage.RENDER,
            (FIRST_OBJECT, METADATA, ANNOTATIONS),
            {"helm.sh/hook": "post-install"},
            "helm_render_hook_unsupported",
        ),
        (Stage.BEFORE, (FIRST_OBJECT,), None, "agent_chart_resource_collision"),
        (Stage.AFTER, (FIRST_OBJECT, METADATA, "uid"), "", RUNTIME_INVALID),
        (
            Stage.AFTER,
            (FIRST_OBJECT, METADATA, "labels", MANAGER),
            "Other",
            OWNER_MISMATCH,
        ),
        (
            Stage.AFTER,
            (FIRST_OBJECT, METADATA, ANNOTATIONS, RELEASE_NAME),
            "other-release",
            OWNER_MISMATCH,
        ),
        (
            Stage.AFTER,
            (FIRST_OBJECT, METADATA, ANNOTATIONS, RELEASE_NAMESPACE),
            ELSEWHERE,
            OWNER_MISMATCH,
        ),
    ),
    ids=(
        "cross-namespace-render",
        "unknown-render-type",
        "duplicated-render-identity",
        "generated-render-name",
        "unsupported-helm-hook",
        "pre-existing-object-even-with-matching-owner",
        "missing-native-uid",
        "wrong-helm-manager",
        "wrong-helm-release",
        "wrong-helm-release-namespace",
    ),
)
def test_agent_installation_rejects_one_changed_render_or_native_invariant(
    stage: Stage, path: tuple[str | int, ...], value: object, reason: str
) -> None:
    """Refuse one changed invariant without writing any Kubernetes object."""
    wire = json.loads(BASELINE)
    target = wire[stage]
    for key in path[:-1]:
        target = target[key]
    if path[-1] == len(IDENTITIES):
        target.append(wire[Stage.RENDER][FIRST_OBJECT])
    else:
        target[path[-1]] = (
            wire[Stage.AFTER][FIRST_OBJECT] if stage is Stage.BEFORE else value
        )

    service = create_autospec(INSTALL.KubernetesService, instance=True)
    if stage is Stage.RENDER:
        with pytest.raises(NATIVE_HELM.HelmError) as rejected:
            _inventory(wire)
        assert service.mock_calls == []
    else:
        resources = _inventory(wire)
        service.read.side_effect = wire[stage]
        with pytest.raises(INSTALL.KubernetesError) as rejected:
            if stage is Stage.BEFORE:
                INSTALL.verify_chart_absent(service, resources)
            else:
                INSTALL.observe_chart_resources(service, SELECTION_CASE, resources)
        assert tuple(service.mock_calls) == READS[:1]
    assert str(rejected.value) == reason
