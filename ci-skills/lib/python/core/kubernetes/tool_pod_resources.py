"""Render native resources for the reusable tool Pod capability.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, Protocol

from lib.python.core.json_types import JSONObject, copy_json_object
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
    """

    image: str
    fingerprint: str
    fingerprint_label: str
    mounts: Sequence[ToolPodMountSpec]
    node_selector: Mapping[str, str]


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


def tool_pod_document(scope: RunScope, spec: ToolPodResourceSpec) -> JSONObject:
    """Render the ci-skills-owned long-lived execution Pod.

    :param scope: Resolved run scope and ownership labels.
    :param spec: Validated image and specification fingerprint.
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
            "annotations": {SPEC_FINGERPRINT_ANNOTATION: spec.fingerprint},
        },
        "spec": pod_spec,
    }
