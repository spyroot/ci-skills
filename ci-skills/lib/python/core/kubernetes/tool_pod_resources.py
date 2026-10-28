"""Render native resources for the reusable tool Pod capability.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Final, Protocol

from lib.python.core.json_types import (
    JSONObject,
    copy_json_object,
    json_object,
    json_objects,
)
from lib.python.core.kubernetes.run_scope import RunScope
from lib.python.core.kubernetes.script_resources import immutable_script_configmap
from lib.python.core.paths import SKILL_ROOT

TOOL_POD_ROLE: Final = "tool-pod"
TOOL_POD_CONTAINER: Final = "ci-skills-pod"
TOOL_POD_SCRIPT_ROLE: Final = "tool-pod-scripts"
TOOL_POD_SCRIPT_NAME: Final = "tool-pod-hold.sh"
TOOL_POD_SCRIPT_MOUNT: Final = "/ci-skills/scripts"
TOOL_POD_SCRIPT_VOLUME: Final = "ci-skills-scripts"
TOOL_POD_SCRIPT_SOURCES: Final = {
    TOOL_POD_SCRIPT_NAME: SKILL_ROOT / "lib/bash/kubernetes" / TOOL_POD_SCRIPT_NAME,
}
SPEC_FINGERPRINT_LABEL: Final = "ci-skills.spyroot.dev/spec-fingerprint"
SPEC_FINGERPRINT_ANNOTATION: Final = "ci-skills.spyroot.dev/spec-sha256"
FAILED_NODES_ANNOTATION: Final = "ci-skills.spyroot.dev/failed-mount-nodes"
TOOL_POD_MAX_ATTEMPTS: Final = 3


class ToolPodMountSpec(Protocol):
    """Narrow native mount inputs retained by the preparation action.

    .. attribute :: name
        :type: str

        Native volume and mount name.

    .. attribute :: volume_source
        :type: JSONObject

        Native volume-source fields passed through unchanged.

    .. attribute :: mount_path
        :type: str

        Absolute container mount path.

    .. attribute :: read_only
        :type: bool

        Native container mount access flag.
    """

    name: str
    volume_source: JSONObject
    mount_path: str
    read_only: bool


class ToolPodResourceSpec(Protocol):
    """Narrow rendering inputs supplied by the typed tool Pod action.

    .. attribute :: image
        :type: str

        Caller-selected execution image.

    .. attribute :: fingerprint
        :type: str

        Full action fingerprint binding tools and mounts.

    .. attribute :: fingerprint_label
        :type: str

        Label-safe action fingerprint.

    .. attribute :: mounts
        :type: Sequence[ToolPodMountSpec]

        Ordered native mounts selected by the internal consumer.

    .. attribute :: node_selector
        :type: Mapping[str, str]

        Native Pod placement labels selected by the internal consumer.

    .. attribute :: affinity
        :type: JSONObject

        Caller-selected native scheduling constraints and preferences.

    .. attribute :: tolerations
        :type: Sequence[JSONObject]

        Caller-selected native tolerations in declaration order.

    .. attribute :: preferred_node
        :type: str

        Optional producer node to prefer while preserving eligibility constraints.
    """

    image: str
    fingerprint: str
    fingerprint_label: str
    mounts: Sequence[ToolPodMountSpec]
    node_selector: Mapping[str, str]
    affinity: JSONObject
    tolerations: Sequence[JSONObject]
    preferred_node: str


def tool_pod_affinity(
    spec: ToolPodResourceSpec, excluded_nodes: Sequence[str] = ()
) -> JSONObject:
    """Compose soft producer preference and failed-node exclusion with native inputs.

    :param spec: Caller-owned native placement and optional producer preference.
    :param excluded_nodes: Actual nodes observed in prior failed mount attempts.
    :returns: Native affinity preserving every original required scheduling term.
    """
    affinity = copy_json_object(spec.affinity)
    if not spec.preferred_node and not excluded_nodes:
        return affinity
    node_affinity = copy_json_object(json_object(affinity.get("nodeAffinity", {})))
    if spec.preferred_node:
        preferences = list(
            json_objects(
                node_affinity.get("preferredDuringSchedulingIgnoredDuringExecution", [])
            )
        )
        preferences.append(
            {
                "weight": 100,
                "preference": {
                    "matchFields": [
                        {
                            "key": "metadata.name",
                            "operator": "In",
                            "values": [spec.preferred_node],
                        }
                    ]
                },
            }
        )
        node_affinity["preferredDuringSchedulingIgnoredDuringExecution"] = preferences
    if excluded_nodes:
        required_key = "requiredDuringSchedulingIgnoredDuringExecution"
        required = copy_json_object(json_object(node_affinity.get(required_key, {})))
        terms = json_objects(required.get("nodeSelectorTerms", [{}]))
        constrained = []
        for item in terms:
            term = copy_json_object(item)
            # An empty native term matches no nodes; adding a constraint must not widen it.
            if (
                term.get("matchExpressions")
                or term.get("matchFields")
                or required_key not in node_affinity
            ):
                term["matchFields"] = [
                    *json_objects(term.get("matchFields", [])),
                    {
                        "key": "metadata.name",
                        "operator": "NotIn",
                        "values": list(excluded_nodes),
                    },
                ]
            constrained.append(term)
        required["nodeSelectorTerms"] = constrained
        node_affinity[required_key] = required
    affinity["nodeAffinity"] = node_affinity
    return affinity


def tool_pod_script_configmap(scope: RunScope) -> JSONObject:
    """Render the checked long-lived process script as an immutable ConfigMap.

    :param scope: Run scope supplying the concrete namespace and labels.
    :returns: Native ConfigMap containing the installed script bytes.
    :raises OSError: If the installed script is absent or unreadable.
    :raises UnicodeError: If the script is not valid UTF-8.
    """  # noqa: DOC502 - shared script rendering propagates read failures.
    return immutable_script_configmap(
        scope,
        TOOL_POD_SCRIPT_ROLE,
        TOOL_POD_SCRIPT_SOURCES,
    )


def tool_pod_document(
    scope: RunScope,
    spec: ToolPodResourceSpec,
    *,
    excluded_nodes: Sequence[str] = (),
) -> JSONObject:
    """Render the ci-skills-owned long-lived execution Pod.

    :param scope: Resolved run scope and ownership labels.
    :param spec: Validated image and specification fingerprint.
    :param excluded_nodes: Nodes observed in classified prior mount failures.
    :returns: Native Pod document for Kubernetes validation.
    """
    volumes = [
        {
            "name": TOOL_POD_SCRIPT_VOLUME,
            "configMap": {
                "name": scope.name(TOOL_POD_SCRIPT_ROLE),
                "defaultMode": 0o555,
            },
        },
        *(
            {**copy_json_object(mount.volume_source), "name": mount.name}
            for mount in spec.mounts
        ),
    ]
    volume_mounts = [
        {
            "name": TOOL_POD_SCRIPT_VOLUME,
            "mountPath": TOOL_POD_SCRIPT_MOUNT,
            "readOnly": True,
        },
        *(
            {
                "name": mount.name,
                "mountPath": mount.mount_path,
                "readOnly": mount.read_only,
            }
            for mount in spec.mounts
        ),
    ]
    pod_spec: JSONObject = {
        "automountServiceAccountToken": False,
        "restartPolicy": "Never",
        "containers": [
            {
                "name": TOOL_POD_CONTAINER,
                "image": spec.image,
                "command": [
                    "/bin/sh",
                    f"{TOOL_POD_SCRIPT_MOUNT}/{TOOL_POD_SCRIPT_NAME}",
                ],
                "volumeMounts": volume_mounts,
            }
        ],
        "volumes": volumes,
    }
    if spec.node_selector:
        pod_spec["nodeSelector"] = dict(spec.node_selector)
    affinity = tool_pod_affinity(spec, excluded_nodes)
    if affinity:
        pod_spec["affinity"] = affinity
    if spec.tolerations:
        pod_spec["tolerations"] = [copy_json_object(item) for item in spec.tolerations]
    annotations = {SPEC_FINGERPRINT_ANNOTATION: spec.fingerprint}
    if excluded_nodes:
        annotations[FAILED_NODES_ANNOTATION] = json.dumps(
            list(excluded_nodes), separators=(",", ":")
        )
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": scope.name(TOOL_POD_ROLE),
            "namespace": scope.namespace,
            "labels": {
                **dict(scope.labels),
                SPEC_FINGERPRINT_LABEL: spec.fingerprint_label,
            },
            "annotations": annotations,
        },
        "spec": pod_spec,
    }
