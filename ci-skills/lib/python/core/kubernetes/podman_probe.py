"""Typed inputs and manifest rendering for a Podman readiness Job.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Optional

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.kubernetes import podman_runtime
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.podman_evidence import (
    EXPECTED_INNER_EXIT,
    MARKER_PREFIX,
    PROBE_CONTAINER,
)
from lib.python.core.kubernetes.podman_runtime import (
    DEFAULT_SERVICE_ACCOUNT,
    PRIVILEGED_CONSTRAINT,
    PodmanRuntimeSettings,
)
from lib.python.core.kubernetes.podman_runtime import (
    PodmanResources as ProbeResources,
)
from lib.python.core.kubernetes.run_scope import (
    RUN_ID_LABEL,
    RunScope,
    validate_namespace,
)
from lib.python.core.kubernetes.scc_access import SCCAccessAction
from lib.python.core.paths import SKILL_ROOT
from lib.python.core.report import plan_digest
from lib.python.core.target import Target

DEFAULT_CPU_REQUEST: Final = podman_runtime.DEFAULT_CPU_REQUEST
DEFAULT_CPU_LIMIT: Final = podman_runtime.DEFAULT_CPU_LIMIT
DEFAULT_MEMORY_REQUEST: Final = podman_runtime.DEFAULT_MEMORY_REQUEST
DEFAULT_MEMORY_LIMIT: Final = podman_runtime.DEFAULT_MEMORY_LIMIT
DEFAULT_STORAGE_SIZE: Final = podman_runtime.DEFAULT_STORAGE_SIZE
PROBE_KIND: Final = "image_build_readiness"
NESTED_RUNTIME_CLAIM: Final = "nested_podman_runtime_readiness"
DEFAULT_PROBE_IMAGE: Final = (
    "quay.io/podman/stable@sha256:"
    "5118bdbb154b115d77f58107a2e69d65df4375bcb0d89a8da10b055754e8b9f3"
)
DEFAULT_INNER_IMAGE: Final = (
    "docker.io/library/busybox@sha256:"
    "bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e"
)
DEFAULT_PROBE_TIMEOUT: Final = 300
PROBE_TTL_SECONDS: Final = 600
PROBE_POLL_SECONDS: Final = 2
PROBE_SCRIPT_ROOT: Final = SKILL_ROOT / "lib/bash/kubernetes"
PROBE_MOUNT: Final = "/ci-skills/scripts"
PROBE_VOLUME: Final = "probe-scripts"
PROBE_EVIDENCE_MOUNT: Final = "/ci-skills/evidence"
PROBE_SCRIPT_NAME: Final = "probe.bash"
INNER_SCRIPT_NAME: Final = "inner.sh"
PROBE_CONFIGMAP_ROLE: Final = "podman-scripts"
PROBE_SCRIPT_SOURCES: Final = {
    PROBE_SCRIPT_NAME: PROBE_SCRIPT_ROOT / PROBE_SCRIPT_NAME,
    INNER_SCRIPT_NAME: PROBE_SCRIPT_ROOT / INNER_SCRIPT_NAME,
    "runtime.bash": SKILL_ROOT / "lib/bash/core/runtime.bash",
    "bash_runtime.bash": SKILL_ROOT / "lib/bash/core/bash_runtime.bash",
}
_IMAGE_DIGEST = re.compile(r"[^\s]+@sha256:[0-9a-f]{64}")


def immutable_image(value: str) -> str:
    """Require a complete SHA-256-pinned image reference.

    :param value: OCI image name supplied by the default or caller.
    :returns: The unchanged immutable reference.
    :raises ValueError: If the reference is empty, contains whitespace, or lacks
        a digest.
    """
    if _IMAGE_DIGEST.fullmatch(value) is None:
        raise ValueError("immutable_image_required")
    return value


class ProbeMode(StrEnum):
    """Closed execution modes for planning or running the probe.

    .. attribute :: DRY_RUN

        Offline manifest planning with no API access.

    .. attribute :: LIVE_PLAN

        Live worker selection and confirmable plan generation.

    .. attribute :: APPLY

        Confirmed resource creation and runtime verification.
    """

    DRY_RUN = "dry-run"
    LIVE_PLAN = "live-plan"
    APPLY = "apply"


@dataclass(frozen=True)
class PodmanProbeAction(SkillAction):
    """Inputs for one live plan or disposable Podman runtime probe.

    .. attribute :: target
        :type: Target

        Resolved Kubernetes-only runtime target.

    .. attribute :: node
        :type: Optional[str]

        Requested worker name, or ``None`` for live discovery.

    .. attribute :: probe_image
        :type: str

        Digest-pinned image providing Podman.

    .. attribute :: inner_image
        :type: str

        Digest-pinned image run by nested Podman.

    .. attribute :: timeout
        :type: int

        Overall Job deadline in seconds.

    .. attribute :: resources
        :type: ProbeResources

        Bounded temporary Pod resource settings.

    .. attribute :: mode
        :type: ProbeMode

        Offline plan, live plan, or confirmed apply.

    .. attribute :: confirm_plan
        :type: Optional[str]

        Exact live-plan digest required by apply.

    .. attribute :: namespace
        :type: Optional[str]

        Existing caller-selected namespace, or ``None`` for a generated scope.

    .. attribute :: service_account
        :type: str

        Fixed default account selected by the CI13 execution contract.
    """

    target: Target
    node: Optional[str]
    probe_image: str
    inner_image: str
    timeout: int
    resources: ProbeResources
    mode: ProbeMode
    confirm_plan: Optional[str]
    namespace: Optional[str]
    service_account: str = field(init=False, default=DEFAULT_SERVICE_ACCOUNT)

    def __post_init__(self) -> None:
        """Validate immutable images, selected names, and the supported time bound.

        :raises ValueError: If an image, node, namespace, or timeout is invalid.
        """
        immutable_image(self.probe_image)
        immutable_image(self.inner_image)
        if self.node is not None and not self.node.strip():
            raise ValueError("node_invalid")
        if self.node is not None:
            object.__setattr__(self, "node", self.node.strip())
        if self.namespace is not None:
            object.__setattr__(
                self,
                "namespace",
                validate_namespace(self.namespace.strip()),
            )
        if self.timeout <= 0:
            raise ValueError("timeout_must_be_positive")
        if self.mode == ProbeMode.APPLY and self.confirm_plan is None:
            raise ValueError("confirm_plan_required")
        if self.mode != ProbeMode.APPLY and self.confirm_plan is not None:
            raise ValueError("confirm_plan_requires_apply")

    def plan(self, selected_node: str) -> JSONObject:
        """Build the canonical nonsecret plan for one selected worker.

        :param selected_node: Concrete live worker, or ``auto`` for an offline plan.
        :returns: Plan fields bound by confirmation before mutation.
        :raises ValueError: If the resolved target lacks Kubernetes configuration.
        """
        kubernetes = self.target.kubernetes
        if kubernetes is None:
            raise ValueError("kubernetes_target_missing")
        permission = SCCAccessAction(
            constraint=PRIVILEGED_CONSTRAINT,
            service_account=self.service_account,
        )
        side_effects = [
            "temporary_scc_role_binding_when_required",
            "temporary_configmap",
            "temporary_job",
        ]
        if self.namespace is None:
            side_effects.insert(0, "temporary_namespace")
        return {
            "kind": PROBE_KIND,
            "operation": "probe",
            "target": {
                "context": kubernetes.context,
                "server": kubernetes.server,
            },
            "selected_node": selected_node,
            "probe_image": self.probe_image,
            "inner_image": self.inner_image,
            "timeout_seconds": self.timeout,
            "resources": {
                "cpu_request": self.resources.cpu_request,
                "cpu_limit": self.resources.cpu_limit,
                "memory_request": self.resources.memory_request,
                "memory_limit": self.resources.memory_limit,
                "storage_size": self.resources.storage_size,
            },
            "script_digest": plan_digest(probe_scripts()),
            "run_scope": {
                "namespace_mode": (
                    "selected" if self.namespace is not None else "generated"
                ),
                "namespace": self.namespace,
                "service_account": self.service_account,
            },
            "authorization": permission.plan(),
            "side_effects_on_apply": side_effects,
            "claim": NESTED_RUNTIME_CLAIM,
        }

    def fingerprint(self, selected_node: str) -> str:
        """Fingerprint the live-selected worker and all operation inputs.

        :param selected_node: Concrete live worker, or ``auto`` offline.
        :returns: Canonical SHA-256 plan fingerprint.
        """
        return plan_digest(self.plan(selected_node))

    def scc_access(self) -> SCCAccessAction:
        """Build the reusable SCC preparation action for this probe.

        :returns: Privileged SCC input for the probe's fixed ServiceAccount.
        """
        return SCCAccessAction(
            constraint=PRIVILEGED_CONSTRAINT,
            service_account=self.service_account,
        )


@dataclass(frozen=True)
class ReadyWorker:
    """Node identity and build-relevant details retained from one NodeList read.

    .. attribute :: name
        :type: str

        Ready schedulable worker name.

    .. attribute :: kernel_version
        :type: str

        Kernel version reported by ``status.nodeInfo.kernelVersion``.

    .. attribute :: os_image
        :type: str

        Operating-system image reported by ``status.nodeInfo.osImage``.
    """

    name: str
    kernel_version: str
    os_image: str

    def evidence(self) -> JSONObject:
        """Render native Node information for the shared result.

        :returns: Node name, kernel version, and operating-system image.
        """
        return {
            "name": self.name,
            "kernelVersion": self.kernel_version,
            "osImage": self.os_image,
        }


def select_ready_worker(document: JSONValue, requested: Optional[str]) -> ReadyWorker:
    """Select one deterministic Ready, schedulable worker from a NodeList.

    :param document: Kubernetes NodeList returned by the verified service.
    :param requested: Exact requested node, or ``None`` for sorted discovery.
    :returns: Requested eligible node evidence or the first eligible worker by name.
    :raises KubernetesError: If the response is malformed or no eligible node exists.
    """
    items = document.get("items") if isinstance(document, Mapping) else None
    if not isinstance(items, list):
        raise KubernetesError("node_list_invalid")
    eligible = sorted(
        (
            worker
            for item in items
            if isinstance(item, Mapping) and (worker := _ready_worker(item)) is not None
        ),
        key=lambda worker: worker.name,
    )
    if requested is not None:
        matching = [worker for worker in eligible if worker.name == requested]
        if not matching:
            raise KubernetesError("requested_node_not_ready_worker")
        return matching[0]
    if not eligible:
        raise KubernetesError("ready_worker_missing")
    return eligible[0]


def _ready_worker(node: Mapping[str, JSONValue]) -> Optional[ReadyWorker]:
    """Return one eligible worker and its native Node information.

    :param node: Native Node object from a NodeList.
    :returns: Evidence when Ready, schedulable, and a worker; otherwise ``None``.
    :raises KubernetesError: If an eligible worker omits required node information.
    """
    metadata, spec, status = node.get("metadata"), node.get("spec"), node.get("status")
    if not all(isinstance(value, Mapping) for value in (metadata, spec, status)):
        raise KubernetesError("node_record_invalid")
    assert isinstance(metadata, Mapping)
    assert isinstance(spec, Mapping)
    assert isinstance(status, Mapping)
    name, labels, conditions = (
        metadata.get("name"),
        metadata.get("labels", {}),
        status.get("conditions", []),
    )
    unschedulable = spec.get("unschedulable", False)
    if (
        not isinstance(name, str)
        or not name
        or not isinstance(labels, Mapping)
        or not isinstance(conditions, list)
        or any(not isinstance(condition, Mapping) for condition in conditions)
        or not isinstance(unschedulable, bool)
    ):
        raise KubernetesError("node_record_invalid")
    if unschedulable:
        return None
    control_labels = {
        "node-role.kubernetes.io/control-plane",
        "node-role.kubernetes.io/master",
    }
    worker = "node-role.kubernetes.io/worker" in labels or not (
        control_labels & set(labels)
    )
    ready = any(
        isinstance(condition, Mapping)
        and condition.get("type") == "Ready"
        and condition.get("status") == "True"
        for condition in conditions
    )
    if not worker or not ready:
        return None
    node_info = status.get("nodeInfo")
    if not isinstance(node_info, Mapping):
        raise KubernetesError("node_info_missing")
    kernel_version, os_image = (
        node_info.get("kernelVersion"),
        node_info.get("osImage"),
    )
    if (
        not isinstance(kernel_version, str)
        or not kernel_version
        or not isinstance(os_image, str)
        or not os_image
    ):
        raise KubernetesError("node_info_invalid")
    return ReadyWorker(name, kernel_version, os_image)


def probe_scripts() -> JSONObject:
    """Read the checked script bundle shipped by this installed skill.

    :returns: ConfigMap file names mapped to their exact UTF-8 source text.
    :raises OSError: If an installed script is absent or unreadable.
    :raises UnicodeError: If a script is not valid UTF-8.
    """  # noqa: DOC502 - Path.read_text propagates file and decoding errors.
    return {
        name: path.read_text(encoding="utf-8")
        for name, path in PROBE_SCRIPT_SOURCES.items()
    }


def probe_configmap(scope: RunScope) -> JSONObject:
    """Mount the installed script bundle as one run-owned, immutable ConfigMap.

    :param scope: Generated run namespace, names, and ownership labels.
    :returns: ConfigMap document consumed read-only by the probe Job.
    :raises OSError: If an installed script is absent or unreadable.
    :raises UnicodeError: If a script is not valid UTF-8.
    """  # noqa: DOC502 - The script reader propagates file and decoding errors.
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": scope.name(PROBE_CONFIGMAP_ROLE),
            "namespace": scope.namespace,
            "labels": dict(scope.labels),
        },
        "immutable": True,
        "data": probe_scripts(),
    }


def probe_job(
    action: PodmanProbeAction,
    scope: RunScope,
    selected_node: str,
) -> JSONObject:
    """Render the temporary privileged root Podman Job.

    :param action: Validated immutable images, deadline, and resources.
    :param scope: Generated run namespace, names, and labels.
    :param selected_node: Live-read Ready worker name.
    :returns: Complete ``batch/v1`` Job manifest.
    """
    name = scope.name("podman-probe")
    labels = dict(scope.labels)
    labels["app.kubernetes.io/name"] = PROBE_KIND
    runtime = PodmanRuntimeSettings(
        selected_node,
        action.service_account,
        action.resources,
    )
    pod_fields = runtime.pod_fields()
    container_fields = runtime.container_fields()
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": scope.namespace, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": action.timeout,
            "ttlSecondsAfterFinished": PROBE_TTL_SECONDS,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    **pod_fields,
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": PROBE_CONTAINER,
                            "image": action.probe_image,
                            "imagePullPolicy": "IfNotPresent",
                            **container_fields,
                            "command": [
                                "/bin/bash",
                                f"{PROBE_MOUNT}/{PROBE_SCRIPT_NAME}",
                            ],
                            "env": [
                                {"name": "INNER_IMAGE", "value": action.inner_image},
                                {"name": "PROBE_RUNTIME_DIR", "value": PROBE_MOUNT},
                                {"name": "EVIDENCE_DIR", "value": PROBE_EVIDENCE_MOUNT},
                                {
                                    "name": "EXPECTED_INNER_EXIT",
                                    "value": str(EXPECTED_INNER_EXIT),
                                },
                                {"name": "OK_NAME", "value": scope.name("inner-ok")},
                                {
                                    "name": "FAIL_NAME",
                                    "value": scope.name("inner-fail"),
                                },
                                {
                                    "name": "RUN_LABEL",
                                    "value": f"{RUN_ID_LABEL}={scope.run_id}",
                                },
                                {"name": "MARKER_PREFIX", "value": MARKER_PREFIX},
                            ],
                            "volumeMounts": [
                                {
                                    "name": PROBE_VOLUME,
                                    "mountPath": PROBE_MOUNT,
                                    "readOnly": True,
                                },
                                *runtime.volume_mounts(),
                                {
                                    "name": "evidence",
                                    "mountPath": PROBE_EVIDENCE_MOUNT,
                                },
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": PROBE_VOLUME,
                            "configMap": {
                                "name": scope.name(PROBE_CONFIGMAP_ROLE),
                                "defaultMode": 0o444,
                            },
                        },
                        *runtime.volumes(),
                        {"name": "evidence", "emptyDir": {"sizeLimit": "16Mi"}},
                    ],
                },
            },
        },
    }
