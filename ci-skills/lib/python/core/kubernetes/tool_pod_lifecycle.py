"""Lifecycle ownership callbacks for reusable Kubernetes tool Pods.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import shlex
from typing import Optional

from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.resources import ResourceSet, matches
from lib.python.core.kubernetes.run_scope import run_owned
from lib.python.core.kubernetes.tool_pod_models import (
    PrepareToolPodAction,
    RemoveToolPodAction,
)
from lib.python.core.kubernetes.tool_pod_resources import (
    TOOL_POD_ROLE,
    TOOL_POD_SCRIPT_ROLE,
    tool_pod_document,
    tool_pod_script_configmap,
)
from lib.python.core.kubernetes.tool_pod_runtime import (
    OUTPUT_LIMIT,
    SHELL,
    ToolPodK8SCallback,
    _pod_handle,
    _presence,
    _resource,
    retain_tool_pod,
)
from lib.python.core.runtime import error_class, sanitize
from lib.python.sys.status import APPLIED, NO_OP, PASS, VERIFIED


class StageToolPod(ToolPodK8SCallback[PrepareToolPodAction]):
    """Resolve or create one run scope and stage its exact execution Pod."""

    def __init__(self, action: PrepareToolPodAction):
        """Retain confirmed inputs and initialize empty ownership trackers.

        :param action: Confirmed tool specification, scope inputs, and mounts.
        """
        super().__init__(action)
        self.resources: Optional[ResourceSet] = None

    def run(self) -> JSONObject:
        """Reuse a matching live Pod or create and read back a new one.

        :returns: Concrete run, Pod identity, reuse, and ownership evidence.
        :raises KubernetesError: If lookup is ambiguous, selected resources differ,
            the namespace is absent, or the resulting Pod is not usable.
        """
        state, action = self.tool_state, self.action
        scope = state.run_scope
        if action.run_id is not None and scope.run_id != action.run_id:
            raise KubernetesError("tool_pod_run_scope_mismatch")
        if action.namespace is not None and (
            scope.namespace != action.namespace or scope.owns_namespace
        ):
            raise KubernetesError("tool_pod_namespace_mismatch")
        reused = state.scope_reused
        resources = scope.resources(self.kubernetes)
        self.resources = resources
        script_name = scope.name(TOOL_POD_SCRIPT_ROLE)
        pod_name = scope.name(TOOL_POD_ROLE)
        if reused:
            expected_script = tool_pod_script_configmap(scope)
            expected_pod = tool_pod_document(scope, action)
            observed_script = self.kubernetes.read(
                "ConfigMap", script_name, scope.namespace
            )
            if (
                observed_script is None
                or not resources.owns(observed_script)
                or not matches(expected_script, observed_script)
            ):
                raise KubernetesError("tool_pod_script_resource_mismatch")
            observed = self.kubernetes.read("Pod", pod_name, scope.namespace)
            if (
                observed is None
                or not resources.owns(observed)
                or not matches(expected_pod, observed)
            ):
                raise KubernetesError("tool_pod_owned_resource_missing")
            _pod_handle(observed, action)
            resource_action = NO_OP
        else:
            resources.apply(tool_pod_script_configmap(scope))
            evidence = resources.apply(tool_pod_document(scope, action))
            resource_action = str(evidence["action"])
        ready = self.kubernetes.wait_pod_ready(pod_name, scope.namespace)
        handle = _pod_handle(ready, action)
        state.pod = handle
        state.readback.update(
            {
                "run_id": scope.run_id,
                "pod": handle.as_dict(),
                "owns_namespace": scope.owns_namespace,
            }
        )
        return {
            "action": resource_action,
            "verified": True,
            "reused": reused,
            "run_id": scope.run_id,
            "owns_namespace": scope.owns_namespace,
            "pod": handle.as_dict(),
        }

    def cleanup(self) -> JSONObject:
        """Remove created unretained objects and preserve retained identities.

        :returns: Verified removed and retained resource identities.
        """
        state = self.tool_state
        removed: list[JSONObject] = []
        retained = state.retained_resources
        if self.resources is not None:
            selected = [
                (kind, name)
                for kind, name in reversed(self.resources.created)
                if _resource(kind, name, self.resources.namespace) not in retained
            ]
            self.resources.cleanup_owned(selected)
            removed.extend(
                {
                    "kind": kind,
                    "name": name,
                    "namespace": self.resources.namespace,
                    "status": PASS,
                    "absent": True,
                }
                for kind, name in selected
            )
        state.cleanup_resources.extend(removed)
        return {
            "resources": removed,
            "retained": [
                item.as_dict()
                for item in sorted(
                    retained,
                    key=lambda item: (item.namespace, item.kind, item.name),
                )
            ],
        }


class VerifyTools(ToolPodK8SCallback[PrepareToolPodAction]):
    """Re-probe every tool in the current container and decide retention."""

    def run(self) -> JSONObject:
        """Verify declared probes and retain only a completely usable Pod.

        :returns: Final Pod, tool probe, and retained-resource evidence.
        :raises KubernetesError: If Pod identity, shape, restart state, presence,
            or a declared probe cannot be verified.
        """
        previous = self.tool_state.pod
        observed = self.kubernetes.read("Pod", previous.name, previous.namespace)
        if observed is None:
            raise KubernetesError("tool_pod_missing_before_verify")
        current = _pod_handle(observed, self.action)
        if current.uid != previous.uid:
            raise KubernetesError("tool_pod_uid_changed")
        if not matches(
            tool_pod_document(self.tool_state.run_scope, self.action),
            observed,
        ):
            raise KubernetesError("tool_pod_resource_shape_changed")
        self.tool_state.pod = current
        records: list[JSONObject] = []
        for index, dependency in enumerate(self.action.spec.dependencies):
            probe = dependency.probe
            if probe:
                pod = self.tool_state.pod
                if self.tool_state.search_paths:
                    additions = ":".join(
                        shlex.quote(path) for path in self.tool_state.search_paths
                    )
                    result = self.kubernetes.pod_exec(
                        pod.namespace,
                        pod.name,
                        pod.container,
                        SHELL,
                        "-ceu",
                        f'PATH={additions}:$PATH; export PATH; exec "$@"',
                        "probe",
                        *probe,
                    )
                else:
                    result = self.kubernetes.pod_exec(
                        pod.namespace,
                        pod.name,
                        pod.container,
                        *probe,
                    )
                if result.returncode:
                    raise KubernetesError(error_class(result))
                output = sanitize(result.stdout, OUTPUT_LIMIT)
            else:
                if not _presence(self.tool_state, dependency):
                    raise KubernetesError("dependency_verify_missing")
                output = "present"
            records.append(
                {
                    "index": index,
                    "name": dependency.name,
                    "probe": list(probe),
                    "observed": output,
                    "verified": True,
                }
            )
        state, scope = self.tool_state, self.tool_state.run_scope
        if self.action.retain:
            retain_tool_pod(state)
        state.readback["tools"] = records
        state.readback["retained_resources"] = [
            item.as_dict()
            for item in sorted(
                state.retained_resources,
                key=lambda item: (item.namespace, item.kind, item.name),
            )
        ]
        return {
            "action": VERIFIED,
            "verified": True,
            "run_id": scope.run_id,
            "pod": state.pod.as_dict(),
            "tools": records,
            "retained_resources": state.readback["retained_resources"],
        }


class RemoveToolPod(ToolPodK8SCallback[RemoveToolPodAction]):
    """Delete only labeled run-owned Pod resources and verify absence."""

    def run(self) -> JSONObject:
        """Resolve ownership, preserve selected namespaces, and remove the scope.

        :returns: Verified resource absence or an idempotent no-op result.
        :raises KubernetesError: If lookup provenance or live ownership differs.
        """
        state = self.tool_state
        if state.resolved_run_id != self.action.run_id or any(
            scope.run_id != self.action.run_id for scope in state.found_scopes
        ):
            raise KubernetesError("run_scope_lookup_action_mismatch")
        if not state.found_scopes:
            result: JSONObject = {
                "action": NO_OP,
                "verified": True,
                "run_id": self.action.run_id,
                "resources": [],
            }
            state.readback.update(result)
            return result
        removed: list[JSONObject] = []
        for scope in state.found_scopes:
            state.run_scope = scope
            resources = scope.resources(self.kubernetes)
            for kind, name in (
                ("Pod", scope.name(TOOL_POD_ROLE)),
                ("ConfigMap", scope.name(TOOL_POD_SCRIPT_ROLE)),
            ):
                observed = self.kubernetes.read(kind, name, scope.namespace)
                if observed is None:
                    continue
                if not run_owned(observed, self.action.run_id):
                    raise KubernetesError("tool_pod_resource_ownership_mismatch")
                if resources.delete(kind, name):
                    removed.append(
                        {
                            "kind": kind,
                            "name": name,
                            "namespace": scope.namespace,
                            "absent": True,
                            "status": PASS,
                        }
                    )
        result = {
            "action": APPLIED if removed else NO_OP,
            "verified": True,
            "run_id": self.action.run_id,
            "scopes": [scope.as_dict() for scope in state.found_scopes],
            "resources": removed,
        }
        state.cleanup_resources.extend(removed)
        state.readback.update(result)
        return result
