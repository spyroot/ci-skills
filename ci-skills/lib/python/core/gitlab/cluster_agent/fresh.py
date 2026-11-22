"""Execute a freshly confirmed Agent install with exact namespace ownership."""

from __future__ import annotations

import json
from typing import Final

from lib.python.core.cli_api import RequestDeadline, response_decoder
from lib.python.core.gitlab.cluster_agent.authority import AgentAuthorities
from lib.python.core.gitlab.cluster_agent.connect import connect_fresh_agent
from lib.python.core.gitlab.cluster_agent.connection import AgentConnectivity
from lib.python.core.gitlab.cluster_agent.plan import (
    FreshConnectPlan,
    prepare_fresh_connect,
)
from lib.python.core.gitlab.cluster_agent.recovery import (
    CREDENTIAL,
    CREDENTIAL_ID,
    CREDENTIAL_NAME,
    CREDENTIAL_RESOURCE,
    CREDENTIAL_RESOURCE_NAME,
    CREDENTIAL_RESOURCE_UID,
    PREEXISTING_CREDENTIAL_IDS,
    agent_recovery_name,
)
from lib.python.core.gitlab.cluster_agent.registration import observed_resource
from lib.python.core.gitlab.cluster_agent.secret import agent_secret_name
from lib.python.core.gitlab.cluster_agent.selection import (
    APP_VERSION,
    CHART_SHA256,
    CHART_VERSION,
    ConnectSelection,
)
from lib.python.core.helm.native import VerifiedChart
from lib.python.core.json_types import JSONObject, copy_json_object, json_object
from lib.python.core.kubernetes.gitlab_agent import (
    read_agent_cluster_scope,
    read_agent_cluster_uid,
)
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.kubernetes.run_scope import namespace_document
from lib.python.core.oci import ImageManifestIdentity
from lib.python.core.schema import validate_specification

_OWNERSHIP_SCHEMA: Final = "gitlab-agent-ownership.schema.json"


def _owned_namespace_uid(
    raw: JSONObject | None, namespace: str, expected_labels: JSONObject
) -> str:
    if raw is None:
        raise KubernetesError("agent_namespace_create_unconfirmed")
    item = KubernetesObject(raw)
    try:
        metadata = item.metadata
        if (
            item.text("apiVersion") != "v1"
            or item.text("kind") != "Namespace"
            or metadata.text("name") != namespace
            or metadata.text("deletionTimestamp") is not None
            or any(
                metadata.object("labels").text(key) != expected
                for key, expected in expected_labels.items()
            )
        ):
            raise KubernetesError("agent_namespace_create_identity_mismatch")
        return metadata.required_text("uid")
    except TypeError:
        raise KubernetesError("agent_namespace_create_unconfirmed") from None


def _confirmed_namespace(
    authorities: AgentAuthorities,
    selection: ConnectSelection,
    plan: FreshConnectPlan,
    deadline: RequestDeadline,
) -> str:
    kubernetes = authorities.kubernetes
    cluster_uid = authorities.cluster_uid
    scope = selection.scope
    if not scope.owns_namespace:
        actual_cluster, observed_uid = deadline.call(
            read_agent_cluster_scope, kubernetes, scope.namespace
        )
        if actual_cluster != cluster_uid or observed_uid != plan.namespace_uid:
            raise KubernetesError("agent_plan_namespace_changed")
        return observed_uid
    if plan.namespace_uid is not None:
        raise KubernetesError("agent_plan_namespace_collision")
    if deadline.call(read_agent_cluster_uid, kubernetes) != cluster_uid:
        raise KubernetesError("agent_plan_cluster_changed")

    document = scope.cluster_resources(kubernetes).managed(
        copy_json_object(namespace_document(scope))
    )
    labels = json_object(json_object(document["metadata"])["labels"])
    result = kubernetes.command(
        "create",
        "--raw",
        "/api/v1/namespaces",
        "-f",
        "-",
        input_data=json.dumps(document).encode("utf-8"),
        timeout=deadline.remaining(),
    )
    if result.returncode:
        raise KubernetesError("agent_namespace_create_unconfirmed")
    try:
        returned = json_object(response_decoder().decode(result.stdout))
    except (TypeError, ValueError, RecursionError):
        raise KubernetesError("agent_namespace_create_unconfirmed") from None
    uid = _owned_namespace_uid(returned, scope.namespace, labels)
    observed = kubernetes.read(
        "Namespace", scope.namespace, "", timeout=deadline.remaining()
    )
    if _owned_namespace_uid(observed, scope.namespace, labels) != uid:
        raise KubernetesError("agent_namespace_create_identity_mismatch")
    return uid


