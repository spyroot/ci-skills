"""Provider-independent runner workload and image pull callbacks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any, Final, cast

from .action import AbstractCallback, K8SCallback
from .kubernetes import KubernetesError, KubernetesService
from .resources import ResourceSet
from .status import APPLIED, NO_OP, VERIFIED
from .toolbox import DOCTOR_PATH

POD_API: Final = "v1"
DEPLOYMENT_API: Final = "apps/v1"
JOB_API: Final = "batch/v1"
PULL_SECRET_TYPE: Final = "kubernetes.io/dockerconfigjson"
PULL_SECRET_KEY: Final = ".dockerconfigjson"
WORK_VOLUME: Final = "runner-work"
RUNTIME_ROOT: Final = "/ci-skills/runner"
RUNNER_CONTAINER: Final = "runner"
REGISTRY_SECRET_SUFFIX: Final = "-registry"
REGISTRATION_SECRET_SUFFIX: Final = "-registration"
DOCTOR_COMMAND: Final = [DOCTOR_PATH]


@dataclass(frozen=True)
class RunnerRuntime:
    """Provider-selected bootstrap, process, and private registration material."""

    init_command: tuple[str, ...]
    command: tuple[str, ...]
    environment: tuple[dict[str, Any], ...]
    readiness_command: tuple[str, ...]
    credential: dict[str, str] = field(repr=False)


@dataclass(frozen=True)
class RunnerAction:
    """Caller-selected workload identity and runtime inputs."""

    namespace: str
    name: str
    source_root: str
    labels: tuple[str, ...]
    timeout: int


class DeployRunner(K8SCallback[RunnerAction, dict[str, Any]]):
    """Deploy any provider's runner runtime using a published toolbox image."""

    def __init__(
        self,
        *,
        action: RunnerAction,
        image: str | AbstractCallback[Any],
        runtime: AbstractCallback[RunnerRuntime],
        dockerconfig: AbstractCallback[Any],
    ) -> None:
        """Keep explicit upstream values without invoking their operations.

        :param action: Selected runner workload inputs.
        :param image: Immutable image or completed publication callback.
        :param runtime: Provider callback producing its bootstrap and credentials.
        :param dockerconfig: Auth callback producing native registry credentials.
        """
        super().__init__(action=action)
        self.image = image
        self.runtime = runtime
        self.registry = dockerconfig
        self.resources: ResourceSet | None = None

    def run(self) -> dict[str, Any]:
        """Reconcile Secrets and workload, then prove the running image.

        :returns: Safe workload and Pod identity with applied/no-op status.
        :raises ValueError: If a required upstream callback result is missing.
        :raises KubernetesError: If apply, rollout, or image read-back fails.
        """
        action = self.action
        runtime = self.runtime.result
        if action is None or runtime is None or self.registry.result is None:
            raise ValueError("runner_dependencies_missing")
        image = published_image(self.image)
        service = cast(KubernetesService, self.kubernetes)
        self.resources = ResourceSet(service, action.namespace, action.name)
        registry_name = action.name + REGISTRY_SECRET_SUFFIX
        access_name = action.name + REGISTRATION_SECRET_SUFFIX
        self.resources.apply(
            registry_secret(registry_name, self.registry.result.dockerconfig)
        )
        self.resources.apply(
            {
                "apiVersion": POD_API,
                "kind": "Secret",
                "metadata": {"name": access_name},
                "type": "Opaque",
                "data": {
                    key: base64.b64encode(value.encode()).decode()
                    for key, value in runtime.credential.items()
                },
            }
        )
        env = list(runtime.environment)
        container = {
            "name": RUNNER_CONTAINER,
            "image": image,
            "imagePullPolicy": "IfNotPresent",
            "workingDir": RUNTIME_ROOT,
            "command": list(runtime.command),
            "volumeMounts": [{"name": WORK_VOLUME, "mountPath": RUNTIME_ROOT}],
        }
        if runtime.readiness_command:
            container["readinessProbe"] = {
                "exec": {"command": list(runtime.readiness_command)}
            }
        init = {
            "name": "register",
            "image": image,
            "imagePullPolicy": "IfNotPresent",
            "workingDir": RUNTIME_ROOT,
            "command": list(runtime.init_command),
            "env": env,
            "volumeMounts": container["volumeMounts"],
        }
        document = {
            "apiVersion": DEPLOYMENT_API,
            "kind": "Deployment",
            "metadata": {"name": action.name},
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": {"app": action.name}},
                "template": {
                    "metadata": {"labels": {"app": action.name}},
                    "spec": {
                        "automountServiceAccountToken": False,
                        "imagePullSecrets": [{"name": registry_name}],
                        "initContainers": [init],
                        "containers": [container],
                        "volumes": [{"name": WORK_VOLUME, "emptyDir": {}}],
                    },
                },
            },
        }
        self.resources.apply(document)
        service.wait_rollout("deployment", action.name, action.namespace)
        pods = service.json(
            "-n",
            action.namespace,
            "get",
            "pods",
            "-l",
            f"app={action.name}",
            "-o",
            "json",
        )
        evidence = running_images(pods, image)
        changed = any(item["action"] == APPLIED for item in self.resources.evidence)
        return {
            "action": APPLIED if changed else NO_OP,
            "verified": True,
            "namespace": action.namespace,
            "name": action.name,
            "image": image,
            "pods": evidence,
            "resources": self.resources.evidence,
        }

    def cleanup(self) -> None:
        """Roll back newly created workload resources after a failed combination.

        :raises KubernetesError: If owned resource cleanup fails.
        """
        if self.resources is not None and (self.context.failed or self.result is None):
            self.resources.recover()


