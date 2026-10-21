"""Bound Kubernetes operations for composed CI Skills workflows.

The service returns provider data to its atomic owner and never emits it. Report
boundaries select safe fields and apply the shared runtime redaction policy.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final, Optional

from lib.python.access.credentials import bind_sources
from lib.python.access.kubernetes import kubectl_argv, kubernetes_env, oc_argv
from lib.python.access.sources import Sources
from lib.python.core.abstract.callback import Callback
from lib.python.core.abstract.skill_action import ActionT_co
from lib.python.core.abstract.skill_plugin_type import SkillPluginType
from lib.python.core.cli_api import response_decoder
from lib.python.core.delivery_state import DeliveryState, ProviderAuthAction
from lib.python.core.json_types import (
    JSONDocument,
    JSONObject,
    copy_json_object,
    json_object,
    json_objects,
    json_string,
)
from lib.python.core.runtime import (
    CommandResult,
    error_class,
    run_command_bounded,
    run_command_tail,
    sanitize,
    trace_enabled,
    trace_event,
)
from lib.python.core.target import KubernetesTarget, Target, TargetError

DEFAULT_COMMAND_TIMEOUT: Final = 30
KUBERNETES_SERVICE_KEY: Final = SkillPluginType.KUBERNETES.value
BUILD_COMPLETE_PHASE: Final = "Complete"
BUILD_FAILED_PHASES: Final = frozenset({"Failed", "Error", "Cancelled"})
BUILD_POLL_SECONDS: Final = 5
BUILD_LOG_BYTES: Final = 4096
BUILD_LOG_LINES: Final = 40
POD_MOUNT_FAILURE_SECONDS: Final = 60
POD_READINESS_POLL_SECONDS: Final = 5


class KubernetesError(RuntimeError):
    """A bounded native Kubernetes operation did not complete safely."""


class PodMountFailure(KubernetesError):
    """A Pending Pod has UID-correlated FailedMount evidence on its assigned node."""

    def __init__(self, pod: JSONObject, events: Sequence[JSONObject]) -> None:
        """Retain exact provider observations for owned-Pod recovery.

        :param pod: Current Pending Pod read back from the selected cluster.
        :param events: FailedMount events selected for that exact Pod UID.
        """
        super().__init__("tool_pod_failed_mount")
        self.pod = copy_json_object(pod)
        self.events = tuple(copy_json_object(event) for event in events)


@dataclass
class KubernetesService:
    """Run native commands against one verified Kubernetes target.

    :param target: Selected context, server, and bound credential sources.
    :param timeout: Per-command runtime bound in seconds.
    :ivar identity: Cluster username read after target verification.
    :ivar configuration: Concrete Kubernetes selection retained at construction.

    .. attribute :: target
        :type: Target

        Selected context, server, and bound credential sources.

    .. attribute :: timeout
        :type: int

        Per-command runtime bound in seconds.

    .. attribute :: identity
        :type: str

        Cluster username read after target verification.

    .. attribute :: configuration
        :type: KubernetesTarget

        Concrete Kubernetes selection retained at construction.
    """

    target: Target
    timeout: int = DEFAULT_COMMAND_TIMEOUT
    identity: str = field(init=False, repr=False)
    configuration: KubernetesTarget = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Reject an unusable service before any provider command is built.

        :raises ValueError: If the command timeout is not positive.
        :raises TargetError: If the target has no Kubernetes authority.
        """
        if self.timeout <= 0:
            raise ValueError("kubernetes_timeout_invalid")
        selected = self.target.kubernetes
        if selected is None:
            raise TargetError("kubernetes_target_missing")
        self.configuration = selected

    def command(
        self,
        *args: str,
        openshift: bool = False,
        input_data: Optional[bytes] = None,
        timeout: Optional[float] = None,
    ) -> CommandResult:
        """Run one bounded ``kubectl`` or ``oc`` command without a shell.

        :param *args: Native client arguments after the selected context flags.
        :param openshift: Select ``oc`` instead of ``kubectl``.
        :param input_data: Optional exact stdin bytes, excluded from command metadata.
        :param timeout: Optional remaining operation seconds; capped by the service bound.
        :returns: Bounded stdout, stderr, exit status, and exact argument vector.
        :raises ValueError: An explicitly supplied timeout is not positive.
        :raises TargetError: If the bound kubeconfig changed before execution.
        :raises OSError: If the native process cannot start or its output cannot be read.
        """  # noqa: DOC502,DOC503 - Binding and native process failures propagate.
        command_timeout = (
            self.timeout if timeout is None else min(self.timeout, timeout)
        )
        if command_timeout <= 0:
            raise ValueError("kubernetes_timeout_invalid")
        builder = oc_argv if openshift else kubectl_argv
        return run_command_bounded(
            builder(self.target, *args),
            timeout=command_timeout,
            env=kubernetes_env(self.target),
            **({"input_data": input_data} if input_data is not None else {}),
        )

    def json(
        self,
        *args: str,
        openshift: bool = False,
        timeout: Optional[float] = None,
    ) -> JSONDocument:
        """Run one command and parse its bounded JSON response.

        The returned value is internal provider data. Callers must select safe
        report fields and use the shared output redaction boundary before emit.

        :param *args: Native client arguments that request a JSON response.
        :param openshift: Select ``oc`` instead of ``kubectl``.
        :param timeout: Optional remaining operation seconds; capped by the service bound.
        :returns: Parsed provider object or list.
        :raises ValueError: An explicitly supplied timeout is not positive.
        :raises KubernetesError: If the command fails or returns invalid JSON.
        :raises TargetError: If the bound kubeconfig changed before execution.
        :raises OSError: If the native process cannot start or its output cannot be read.
        """  # noqa: DOC502,DOC503 - command propagates target binding errors
        result = self.command(
            *args,
            openshift=openshift,
            **({"timeout": timeout} if timeout is not None else {}),
        )
        if result.returncode:
            raise KubernetesError(error_class(result))
        try:
            value = response_decoder().decode(result.stdout)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise KubernetesError("invalid_json") from exc
        if not isinstance(value, (dict, list)):
            raise KubernetesError("invalid_json_shape")
        return value

    def read(
        self,
        kind: str,
        name: str,
        namespace: str,
        *,
        timeout: Optional[float] = None,
    ) -> Optional[JSONObject]:
        """Read one exact object, returning ``None`` when it is absent.

        The raw object remains internal so its atomic owner can compare exact
        read-back. It must not be emitted directly.

        :param kind: Kubernetes resource kind accepted by the native client.
        :param name: Exact resource name.
        :param namespace: Resource namespace, or an empty string for cluster scope.
        :param timeout: Optional remaining operation seconds; capped by the service bound.
        :returns: The provider object or ``None`` after an ignored 404.
        :raises ValueError: An explicitly supplied timeout is not positive.
        :raises KubernetesError: If the read fails or returns a non-object response.
        :raises TargetError: If the bound kubeconfig changed before execution.
        :raises OSError: If the native process cannot start or its output cannot be read.
        """  # noqa: DOC502,DOC503 - Command and decoding helpers propagate failures.
        args = [*self._namespace_args(namespace), "get", f"{kind}/{name}"]
        args.extend(("--ignore-not-found", "-o", "json"))
        result = self.command(
            *args, **({"timeout": timeout} if timeout is not None else {})
        )
        if result.returncode == 0 and not result.stdout.strip():
            return None
        return self._object_response(result)

    def apply(self, document: JSONObject) -> JSONObject:
        """Apply one document through a mode-0600 file.

        The raw returned object remains internal for exact atomic read-back and
        must not be emitted directly.

        :param document: Complete Kubernetes object supplied by the atomic owner;
            nested mappings and arrays may be read-only containers.
        :returns: Object returned by the Kubernetes API after apply.
        :raises KubernetesError: If serialization, apply, or response parsing fails.
        :raises TargetError: If the bound kubeconfig changed before execution.
        :raises OSError: If the private manifest cannot be created, permissioned,
            written, or removed, or the native process cannot start or its output
            cannot be read.
        """  # noqa: DOC502,DOC503 - Binding, filesystem, and command failures propagate.
        try:
            payload = json.dumps(
                copy_json_object(document),
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise KubernetesError("document_not_json") from exc

        path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix="ci-skills-kubernetes-",
                suffix=".json",
                delete=False,
            ) as handle:
                path = Path(handle.name)
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
                handle.write(payload)
                handle.write("\n")
            result = self.command("apply", "-f", str(path), "-o", "json")
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

        return self._object_response(result)

    @staticmethod
    def _object_response(result: CommandResult) -> JSONObject:
        """Decode one native result whose successful response must be an object.

        :param result: Bounded native command result containing JSON response text.
        :returns: Validated provider object through a read-only mapping interface.
        :raises KubernetesError: If the command failed, JSON is malformed or too
            deeply nested, or the response is an array, scalar, or JSON null.
        """
        if result.returncode:
            raise KubernetesError(error_class(result))
        try:
            value = response_decoder().decode(result.stdout)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise KubernetesError("invalid_json") from exc
        try:
            return json_object(value)
        except TypeError as exc:
            raise KubernetesError("invalid_object_response") from exc

    def delete(self, kind: str, name: str, namespace: str) -> None:
        """Delete one exact object and independently verify absence.

        :param kind: Kubernetes resource kind accepted by the native client.
        :param name: Exact resource name.
        :param namespace: Resource namespace, or an empty string for cluster scope.
        :raises KubernetesError: If deletion fails or the object remains present.
        :raises TargetError: If the bound kubeconfig changed before execution.
        :raises OSError: If deletion or read-back cannot start its native process
            or read its output.
        """  # noqa: DOC502,DOC503 - read and command propagate their errors
        args = [*self._namespace_args(namespace), "delete", f"{kind}/{name}"]
        args.extend(
            (
                "--ignore-not-found",
                "--wait=true",
                "--cascade=foreground",
                f"--timeout={self._native_timeout()}s",
            )
        )
        result = self.command(*args)
        if result.returncode:
            raise KubernetesError(error_class(result))
        if self.read(kind, name, namespace) is not None:
            raise KubernetesError("resource_delete_incomplete")

    def pod_exec(
        self,
        namespace: str,
        pod: str,
        container: str,
        *argv: str,
        input_data: Optional[bytes] = None,
    ) -> CommandResult:
        """Execute one bounded argument vector in an exact Pod container.

        The caller chooses whether an argument vector invokes a shell. This
        transport never interpolates command text or selects another Pod.

        :param namespace: Exact observed Pod namespace.
        :param pod: Exact observed Pod name.
        :param container: Exact selected container name.
        :param *argv: Command and arguments after the native ``--`` separator.
        :param input_data: Optional stdin bytes; supplied bytes enable native ``-i``.
        :returns: Bounded native process result.
        :raises ValueError: If the command vector is empty.
        :raises TargetError: If the bound kubeconfig changed before execution.
        :raises OSError: If the native process cannot start or read its output.
        """  # noqa: DOC503 - command propagates target binding and process failures.
        if not argv:
            raise ValueError("pod_exec_command_missing")
        return self.command(
            *self._namespace_args(namespace),
            "exec",
            *(("-i",) if input_data is not None else ()),
            f"pod/{pod}",
            "-c",
            container,
            "--",
            *argv,
            **({"input_data": input_data} if input_data is not None else {}),
        )

    def wait_pod_ready(self, name: str, namespace: str) -> JSONObject:
        """Wait for one exact Pod's Ready condition and read it back.

        :param name: Exact Pod name.
        :param namespace: Exact Pod namespace.
        :returns: Ready Pod read back from the API.
        :raises KubernetesError: If the wait fails, the Pod is absent, or its
            read-back does not retain a true Ready condition.
        :raises TargetError: If the bound kubeconfig changes during the wait.
        """  # noqa: DOC503 - command and read-back propagate target binding errors.
        result = self.command(
            *self._namespace_args(namespace),
            "wait",
            "--for=condition=Ready",
            f"pod/{name}",
            f"--timeout={self._native_timeout()}s",
        )
        pod = self._waited_object(result, "pod", name, namespace)
        status = pod.get("status")
        conditions = status.get("conditions", []) if isinstance(status, dict) else []
        if not isinstance(conditions, list) or not any(
            isinstance(condition, dict)
            and condition.get("type") == "Ready"
            and condition.get("status") == "True"
            for condition in conditions
        ):
            raise KubernetesError("pod_not_ready_after_wait")
        return pod

    def wait_pod_mount_ready(
        self,
        name: str,
        namespace: str,
        *,
        mount_failure_timeout: int = POD_MOUNT_FAILURE_SECONDS,
        timeout: Optional[float] = None,
    ) -> JSONObject:
        """Observe bounded mount failures before completing normal Pod readiness.

        :param name: Exact owned Pod name.
        :param namespace: Exact owned Pod namespace.
        :param mount_failure_timeout: Pending seconds after the first matching mount event.
        :param timeout: Remaining shared readiness seconds, capped by the service timeout.
        :returns: Ready Pod read back from the selected API.
        :raises ValueError: The mount-detection bound is not positive.
        :raises PodMountFailure: A UID-correlated mount failure remains Pending for the bound.
        :raises KubernetesError: Readiness fails without a classified mount failure.
        :raises TargetError: Bound kubeconfig changes during observation.
        """  # noqa: DOC503 - Provider helpers propagate their declared errors.
        operation_timeout = (
            self.timeout if timeout is None else min(self.timeout, timeout)
        )
        if mount_failure_timeout <= 0 or operation_timeout <= 0:
            raise ValueError("pod_mount_failure_timeout_invalid")
        started = time.monotonic()
        deadline = started + operation_timeout
        failed_since: Optional[float] = None
        observed_uid: Optional[str] = None
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            pod = self.read("Pod", name, namespace, timeout=remaining)
            if pod is None:
                raise KubernetesError("waited_resource_missing")
            uid = json_string(json_object(pod.get("metadata", {})).get("uid"))
            if observed_uid is not None and uid != observed_uid:
                raise KubernetesError("tool_pod_uid_changed_before_ready")
            observed_uid = uid
            status = json_object(pod.get("status", {}))
            conditions = json_objects(status.get("conditions", []))
            if any(
                item.get("type") == "Ready" and item.get("status") == "True"
                for item in conditions
            ):
                return pod
            if status.get("phase") in {"Failed", "Succeeded"}:
                raise KubernetesError("tool_pod_terminal_before_ready")
            node = json_object(pod.get("spec", {})).get("nodeName")
            containers = json_objects(status.get("containerStatuses", []))
            mounting = not containers or all(
                json_object(json_object(item.get("state", {})).get("waiting", {})).get(
                    "reason"
                )
                == "ContainerCreating"
                for item in containers
            )
            if (
                status.get("phase") == "Pending"
                and mounting
                and isinstance(node, str)
                and node
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                response = self.json(
                    *self._namespace_args(namespace),
                    "get",
                    "events",
                    "--field-selector",
                    f"involvedObject.uid={uid},reason=FailedMount",
                    "-o",
                    "json",
                    timeout=remaining,
                )
                events = [
                    item
                    for item in json_objects(json_object(response).get("items", []))
                    if item.get("reason") == "FailedMount"
                    and json_object(item.get("involvedObject", {})).get("uid") == uid
                ]
                if events:
                    if failed_since is None:
                        failed_since = time.monotonic()
                    if time.monotonic() - failed_since >= mount_failure_timeout:
                        raise PodMountFailure(pod, events)
                else:
                    failed_since = None
            else:
                failed_since = None
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(POD_READINESS_POLL_SECONDS, remaining))
        raise KubernetesError("tool_pod_ready_timeout")

    def wait_rollout(self, kind: str, name: str, namespace: str) -> JSONObject:
        """Wait for a native rollout, then read the exact workload back.

        :param kind: Workload kind supported by ``kubectl rollout status``.
        :param name: Exact workload name.
        :param namespace: Workload namespace.
        :returns: Workload read back after the rollout command succeeds.
        :raises KubernetesError: If the rollout fails or the workload is absent.
        :raises TargetError: If the bound kubeconfig changed before execution.
        """  # noqa: DOC502,DOC503 - helpers propagate their declared errors
        result = self.command(
            *self._namespace_args(namespace),
            "rollout",
            "status",
            f"{kind}/{name}",
            f"--timeout={self._native_timeout()}s",
        )
        return self._waited_object(result, kind, name, namespace)

    def wait_job(self, name: str, namespace: str) -> JSONObject:
        """Wait for a Kubernetes Job to complete and return its read-back.

        :param name: Exact Job name.
        :param namespace: Job namespace.
        :returns: Job read back after its ``Complete`` condition becomes true.
        :raises KubernetesError: If the wait fails or the Job is absent.
        :raises TargetError: If the bound kubeconfig changed before execution.
        """  # noqa: DOC502,DOC503 - helpers propagate their declared errors
        result = self.command(
            *self._namespace_args(namespace),
            "wait",
            "--for=condition=complete",
            f"job/{name}",
            f"--timeout={self._native_timeout()}s",
        )
        return self._waited_object(result, "job", name, namespace)

    def wait_build(self, name: str, namespace: str) -> JSONObject:
        """Wait for an OpenShift Build phase and verify exact read-back.

        :param name: Exact OpenShift Build name.
        :param namespace: Build namespace.
        :returns: Build whose read-back phase is exactly ``Complete``.
        :raises KubernetesError: If the wait fails, Build is absent, or phase differs.
        :raises TargetError: If the bound kubeconfig changed before execution.
        """  # noqa: DOC502,DOC503 - helpers propagate their declared errors
        deadline = time.monotonic() + self.timeout
        observed_phase = None
        try:
            while True:
                build = self.read("build", name, namespace)
                if build is None:
                    raise KubernetesError("build_missing")
                status = build.get("status", {})
                phase = status.get("phase") if isinstance(status, dict) else None
                if phase != observed_phase:
                    trace_event(
                        "build.phase",
                        {"name": name, "namespace": namespace, "phase": phase},
                    )
                    observed_phase = phase
                if phase == BUILD_COMPLETE_PHASE:
                    return build
                if phase in BUILD_FAILED_PHASES:
                    raise KubernetesError("build_phase_" + phase.lower())
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise KubernetesError("build_wait_timeout")
                time.sleep(min(BUILD_POLL_SECONDS, remaining))
        finally:
            if trace_enabled() and observed_phase != BUILD_COMPLETE_PHASE:
                result = run_command_tail(
                    oc_argv(
                        self.target,
                        "--namespace",
                        namespace,
                        "logs",
                        f"build/{name}",
                        "--timestamps",
                    ),
                    timeout=DEFAULT_COMMAND_TIMEOUT,
                    env=kubernetes_env(self.target),
                    max_bytes=BUILD_LOG_BYTES,
                    max_lines=BUILD_LOG_LINES,
                )
                trace_event(
                    "build.output",
                    {
                        "name": name,
                        "namespace": namespace,
                        "returncode": result.returncode,
                        "stdout": sanitize(result.stdout, BUILD_LOG_BYTES),
                        "stderr": sanitize(result.stderr),
                    },
                )

    def verify(self) -> str:
        """Verify the selected context, API server, and native identity.

        This operation-specific proof does not replace or claim the broader
        diagnostic ``kubernetes_access`` receipt.

        :returns: Username reported by native ``kubectl auth whoami``.
        :raises KubernetesError: If context, server, or identity read-back differs.
        :raises TargetError: If the bound kubeconfig changed.
        """  # noqa: DOC503 - Native command setup checks the bound kubeconfig.
        configuration = self.json("config", "view", "--minify", "-o", "json")
        if not isinstance(configuration, dict):
            raise KubernetesError("invalid_context_response")
        expected = self.configuration
        contexts = configuration.get("contexts")
        if not isinstance(contexts, list):
            raise KubernetesError("invalid_context_response")
        selected = [
            item
            for item in contexts
            if isinstance(item, dict) and item.get("name") == expected.context
        ]
        if len(selected) != 1:
            raise KubernetesError("context_not_unique")
        context = selected[0].get("context")
        if not isinstance(context, dict) or not isinstance(context.get("cluster"), str):
            raise KubernetesError("context_cluster_missing")
        clusters = configuration.get("clusters")
        if not isinstance(clusters, list):
            raise KubernetesError("invalid_context_response")
        matching = [
            item
            for item in clusters
            if isinstance(item, dict) and item.get("name") == context["cluster"]
        ]
        if len(matching) != 1:
            raise KubernetesError("cluster_not_unique")
        cluster = matching[0].get("cluster")
        if not isinstance(cluster, dict) or cluster.get("server") != expected.server:
            raise KubernetesError("api_server_mismatch")

        whoami = self.json("auth", "whoami", "-o", "json")
        if not isinstance(whoami, dict):
            raise KubernetesError("invalid_identity_response")
        status = whoami.get("status")
        user_info = status.get("userInfo") if isinstance(status, dict) else None
        if not isinstance(user_info, dict):
            user_info = whoami.get("userInfo")
        identity = user_info.get("username") if isinstance(user_info, dict) else None
        if not isinstance(identity, str) or not identity:
            raise KubernetesError("missing_identity")
        self.identity = identity
        return identity

    def _waited_object(
        self,
        result: CommandResult,
        kind: str,
        name: str,
        namespace: str,
    ) -> JSONObject:
        """Read an object back after a successful native wait command.

        :param result: Result of the rollout or Job wait command.
        :param kind: Resource kind to read back.
        :param name: Exact resource name.
        :param namespace: Resource namespace.
        :returns: Object read from the Kubernetes API after the wait.
        :raises KubernetesError: The wait failed or the named object is absent.
        :raises TargetError: Bound kubeconfig changed during read-back.
        """  # noqa: DOC503 - Read-back validates bound kubeconfig sources.
        if result.returncode:
            raise KubernetesError(error_class(result))
        value = self.read(kind, name, namespace)
        if value is None:
            raise KubernetesError("waited_resource_missing")
        return value

    def _native_timeout(self) -> int:
        """Reserve process time around a native wait timeout.

        :returns: Native timeout in seconds, at least one and usually one less than
            the service process timeout.
        """
        return max(self.timeout - 1, 1)

    @staticmethod
    def _namespace_args(namespace: str) -> tuple[str, ...]:
        """Make optional native namespace arguments.

        :param namespace: Namespace name, or empty text for cluster scope.
        :returns: ``("--namespace", namespace)`` when nonempty; otherwise empty tuple.
        """
        return ("--namespace", namespace) if namespace else ()