def _pending_document(
    authorities: AgentAuthorities,
    selection: ConnectSelection,
    namespace_uid: str,
    image: ImageManifestIdentity,
) -> JSONObject:
    scope = selection.scope
    credential_name = agent_secret_name(selection)
    document: JSONObject = {
        "schema_version": "1.0",
        "kind": "gitlab_agent_ownership",
        "run_id": scope.run_id,
        "origin": authorities.gitlab.session.origin.rstrip("/"),
        "project_id": selection.project.id,
        "project_path": selection.project.path,
        "agent_id": None,
        "agent_name": selection.agent_name,
        CREDENTIAL_ID: None,
        CREDENTIAL_NAME: credential_name,
        PREEXISTING_CREDENTIAL_IDS: [],
        "cluster_uid": authorities.cluster_uid,
        "namespace": scope.namespace,
        "namespace_uid": namespace_uid,
        CREDENTIAL_RESOURCE_NAME: credential_name,
        CREDENTIAL_RESOURCE_UID: None,
        "ownership_record_name": agent_recovery_name(selection),
        "ownership_record_uid": None,
        "release": selection.release,
        "release_revision": None,
        "release_storage_name": None,
        "release_storage_uid": None,
        "chart_version": CHART_VERSION,
        "chart_sha256": CHART_SHA256,
        "app_version": APP_VERSION,
        "image_digest": image.digest,
        "created": {
            "agent": None,
            CREDENTIAL: None,
            "namespace": scope.owns_namespace,
            CREDENTIAL_RESOURCE: None,
            "release": None,
            "ownership_record": True,
        },
        "state": "PENDING",
        "resources": [
            observed_resource(
                "Namespace",
                scope.namespace,
                "",
                namespace_uid,
                created=scope.owns_namespace,
            )
        ],
    }
    validate_specification(document, _OWNERSHIP_SCHEMA)
    return document


def confirm_and_connect_fresh(
    authorities: AgentAuthorities,
    selection: ConnectSelection,
    chart: VerifiedChart,
    image: ImageManifestIdentity,
    *,
    confirmation: str,
    deadline: RequestDeadline,
) -> AgentConnectivity:
    """Reobserve and confirm the complete fresh plan before the first write.

    :param authorities: Verified GitLab, Kubernetes and installed candidate.
    :param selection: Exact project and run identities selected by the caller.
    :param chart: Pinned chart archive retained for this invocation.
    :param image: Runtime-verified immutable image identity.
    :param confirmation: Digest supplied from the earlier read-only plan.
    :param deadline: Absolute operation deadline shared with native reads.
    :returns: Durable connected recovery state and independently correlated proof.
    :raises ValueError: The caller's digest differs from the newly observed plan.
    :raises KubernetesError: Cluster, namespace, or installation identity is uncertain.
    """  # noqa: DOC502,DOC503 - Native planning and lifecycle errors propagate.
    plan = prepare_fresh_connect(
        authorities, selection, chart, image, deadline=deadline
    )
    plan.require_confirmation(confirmation)
    uid = _confirmed_namespace(authorities, selection, plan, deadline)
    pending = _pending_document(authorities, selection, uid, image)
    return connect_fresh_agent(
        authorities.gitlab,
        authorities.kubernetes,
        selection,
        pending,
        authorities.kas,
        chart,
        image,
        timeout=deadline.remaining(),
    )
