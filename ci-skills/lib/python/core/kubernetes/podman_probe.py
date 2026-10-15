"""Typed inputs and manifest rendering for a Podman readiness Job.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Optional

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.podman_evidence import (
    MARKER_PREFIX,
    PROBE_CONTAINER,
)
from lib.python.core.kubernetes.run_scope import RUN_ID_LABEL, RunScope
from lib.python.core.report import plan_digest
from lib.python.core.target import Target

PROBE_KIND: Final = "image_build_readiness"
NESTED_RUNTIME_CLAIM: Final = "nested_podman_runtime_readiness"
DEFAULT_PROBE_IMAGE: Final = (
    "quay.io/podman/stable@sha256:"
    "24aa8442c468c62ffae6053c79942b3a53c169934098a80b93f653fd9f0f8783"
)
DEFAULT_INNER_IMAGE: Final = (
    "docker.io/library/busybox@sha256:"
    "bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e"
)
DEFAULT_PROBE_TIMEOUT: Final = 300
DEFAULT_CPU_REQUEST: Final = "250m"
DEFAULT_CPU_LIMIT: Final = "1"
DEFAULT_MEMORY_REQUEST: Final = "256Mi"
DEFAULT_MEMORY_LIMIT: Final = "1Gi"
DEFAULT_STORAGE_SIZE: Final = "8Gi"
PROBE_TTL_SECONDS: Final = 600
PROBE_POLL_SECONDS: Final = 2
_IMAGE_DIGEST = re.compile(r"[^\s]+@sha256:[0-9a-f]{64}")


def immutable_image(value: str) -> str:
    """Require a complete SHA-256-pinned image reference.

    :param value: OCI image name supplied by the default or caller.
    :returns: The unchanged immutable reference.
    :raises ValueError: If the reference is empty, contains whitespace, or lacks a digest.
    """
    if _IMAGE_DIGEST.fullmatch(value) is None:
        raise ValueError("immutable_image_required")
    return value


def _quantity(value: str, field_name: str) -> str:
    """Require one nonempty Kubernetes resource quantity.

    :param value: Quantity supplied through the command interface.
    :param field_name: Stable field name used in a classified error.
    :returns: The stripped quantity for Kubernetes to validate natively.
    :raises ValueError: If the caller supplies an empty value.
    """
    if not value.strip():
        raise ValueError(f"{field_name}_invalid")
    return value.strip()


@dataclass(frozen=True)
class ProbeResources:
    """Bound resource requests and limits for the temporary probe Pod.

    .. attribute :: cpu_request
        :type: str

        Pod CPU request.

    .. attribute :: cpu_limit
        :type: str

        Pod CPU limit.

    .. attribute :: memory_request
        :type: str

        Pod memory request.

    .. attribute :: memory_limit
        :type: str

        Pod memory limit.

    .. attribute :: storage_size
        :type: str

        Size limit for the Podman storage ``emptyDir``.
    """

    cpu_request: str = DEFAULT_CPU_REQUEST
    cpu_limit: str = DEFAULT_CPU_LIMIT
    memory_request: str = DEFAULT_MEMORY_REQUEST
    memory_limit: str = DEFAULT_MEMORY_LIMIT
    storage_size: str = DEFAULT_STORAGE_SIZE

    def __post_init__(self) -> None:
        """Reject malformed resource quantities before rendering a Job.

        :raises ValueError: If any resource quantity is empty.
        """  # noqa: DOC502 - The shared quantity validator rejects empty values.
        object.__setattr__(
            self,
            "cpu_request",
            _quantity(self.cpu_request, "cpu_request"),
        )
        object.__setattr__(self, "cpu_limit", _quantity(self.cpu_limit, "cpu_limit"))
        object.__setattr__(
            self,
            "memory_request",
            _quantity(self.memory_request, "memory_request"),
        )
        object.__setattr__(
            self,
            "memory_limit",
            _quantity(self.memory_limit, "memory_limit"),
        )
        object.__setattr__(
            self,
            "storage_size",
            _quantity(self.storage_size, "storage_size"),
        )


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
    """

    target: Target
    node: Optional[str]
    probe_image: str
    inner_image: str
    timeout: int
    resources: ProbeResources
    mode: ProbeMode
    confirm_plan: Optional[str]

    def __post_init__(self) -> None:
        """Validate immutable images, node text, and the supported time bound.

        :raises ValueError: If an image, node, or timeout is invalid.
        """
        immutable_image(self.probe_image)
        immutable_image(self.inner_image)
        if self.node is not None and not self.node.strip():
            raise ValueError("node_invalid")
        if self.node is not None:
            object.__setattr__(self, "node", self.node.strip())
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
            "side_effects_on_apply": ["temporary_namespace", "temporary_job"],
            "claim": NESTED_RUNTIME_CLAIM,
        }

    def fingerprint(self, selected_node: str) -> str:
        """Fingerprint the live-selected worker and all operation inputs.

        :param selected_node: Concrete live worker, or ``auto`` offline.
        :returns: Canonical SHA-256 plan fingerprint.
        """
        return plan_digest(self.plan(selected_node))


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