class K8SCallback(Callback[DeliveryState, ActionT_co]):
    """Read the verified Kubernetes service from the shared delivery state."""

    @property
    def plugin_type(self) -> SkillPluginType:
        """Identify the configuration shared by this plugin's callbacks.

        :returns: The kubernetes plugin's configuration cache key.
        """
        return SkillPluginType.KUBERNETES

    def read_config(self) -> JSONObject:
        """Reuse the concrete target from a caller-bound Kubernetes service.

        :returns: Empty evidence after retaining the already bound configuration.
        :raises TargetError: Authentication or the caller supplied no service.
        """  # noqa: DOC502 - Direct library callers provide the existing service.
        if not hasattr(self.state, "kubernetes"):
            raise TargetError(
                f"{SkillPluginType.KUBERNETES.value}_configuration_missing"
            )
        self.state.kubernetes_target = self.kubernetes.target
        return {}

    def is_configured(self) -> bool:
        """Check that shared state holds a credential-bound Kubernetes target.

        :returns: Whether target and credential selection completed for this invocation.
        """
        return hasattr(self.state, "kubernetes_target")

    @property
    def kubernetes(self) -> KubernetesService:
        """Read the service established by the preceding authentication action.

        :returns: The workflow's verified Kubernetes service.
        :raises AttributeError: If composition omitted Kubernetes authentication.
        """  # noqa: DOC502 - Missing composition state raises AttributeError.
        return self.state.kubernetes