class DeleteRunner(K8SCallback[RunnerAction, dict[str, Any]]):
    """Remove a selected runner workload and its owned runtime Secrets."""

    def run(self) -> dict[str, Any]:
        """Delete the workload before its access material, verifying each absence.

        :returns: Applied/no-op deletion evidence.
        :raises ValueError: If workload inputs were not supplied.
        :raises KubernetesError: If deletion or independent read-back fails.
        """
        action = self.action
        if action is None:
            raise ValueError("runner_action_missing")
        resources = ResourceSet(
            cast(KubernetesService, self.kubernetes), action.namespace, action.name
        )
        changed = resources.delete("Deployment", action.name)
        for suffix in (REGISTRATION_SECRET_SUFFIX, REGISTRY_SECRET_SUFFIX):
            changed = resources.delete("Secret", action.name + suffix) or changed
        return {
            "action": APPLIED if changed else NO_OP,
            "verified": True,
            "namespace": action.namespace,
            "name": action.name,
        }


class PullImage(K8SCallback[RunnerAction, dict[str, Any]]):
    """Pull and execute the toolbox doctor in a temporary Kubernetes Job."""

    def __init__(
        self,
        *,
        action: RunnerAction,
        image: str | AbstractCallback[Any],
        dockerconfig: AbstractCallback[Any],
    ) -> None:
        """Keep the selected image and native registry credential dependency.

        :param action: Namespace, operation name, and timeout.
        :param image: Exact image or publication callback.
        :param dockerconfig: Registry authentication callback.
        """
        super().__init__(action=action)
        self.image = image
        self.registry = dockerconfig
        self.resources: ResourceSet | None = None

    def run(self) -> dict[str, Any]:
        """Require successful native pull and doctor execution.

        :returns: Verified image, completed Job, and Pod image IDs.
        :raises ValueError: If callback inputs are missing.
        :raises KubernetesError: If pull, doctor, or independent read-back fails.
        """
        action = self.action
        if action is None or self.registry.result is None:
            raise ValueError("pull_dependencies_missing")
        image = published_image(self.image)
        service = cast(KubernetesService, self.kubernetes)
        self.resources = ResourceSet(service, action.namespace, action.name)
        name = action.name + "-pull"
        secret = name + "-registry"
        # An earlier invocation can leave only this exact owned verification Job.
        self.resources.delete("Job", name)
        self.resources.apply(registry_secret(secret, self.registry.result.dockerconfig))
        self.resources.apply(
            {
                "apiVersion": JOB_API,
                "kind": "Job",
                "metadata": {"name": name},
                "spec": {
                    "backoffLimit": 0,
                    "activeDeadlineSeconds": action.timeout,
                    "template": {
                        "spec": {
                            "restartPolicy": "Never",
                            "automountServiceAccountToken": False,
                            "imagePullSecrets": [{"name": secret}],
                            "containers": [
                                {
                                    "name": RUNNER_CONTAINER,
                                    "image": image,
                                    "command": DOCTOR_COMMAND,
                                }
                            ],
                        }
                    },
                },
            }
        )
        service.wait_job(name, action.namespace)
        pods = service.json(
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

    def cleanup(self) -> None:
        """Remove the exact temporary Job and pull credential after every exit.

        :raises KubernetesError: If temporary resources cannot be removed.
        """
        action = self.action
        if self.resources is not None and action is not None:
            name = action.name + "-pull"
            self.resources.cleanup_owned(
                (
                    ("Job", name),
                    ("Secret", name + "-registry"),
                )
            )


def published_image(value: str | AbstractCallback[Any]) -> str:
    """Read an explicit image or a completed callback's image field.

    :param value: Explicit immutable image or image-producing callback.
    :returns: Resolved immutable image reference.
    :raises ValueError: If the result contains no digest-pinned image.
    """
    if isinstance(value, AbstractCallback):
        result = value.result
        value = (
            result.get("image")
            if isinstance(result, dict)
            else getattr(result, "image", None)
        )
    if not isinstance(value, str) or "@sha256:" not in value:
        raise ValueError("immutable_image_required")
    return value


def registry_secret(name: str, dockerconfig: bytes) -> dict[str, Any]:
    """Build a native pull/push Secret from already resolved credentials.

    :param name: Exact operation-selected resource name.
    :param dockerconfig: Private native config bytes.
    :returns: Internal Secret manifest; never emit its data.
    """
    return {
        "apiVersion": POD_API,
        "kind": "Secret",
        "metadata": {"name": name},
        "type": PULL_SECRET_TYPE,
        "data": {PULL_SECRET_KEY: base64.b64encode(dockerconfig).decode()},
    }


def running_images(
    document: Any, image: str, *, completed: bool = False
) -> list[dict[str, str]]:
    """Prove the actual selected container image from native Pod status.

    :param document: Native PodList read-back.
    :param image: Expected digest-pinned image.
    :param completed: Accept successful terminated containers for pull Jobs.
    :returns: Safe Pod names and observed immutable image IDs.
    :raises KubernetesError: If no container proves the requested image and state.
    """
    digest = image.rsplit("@", 1)[-1]
    evidence = []
    for pod in document.get("items", []) if isinstance(document, dict) else []:
        if pod.get("metadata", {}).get("deletionTimestamp"):
            continue
        for status in pod.get("status", {}).get("containerStatuses", []):
            state = status.get("state", {})
            healthy = (
                state.get("terminated", {}).get("exitCode") == 0
                if completed
                else bool(state.get("running"))
            )
            if (
                status.get("name") == RUNNER_CONTAINER
                and healthy
                and status.get("imageID", "").endswith(digest)
            ):
                evidence.append(
                    {"name": pod["metadata"]["name"], "image_id": status["imageID"]}
                )
    if not evidence:
        raise KubernetesError("running_image_readback_missing")
    return evidence