PROBE_SCRIPT: Final = r"""set -eu
podman_cmd() {
  podman --root /var/lib/containers/storage \
    --runroot /run/containers/storage \
    --storage-driver overlay \
    --storage-opt= \
    --cgroup-manager cgroupfs "$@"
}
cleanup() {
  probe_rc="$1"
  trap - EXIT HUP INT TERM
  set +e
  podman_cmd rm -f "$OK_NAME" "$FAIL_NAME" >/dev/null 2>&1
  remaining_output="$(podman_cmd ps -aq --filter "label=$RUN_LABEL")"
  inventory_rc="$?"
  if [ "$inventory_rc" -ne 0 ]; then
    probe_rc=1
    remaining=inventory_failed
  else
    remaining="$(printf '%s' "$remaining_output" | tr '\n' ',')"
    if [ -n "$remaining" ]; then
      probe_rc=1
    else
      remaining=none
    fi
  fi
  printf '%s|remaining_containers|%s\n' "$MARKER_PREFIX" "$remaining"
  exit "$probe_rc"
}
trap 'cleanup $?' EXIT
trap 'cleanup 129' HUP
trap 'cleanup 130' INT
trap 'cleanup 143' TERM

printf '%s|kernel|%s\n' "$MARKER_PREFIX" "$(uname -sr)"
printf '%s|podman_version|%s\n' "$MARKER_PREFIX" "$(podman --version)"
storage_info="$(
  podman_cmd info \
    --format '{{.Store.GraphDriverName}}|{{.Store.GraphRoot}}|{{.Host.CgroupManager}}|{{.Host.CgroupsVersion}}'
)"
IFS='|' read -r storage_driver graph_root cgroup_manager cgroup_version <<EOF
$storage_info
EOF
mount_program="$(
  podman_cmd info \
    --format '{{index .Store.GraphOptions "overlay.mount_program"}}'
)"
if [ -z "$mount_program" ] || [ "$mount_program" = '<no value>' ]; then
  mount_program=none
fi
printf '%s|storage|driver=%s;mount_program=%s;cgroup_manager=%s;graph_root=%s;cgroup_version=%s\n' \
  "$MARKER_PREFIX" "$storage_driver" "$mount_program" "$cgroup_manager" \
  "$graph_root" "$cgroup_version"
printf '%s|backing_filesystem|%s\n' "$MARKER_PREFIX" "$(stat -f -c %T /var/lib/containers)"

inner="$(podman_cmd run --name "$OK_NAME" --label "$RUN_LABEL" \
  --memory 64m --cpus 0.25 --security-opt label=disable \
  -v /ci-skills/evidence:/evidence:rw "$INNER_IMAGE" sh -ceu '
    printf ready >/evidence/inner.txt
    printf "marker=inner-ok;cgroup="
    tr "\n" "," </proc/self/cgroup
    printf ";root="
    grep -m 1 " / " /proc/self/mountinfo
  ')"
inner_id="$(podman_cmd inspect --format '{{.Id}}' "$OK_NAME")"
printf '%s|inner_runtime|id=%s;%s\n' "$MARKER_PREFIX" "$inner_id" "$inner"
printf '%s|bind_readback|%s\n' "$MARKER_PREFIX" "$(cat /ci-skills/evidence/inner.txt)"

set +e
podman_cmd run --name "$FAIL_NAME" --label "$RUN_LABEL" \
  --memory 64m --cpus 0.25 --security-opt label=disable \
  "$INNER_IMAGE" sh -c 'exit 7'
failure_rc="$?"
set -e
printf '%s|expected_exit|%s\n' "$MARKER_PREFIX" "$failure_rc"
[ "$failure_rc" -eq 7 ]
"""


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
                    "nodeName": selected_node,
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "containers": [
                        {
                            "name": PROBE_CONTAINER,
                            "image": action.probe_image,
                            "imagePullPolicy": "IfNotPresent",
                            "securityContext": {
                                "privileged": True,
                                "runAsUser": 0,
                                "runAsNonRoot": False,
                            },
                            "command": ["/bin/sh", "-ceu", PROBE_SCRIPT],
                            "env": [
                                {"name": "INNER_IMAGE", "value": action.inner_image},
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
                            "resources": {
                                "requests": {
                                    "cpu": action.resources.cpu_request,
                                    "memory": action.resources.memory_request,
                                },
                                "limits": {
                                    "cpu": action.resources.cpu_limit,
                                    "memory": action.resources.memory_limit,
                                },
                            },
                            "volumeMounts": [
                                {
                                    "name": "containers-storage",
                                    "mountPath": "/var/lib/containers",
                                },
                                {
                                    "name": "containers-runroot",
                                    "mountPath": "/run/containers",
                                },
                                {
                                    "name": "evidence",
                                    "mountPath": "/ci-skills/evidence",
                                },
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "containers-storage",
                            "emptyDir": {"sizeLimit": action.resources.storage_size},
                        },
                        {"name": "containers-runroot", "emptyDir": {}},
                        {"name": "evidence", "emptyDir": {"sizeLimit": "16Mi"}},
                    ],
                },
            },
        },
    }
