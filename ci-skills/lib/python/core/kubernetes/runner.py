"""Provider-independent runner workload and image pull callbacks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Final, Optional

from lib.python.core.json_types import JSONDocument, JSONObject, JSONValue
from lib.python.core.kubernetes.kubernetes import K8SCallback, KubernetesError
from lib.python.core.kubernetes.podman_runtime import (
    PodmanRuntimeSettings,
    podman_runtime_readback,
)
from lib.python.core.kubernetes.resources import (
    MANAGER_LABEL,
    MANAGER_VALUE,
    OPERATION_LABEL,
    ResourceSet,
)
from lib.python.core.kubernetes.run_scope import RUN_ID_LABEL
from lib.python.core.runner import (
    PULL_JOB_SUFFIX,
    REGISTRATION_SECRET_SUFFIX,
    REGISTRY_SECRET_SUFFIX,
    RUNTIME_ROOT,
    RunnerAction,
    RunnerRuntime,
)
from lib.python.core.toolbox import DOCTOR_PATH
from lib.python.sys.status import APPLIED, NO_OP, VERIFIED

POD_API: Final = "v1"
DEPLOYMENT_API: Final = "apps/v1"
JOB_API: Final = "batch/v1"
PULL_SECRET_TYPE: Final = "kubernetes.io/dockerconfigjson"
PULL_SECRET_KEY: Final = ".dockerconfigjson"
WORK_VOLUME: Final = "runner-work"
RUNNER_CONTAINER: Final = "runner"
DOCTOR_COMMAND: Final = (DOCTOR_PATH,)


class DeployRunner(K8SCallback[RunnerAction]):
    """Deploy the preceding publication and provider-registration outputs."""

    def __init__(self, action: RunnerAction):
        """Retain required workload inputs and initialize resource ownership.

        :param action: Runtime namespace, workload identity, and readiness inputs.
        """
        super().__init__(action)
        self.resources: Optional[ResourceSet] = None

    def _secrets(self, resources: ResourceSet, runtime: RunnerRuntime) -> None:
        """Reconcile operation-owned registry and registration Secrets.

        :param resources: Shared resource journal for this workload operation.
        :param runtime: Private registration inputs supplied by the provider callback.
        :raises KubernetesError: If ownership, apply, or independent read-back fails.
        :raises TargetError: If the bound kubeconfig changes before a provider command.
        :raises OSError: If a native provider process or temporary apply file
            cannot be accessed.
        :raises AttributeError: If Harbor authentication did not populate the
            workflow state.
        """  # noqa: DOC502 - ResourceSet.apply propagates resource and target errors.
        labels = _operation_labels(self.action)
        resources.apply(
            registry_secret(
                self.action.name + REGISTRY_SECRET_SUFFIX,
                self.state.harbor.dockerconfig,
                labels,
            )
        )
        resources.apply(
            {
                "apiVersion": POD_API,
                "kind": "Secret",
                "metadata": {
                    "name": self.action.name + REGISTRATION_SECRET_SUFFIX,
                    "labels": labels,
                },
                "type": "Opaque",
                "data": {
                    key: base64.b64encode(value.encode()).decode()
                    for key, value in runtime.credential.items()
                },
            }
        )

    def _document(self, runtime: RunnerRuntime, image: str) -> JSONObject:
        """Build the selected Deployment from explicit runtime and image inputs.

        :param runtime: Registration and process commands from the provider producer.
        :param image: Verified immutable image from the publication producer.
        :returns: Deployment manifest without raw credential values.
        """
        action = self.action
        ownership = _operation_labels(action)
        labels = {"app": action.name, **ownership}
        work_mounts = [{"name": WORK_VOLUME, "mountPath": RUNTIME_ROOT}]
        mounts = list(work_mounts)
        volumes: list[JSONObject] = [{"name": WORK_VOLUME, "emptyDir": {}}]
        if action.podman is not None:
            mounts.extend(action.podman.volume_mounts())
            volumes.extend(action.podman.volumes())
        container: MutableMapping[str, JSONValue] = {
            "name": RUNNER_CONTAINER,
            "image": image,
            "imagePullPolicy": "IfNotPresent",
            "workingDir": RUNTIME_ROOT,
            "command": list(runtime.command),
            "volumeMounts": mounts,
        }
        if action.podman is not None:
            container.update(action.podman.container_fields())
        if runtime.readiness_command:
            container["readinessProbe"] = {
                "exec": {"command": list(runtime.readiness_command)}
            }
        init: MutableMapping[str, JSONValue] = {
            "name": "register",
            "image": image,
            "imagePullPolicy": "IfNotPresent",
            "workingDir": RUNTIME_ROOT,
            "command": list(runtime.init_command),
            "env": list(runtime.environment),
            "volumeMounts": mounts,
        }
        if action.podman is not None:
            init.update(action.podman.container_fields())
        pod_spec: MutableMapping[str, JSONValue] = {
            "automountServiceAccountToken": False,
            "imagePullSecrets": [{"name": action.name + REGISTRY_SECRET_SUFFIX}],
            "initContainers": [init],
            "containers": [container],
            "volumes": volumes,
        }
        if action.podman is not None:
            pod_spec.update(action.podman.pod_fields())
        return {
            "apiVersion": DEPLOYMENT_API,
            "kind": "Deployment",
            "metadata": {"name": action.name, "labels": ownership},
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": {"app": action.name}},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": pod_spec,
                },
            },
        }

    def run(self) -> JSONObject:
        """Reconcile owned resources and retain independently verified workload state.

        :returns: Applied/no-op workload identity, immutable image, and Pod evidence.
        :raises AttributeError: If declared provider or image producers did not run.
        :raises ValueError: If the publication output is not immutable.
        :raises KubernetesError: If apply, rollout, or image read-back fails.
        :raises TargetError: If the bound kubeconfig changes before a provider command.
        :raises OSError: If a native provider process or temporary apply file
            cannot be accessed.
        """  # noqa: DOC502 - State, resource, image, and workload readers propagate failures.
        action, runtime = self.action, self.state.runner_runtime
        image = published_image(self.state.published_image)
        resources = ResourceSet(self.kubernetes, action.namespace, action.operation)
        self.resources = resources
        self._secrets(resources, runtime)
        resources.apply(self._document(runtime, image))
        self.kubernetes.wait_rollout("deployment", action.name, action.namespace)
        pods = self.kubernetes.json(
            "-n",
            action.namespace,
            "get",
            "pods",
            "-l",
            f"app={action.name}",
            "-o",
            "json",
        )
        evidence = running_images(
            pods,
            image,
            podman=action.podman,
            container_name=RUNNER_CONTAINER,
        )
        changed = any(item["action"] == APPLIED for item in resources.evidence)
        result: JSONObject = {
            "action": APPLIED if changed else NO_OP,
            "verified": True,
            "namespace": action.namespace,
            "name": action.name,
            "image": image,
            "pods": evidence,
            "resources": resources.evidence,
        }
        self.state.deployment_result = result
        return result

    def cleanup(self) -> JSONObject:
        """Restore the workload's previous resource state after a failed workflow.

        :returns: Empty report evidence after recovery verification.
        :raises KubernetesError: If owned resource recovery or read-back fails.
        """  # noqa: DOC502 - ResourceSet.recover propagates failed recovery verification.
        if self.resources is not None and self.state.failed:
            self.resources.recover()
        return {}


class DeleteRunner(K8SCallback[RunnerAction]):
    """Remove the selected workload before removing its private runtime Secrets."""

    def run(self) -> JSONObject:
        """Delete exact owned resources and independently verify their absence.

        :returns: Applied/no-op deletion evidence for the selected workload.
        :raises KubernetesError: If ownership, deletion, or absence read-back fails.
        :raises TargetError: If the bound kubeconfig changes before a provider command.
        :raises OSError: If a native provider process or temporary apply file
            cannot be accessed.
        :raises AttributeError: If Kubernetes authentication did not populate
            the workflow state.
        """  # noqa: DOC502 - ResourceSet.delete propagates ownership and target failures.
        action = self.action
        resources = ResourceSet(self.kubernetes, action.namespace, action.operation)
        changed = resources.delete("Deployment", action.name)
        for suffix in (REGISTRATION_SECRET_SUFFIX, REGISTRY_SECRET_SUFFIX):
            changed = resources.delete("Secret", action.name + suffix) or changed
        return {
            "action": APPLIED if changed else NO_OP,
            "verified": True,
            "namespace": action.namespace,
            "name": action.name,
        }


class PullImage(K8SCallback[RunnerAction]):
    """Verify the selected immutable image through a temporary doctor Job."""

    def __init__(self, action: RunnerAction):
        """Retain the required target workload and own temporary resource tracking.

        :param action: Runtime namespace, operation name, and readiness deadline.
        """
        super().__init__(action)
        self.resources: Optional[ResourceSet] = None

    def _document(self, name: str, secret: str, image: str) -> JSONObject:
        """Build the bounded verification Job with private pull access.

        :param name: Exact temporary Job name owned by this action.
        :param secret: Name of this action's registry credential Secret.
        :param image: Immutable image selected by the preceding producer.
        :returns: Job manifest invoking the installed toolbox doctor.
        """
        labels = _operation_labels(self.action)
        return {
            "apiVersion": JOB_API,
            "kind": "Job",
            "metadata": {"name": name, "labels": labels},
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": self.action.timeout,
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "restartPolicy": "Never",
                        "automountServiceAccountToken": False,
                        "imagePullSecrets": [{"name": secret}],
                        "containers": [
                            {
                                "name": RUNNER_CONTAINER,
                                "image": image,
                                "command": list(DOCTOR_COMMAND),
                            }
                        ],
                    },
                },
            },
        }

    def run(self) -> JSONObject:
        """Require completed doctor execution and actual image digest evidence.

        :returns: Verified immutable image and successful Pod read-back.
        :raises AttributeError: If required authentication or image producers
            did not run.
        :raises ValueError: If the selected image is not immutable.
        :raises KubernetesError: If owned resource apply, doctor, or read-back fails.
        :raises TargetError: If the bound kubeconfig changes before a provider command.
        :raises OSError: If a native provider process or temporary apply file
            cannot be accessed.
        """  # noqa: DOC502 - Resource and image readers propagate their declared failures.
        action = self.action
        image = published_image(self.state.published_image)
        resources = ResourceSet(self.kubernetes, action.namespace, action.operation)
        self.resources = resources
        name = action.name + PULL_JOB_SUFFIX
        secret = name + REGISTRY_SECRET_SUFFIX
        resources.delete("Job", name)
        resources.apply(
            registry_secret(
                secret,
                self.state.harbor.dockerconfig,
                _operation_labels(action),
            )
        )
        resources.apply(self._document(name, secret, image))
        self.kubernetes.wait_job(name, action.namespace)
        pods = self.kubernetes.json(
            "-n",
            action.namespace,
            "get",
            "pods",
            "-l",
            f"job-name={name}",
            "-o",
            "json",
        )
        return {
            "action": VERIFIED,
            "verified": True,
            "image": image,
            "pods": running_images(pods, image, completed=True),
        }

    def cleanup(self) -> JSONObject:
        """Delete the exact temporary Job and pull Secret on every exit.

        :returns: Empty evidence after independent resource absence verification.
        :raises KubernetesError: If owned temporary resources cannot be removed.
        """  # noqa: DOC502 - cleanup_owned propagates aggregate cleanup failures.
        if self.resources is not None:
            name = self.action.name + PULL_JOB_SUFFIX
            self.resources.cleanup_owned(
                (("Job", name), ("Secret", name + REGISTRY_SECRET_SUFFIX))
            )
        return {}


def published_image(image: str) -> str:
    """Validate a producer's immutable image reference.

    :param image: Image reference supplied by the preceding typed state producer.
    :returns: The same immutable image reference.
    :raises ValueError: If the image is not pinned to a SHA-256 digest.
    """
    if not image or "@sha256:" not in image:
        raise ValueError("immutable_image_required")
    return image


def registry_secret(
    name: str,
    dockerconfig: bytes,
    labels: Optional[Mapping[str, str]] = None,
) -> JSONObject:
    """Build a native pull/push Secret from resolved private credentials.

    :param name: Exact operation-selected resource name.
    :param dockerconfig: Private native configuration bytes from Harbor authentication.
    :param labels: Optional run labels used by scope discovery.
    :returns: Internal Secret manifest; its encoded data is never reportable.
    """
    return {
        "apiVersion": POD_API,
        "kind": "Secret",
        "metadata": {"name": name, "labels": dict(labels or {})},
        "type": PULL_SECRET_TYPE,
        "data": {PULL_SECRET_KEY: base64.b64encode(dockerconfig).decode()},
    }


def running_images(
    document: JSONDocument,
    image: str,
    completed: bool = False,
    *,
    podman: Optional[PodmanRuntimeSettings] = None,
    container_name: str = RUNNER_CONTAINER,
) -> Sequence[JSONObject]:
    """Read actual container image and state from a native PodList.

    :param document: Provider PodList read-back requiring shape validation.
    :param image: Expected digest-pinned image selected by this operation.
    :param completed: Whether successful termination proves a doctor Job's result.
    :param podman: Optional exact runtime settings to verify on each selected Pod.
    :param container_name: Container whose Podman runtime must be verified.
    :returns: Safe Pod names and observed image IDs matching the expected digest.
    :raises KubernetesError: If the PodList is malformed or no image/state is verified.
    """
    items = document.get("items") if isinstance(document, Mapping) else None
    if not isinstance(items, list):
        raise KubernetesError("pod_list_invalid")
    digest = image.rsplit("@", 1)[-1]
    evidence: list[JSONObject] = []
    for pod in items:
        if not isinstance(pod, Mapping):
            raise KubernetesError("pod_list_invalid")
        metadata, status = pod.get("metadata", {}), pod.get("status", {})
        if not isinstance(metadata, Mapping) or not isinstance(status, Mapping):
            raise KubernetesError("pod_list_invalid")
        if metadata.get("deletionTimestamp"):
            continue
        statuses = status.get("containerStatuses", [])
        if not isinstance(statuses, list):
            raise KubernetesError("pod_list_invalid")
        for container in statuses:
            if not isinstance(container, Mapping):
                raise KubernetesError("pod_list_invalid")
            state = container.get("state", {})
            if not isinstance(state, Mapping):
                continue
            terminated = state.get("terminated", {})
            healthy = (
                isinstance(terminated, Mapping) and terminated.get("exitCode") == 0
                if completed
                else bool(state.get("running"))
            )
            image_id, name = container.get("imageID"), metadata.get("name")
            if (
                container.get("name") == RUNNER_CONTAINER
                and healthy
                and isinstance(image_id, str)
                and image_id.endswith(digest)
                and isinstance(name, str)
                and name
            ):
                row: JSONObject = {"name": name, "image_id": image_id}
                if podman is not None:
                    row["podman"] = podman_runtime_readback(
                        pod,
                        podman,
                        container_name,
                        init_container_name="register",
                    )
                evidence.append(row)
    if not evidence:
        raise KubernetesError("running_image_readback_missing")
    return evidence


def _operation_labels(action: RunnerAction) -> Mapping[str, str]:
    """Render run labels only when a caller supplied an operation identity.

    :param action: Runner workload with an optional run-scope identity.
    :returns: Empty labels for legacy consumers, or exact run ownership labels.
    """
    if action.ownership_id is None:
        return {}
    return {
        MANAGER_LABEL: MANAGER_VALUE,
        OPERATION_LABEL: action.ownership_id,
        RUN_ID_LABEL: action.ownership_id,
    }
