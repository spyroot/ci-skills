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
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from .access import kubectl_argv, kubernetes_env, oc_argv
from .action import K8SCallback
from .credentials import Sources, bind_sources
from .runtime import (
    CommandResult,
    error_class,
    run_command_bounded,
    run_command_tail,
    sanitize,
    trace_enabled,
    trace_event,
)
from .target import Target, TargetError

DEFAULT_COMMAND_TIMEOUT: Final = 30
KUBERNETES_SERVICE_KEY: Final = "kubernetes"
BUILD_COMPLETE_PHASE: Final = "Complete"
BUILD_FAILED_PHASES: Final = frozenset({"Failed", "Error", "Cancelled"})
BUILD_POLL_SECONDS: Final = 5
BUILD_LOG_BYTES: Final = 4096
BUILD_LOG_LINES: Final = 40

JsonValue = dict[str, Any] | list[Any]


class KubernetesError(RuntimeError):
    """A bounded native Kubernetes operation did not complete safely."""


@dataclass
class KubernetesService:
    """Run native commands against one bound Kubernetes target.

    .. attribute:: target
        Target selected by the parent action and bound to its credential sources.

    .. attribute:: timeout
        Process-level bound, in seconds, for every native command.

    .. attribute:: identity
        Username read from the selected cluster after verification.

    """

    target: Target
    timeout: int = DEFAULT_COMMAND_TIMEOUT
    identity: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Reject an unusable service before any provider command is built.

        :raises ValueError: If the command timeout is not positive.
        :raises TargetError: If the target has no Kubernetes authority.
        """
        if self.timeout <= 0:
            raise ValueError("kubernetes_timeout_invalid")
        if self.target.kubernetes is None:
            raise TargetError("kubernetes_target_missing")

    def command(self, *args: str, openshift: bool = False) -> CommandResult:
        """Run one bounded ``kubectl`` or ``oc`` command without a shell.

        :param args: Native client arguments after the selected context flags.
        :param openshift: Select ``oc`` instead of ``kubectl``.
        :returns: Bounded stdout, stderr, exit status, and exact argument vector.
        :raises TargetError: If the bound kubeconfig changed before execution.
        """  # noqa: DOC502,DOC503 - shared target binding raises this error
        builder = oc_argv if openshift else kubectl_argv
        return run_command_bounded(
            builder(self.target, *args),
            timeout=self.timeout,
            env=kubernetes_env(self.target),
        )

    def json(self, *args: str, openshift: bool = False) -> JsonValue:
        """Run one command and parse its bounded JSON response.

        The returned value is internal provider data. Callers must select safe
        report fields and use the shared output redaction boundary before emit.

        :param args: Native client arguments that request a JSON response.
        :param openshift: Select ``oc`` instead of ``kubectl``.
        :returns: Parsed provider object or list.
        :raises KubernetesError: If the command fails or returns invalid JSON.
        :raises TargetError: If the bound kubeconfig changed before execution.
        """  # noqa: DOC502,DOC503 - command propagates target binding errors
        result = self.command(*args, openshift=openshift)
        if result.returncode:
            raise KubernetesError(error_class(result))
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise KubernetesError("invalid_json") from exc
        if not isinstance(value, (dict, list)):
            raise KubernetesError("invalid_json_shape")
        return value

    def read(self, kind: str, name: str, namespace: str) -> dict[str, Any] | None:
        """Read one exact object, returning ``None`` when it is absent.

        The raw object remains internal so its atomic owner can compare exact
        read-back. It must not be emitted directly.

        :param kind: Kubernetes resource kind accepted by the native client.
        :param name: Exact resource name.
        :param namespace: Resource namespace, or an empty string for cluster scope.
        :returns: The provider object or ``None`` after an ignored 404.
        :raises KubernetesError: If the read fails or returns a non-object response.
        :raises TargetError: If the bound kubeconfig changed before execution.
        """  # noqa: DOC502,DOC503 - command propagates target binding errors
        args = [*self._namespace_args(namespace), "get", f"{kind}/{name}"]
        args.extend(("--ignore-not-found", "-o", "json"))
        result = self.command(*args)
        if result.returncode:
            raise KubernetesError(error_class(result))
        if not result.stdout.strip():
            return None
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise KubernetesError("invalid_json") from exc
        if not isinstance(value, dict):
            raise KubernetesError("invalid_object_response")
        return value

    def apply(self, document: Mapping[str, Any]) -> dict[str, Any]:
        """Apply one document through a mode-0600 file.

        The raw returned object remains internal for exact atomic read-back and
        must not be emitted directly.

        :param document: Complete Kubernetes object supplied by the atomic owner.
        :returns: Object returned by the Kubernetes API after apply.
        :raises KubernetesError: If serialization, apply, or response parsing fails.
        :raises TargetError: If the bound kubeconfig changed before execution.
        """  # noqa: DOC502,DOC503 - command propagates target binding errors
        try:
            payload = json.dumps(dict(document), separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise KubernetesError("document_not_json") from exc

        path: Path | None = None
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

        if result.returncode:
            raise KubernetesError(error_class(result))
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise KubernetesError("invalid_json") from exc
        if not isinstance(value, dict):
            raise KubernetesError("invalid_object_response")
        return value

    def delete(self, kind: str, name: str, namespace: str) -> None:
        """Delete one exact object and independently verify absence.

        :param kind: Kubernetes resource kind accepted by the native client.
        :param name: Exact resource name.
        :param namespace: Resource namespace, or an empty string for cluster scope.
        :raises KubernetesError: If deletion fails or the object remains present.
        :raises TargetError: If the bound kubeconfig changed before execution.
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

    def wait_rollout(self, kind: str, name: str, namespace: str) -> dict[str, Any]:
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

    def wait_job(self, name: str, namespace: str) -> dict[str, Any]:
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

    def wait_build(self, name: str, namespace: str) -> dict[str, Any]:
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
        :raises TargetError: If the target is missing or its kubeconfig changed.
        """
        configuration = self.json("config", "view", "--minify", "-o", "json")
        if not isinstance(configuration, dict):
            raise KubernetesError("invalid_context_response")
        expected = self.target.kubernetes
        if expected is None:
            raise TargetError("kubernetes_target_missing")
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
    ) -> dict[str, Any]:
        if result.returncode:
            raise KubernetesError(error_class(result))
        value = self.read(kind, name, namespace)
        if value is None:
            raise KubernetesError("waited_resource_missing")
        return value

    def _native_timeout(self) -> int:
        """Let the native command settle before the process-level deadline."""
        return max(self.timeout - 1, 1)

    @staticmethod
    def _namespace_args(namespace: str) -> tuple[str, ...]:
        return ("--namespace", namespace) if namespace else ()


class K8SAuth(K8SCallback[Target, KubernetesService]):
    """Bind and verify the Kubernetes service selected by the parent action."""

    def __init__(
        self, *, target: Target, timeout: int = DEFAULT_COMMAND_TIMEOUT
    ) -> None:
        """Keep the parent-selected target and process timeout.

        :param target: Target already selected by the parent workflow.
        :param timeout: Positive command timeout in seconds.
        """
        super().__init__(action=target)
        self.target = target
        self.timeout = timeout

    def collect(self) -> None:
        """Validate the injected target without resolving another target file.

        :raises TargetError: If the selected target lacks Kubernetes.
        :raises ValueError: If the command timeout is not positive.
        """
        if (
            self.target.kubernetes is None
            or "kubernetes" not in self.target.active_surfaces
        ):
            raise TargetError("kubernetes_target_missing")
        if self.timeout <= 0:
            raise ValueError("kubernetes_timeout_invalid")

    def run(self) -> KubernetesService:
        """Freeze existing sources and verify operation-specific access.

        :returns: Bound service with verified context, server, and identity.
        :raises KubernetesError: If live operation-specific verification fails.
        :raises TargetError: If existing target or credential binding fails.
        """  # noqa: DOC502,DOC503 - binding and verify errors propagate
        target = (
            self.target
            if isinstance(self.target.sources, Sources)
            else bind_sources(self.target)
        )
        service = KubernetesService(target, self.timeout)
        service.verify()
        return service

    def update(self) -> None:
        """Expose the verified service to dependent callbacks."""
        if self.result is not None:
            self.context.services[KUBERNETES_SERVICE_KEY] = self.result