class K8SAuth(K8SCallback[ProviderAuthAction]):
    """Bind and verify the Kubernetes service selected by the parent action."""

    def __init__(self, target: Target, timeout: int = DEFAULT_COMMAND_TIMEOUT):
        """Construct required authentication inputs without provider access.

        :param target: Runtime target selected by the parent workflow.
        :param timeout: Positive command timeout in seconds.
        """
        super().__init__(ProviderAuthAction(target, timeout))

    def read_config(self) -> JSONObject:
        """Validate and bind this plugin's declared kubeconfig before execution.

        :returns: Empty setup evidence after retaining the credential-bound target.
        :raises TargetError: The selected Kubernetes target or credential is unavailable.
        :raises ValueError: The command timeout is not positive.
        """  # noqa: DOC503 - Credential binding validates configured file sources.
        selected = self.action.target
        if (
            selected.kubernetes is None
            or self.plugin_type.value not in selected.active_surfaces
        ):
            raise TargetError("kubernetes_target_missing")
        if self.action.timeout <= 0:
            raise ValueError("kubernetes_timeout_invalid")
        target = (
            selected
            if isinstance(selected.sources, Sources)
            else bind_sources(
                replace(selected, active_surfaces=(SkillPluginType.KUBERNETES.value,))
            )
        )
        if target.sources is None:
            raise TargetError("kubernetes_credential_unresolved")
        target.sources.require(SkillPluginType.KUBERNETES)
        self.state.kubernetes_target = target
        return {}

    def run(self) -> JSONObject:
        """Freeze existing credentials and retain the verified private service.

        :returns: Safe identity and selected context/server evidence.
        :raises KubernetesError: If live context or identity verification fails.
        :raises TargetError: If target or credential source binding is invalid.
        :raises OSError: If configured credential inputs cannot be read.
        """  # noqa: DOC502 - Credential binding and service verification can fail.
        target = self.state.kubernetes_target
        service = KubernetesService(target, self.action.timeout)
        identity = service.verify()
        self.state.kubernetes = service
        evidence = {
            "identity": identity,
            "context": service.configuration.context,
            "server": service.configuration.server,
        }
        self.state.access_evidence[KUBERNETES_SERVICE_KEY] = evidence
        return evidence
