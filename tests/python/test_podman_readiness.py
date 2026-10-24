"""Focused offline coverage for the Kubernetes Podman readiness probe.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from argparse import Namespace
from collections.abc import MutableMapping, Sequence
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Final, NoReturn, Optional

import pytest
from conftest import (
    SKILL_ROOT,
    bind_kubernetes_fixture,
    import_script_module,
    install_executable,
    parse_json_output,
    read_journal,
    run_script,
)
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

PODMAN = import_script_module("lib.python.core.kubernetes.podman_probe")
PODMAN_EVIDENCE = import_script_module("lib.python.core.kubernetes.podman_evidence")
PODMAN_CALLBACKS = import_script_module("lib.python.core.kubernetes.podman_callbacks")
SCC_ACCESS = import_script_module("lib.python.core.kubernetes.scc_access")
READINESS = import_script_module("lib.python.core.kubernetes.image_build_readiness")
CALLBACKS = import_script_module("lib.python.core.abstract.callbacks")
EXECUTOR = import_script_module("lib.python.core.executor")
RUNTIME = import_script_module("lib.python.core.runtime")
RUN_SCOPE = import_script_module("lib.python.core.kubernetes.run_scope")
KUBERNETES = import_script_module("lib.python.core.kubernetes.kubernetes")
TARGET = import_script_module("lib.python.core.target")

if TYPE_CHECKING:
    from lib.python.core.executor import WorkflowExecutor
    from lib.python.core.json_types import JSONObject, JSONValue
    from lib.python.core.kubernetes.podman_callbacks import (
        PodmanProbeState,
        RunPodmanProbe,
    )
    from lib.python.core.kubernetes.podman_probe import PodmanProbeAction
    from lib.python.core.kubernetes.run_scope import RunScope
    from lib.python.core.runtime import CommandResult
    from lib.python.core.target import Target


def _target(tmp_path: Path) -> Target:
    """Build a Kubernetes-only target for provider-bound probe tests.

    :param tmp_path: Directory containing the synthetic kubeconfig path.
    :returns: Target with no GitHub, GitLab, or Harbor configuration.
    """
    return TARGET.Target(
        github=None,
        gitlab=None,
        kubernetes=TARGET.KubernetesTarget(
            context="unit-context",
            server="https://api.cluster.example.test:6443",
            kubeconfig=tmp_path / "kubeconfig",
        ),
        harbor=None,
        active_surfaces=("kubernetes",),
    )


def _action(tmp_path: Path) -> PodmanProbeAction:
    """Build one probe action with immutable test image digests.

    :param tmp_path: Directory used by the action's Kubernetes target.
    :returns: Concrete Podman probe action.
    """
    return PODMAN.PodmanProbeAction(
        target=_target(tmp_path),
        node="worker-a",
        probe_image=f"registry.example.test/podman@sha256:{'a' * 64}",
        inner_image=f"registry.example.test/busybox@sha256:{'b' * 64}",
        timeout=300,
        resources=PODMAN.ProbeResources(
            cpu_request="100m",
            cpu_limit="1",
            memory_request="128Mi",
            memory_limit="512Mi",
            storage_size="2Gi",
        ),
        mode=PODMAN.ProbeMode.DRY_RUN,
        confirm_plan=None,
        namespace=None,
    )


def test_run_scope_derives_all_owned_names_from_one_identifier() -> None:
    """Keep namespace, object names, and labels tied to one run identifier.

    :returns: None; assertions verify deterministic run ownership.
    """
    scope = RUN_SCOPE.RunScope.new(namespace="unit-namespace", run_id="unit123")
    same_run = RUN_SCOPE.RunScope.new(namespace="other-namespace", run_id="unit123")

    assert scope.namespace == "unit-namespace"
    assert scope.owns_namespace is False
    assert scope.name("podman-probe") == same_run.name("podman-probe")
    assert dict(scope.labels) == {"ci-skills.spyroot.dev/run-id": "unit123"}


@pytest.mark.parametrize(
    "run_id",
    ("", "UPPER", "bad_underscore", "-leading", "trailing-", "a" * 33),
)
def test_run_scope_rejects_identifiers_that_cannot_own_kubernetes_objects(
    run_id: str,
) -> None:
    """Reject invalid run identifiers before constructing resource names.

    :param run_id: Empty, non-DNS-safe, or overlong identifier.
    :returns: None; the expected exception is the behavior under test.
    """
    with pytest.raises(ValueError, match="run_scope_id_invalid"):
        RUN_SCOPE.RunScope(run_id, "unit-namespace", False)


def test_probe_action_requires_digest_pinned_images(tmp_path: Path) -> None:
    """Reject mutable probe and inner image tags before provider access.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; both image inputs are validated independently.
    """
    with pytest.raises(ValueError, match="immutable_image_required"):
        replace(_action(tmp_path), probe_image="registry.example.test/podman:latest")
    with pytest.raises(ValueError, match="immutable_image_required"):
        replace(_action(tmp_path), inner_image="registry.example.test/busybox:latest")


@pytest.mark.parametrize(
    "quantities",
    (
        ("0.5", "1500m", "0.5Gi", "1.5Gi", "2.5Gi"),
        ("5e-1", "1", "512M", "1G", "2G"),
        ("250m", "1", "512Mi", "1Gi", "2Gi"),
    ),
)
def test_probe_inputs_accept_positive_timeout_and_native_decimal_quantities(
    tmp_path: Path,
    quantities: tuple[str, str, str, str, str],
) -> None:
    """Preserve valid Kubernetes quantities instead of rewriting caller inputs.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :param quantities: Valid decimal, exponent, or binary-suffix resource values.
    :returns: None; the action and rendered Job retain the selected quantities.
    """
    resources = PODMAN.ProbeResources(*quantities)
    action = replace(_action(tmp_path), timeout=1800, resources=resources)
    document = PODMAN.probe_job(
        action,
        RUN_SCOPE.RunScope.new(namespace="unit-namespace", run_id="unit123"),
        "worker-a",
    )
    specification = document["spec"]["template"]["spec"]
    observed = specification["containers"][0]["resources"]

    assert action.timeout == 1800
    assert action.resources == resources
    assert observed["requests"] == {"cpu": quantities[0], "memory": quantities[2]}
    assert observed["limits"] == {"cpu": quantities[1], "memory": quantities[3]}
    storage = next(
        volume
        for volume in specification["volumes"]
        if volume["name"] == "containers-storage"
    )
    assert storage["emptyDir"]["sizeLimit"] == quantities[4]


@pytest.mark.parametrize(
    "changes",
    (
        {"cpu_request": "garbage"},
        {"cpu_request": "-100m"},
        {"cpu_limit": "-1"},
        {"memory_request": "1KK"},
        {"memory_request": "-1Gi"},
        {"memory_limit": "-1Gi"},
        {"storage_size": "-2Gi"},
        {"cpu_request": "2", "cpu_limit": "1"},
        {"memory_request": "2Gi", "memory_limit": "1Gi"},
    ),
)
def test_probe_rejects_invalid_resource_bounds_before_action_construction(
    tmp_path: Path,
    changes: dict[str, str],
) -> None:
    """Reject unusable bounds before an action can create its temporary namespace.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :param changes: Malformed, negative, or request-above-limit input overrides.
    :returns: None; input validation raises before constructing an apply action.
    """
    action = _apply_action(tmp_path)

    with pytest.raises(ValueError):
        resources = replace(action.resources, **changes)
        replace(action, resources=resources)


def test_probe_rejects_nonpositive_timeout(tmp_path: Path) -> None:
    """Reject a timeout that cannot bound a provider operation.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; zero and negative values are rejected before provider access.
    """
    for timeout in (0, -1):
        with pytest.raises(ValueError, match="timeout_must_be_positive"):
            replace(_action(tmp_path), timeout=timeout)


def test_probe_defaults_to_live_plan_and_apply_requires_confirmation(
    tmp_path: Path,
) -> None:
    """Keep live planning as the default and reject unconfirmed mutation.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; mode and confirmation invariants are asserted.
    """
    args = READINESS.build_parser().parse_args([])

    assert PODMAN.resolve_probe_mode(args) == PODMAN.ProbeMode.LIVE_PLAN
    with pytest.raises(ValueError, match="confirm_plan_required"):
        replace(
            _action(tmp_path),
            mode=PODMAN.ProbeMode.APPLY,
            confirm_plan=None,
        )


def test_probe_plan_needs_only_kubernetes_configuration(tmp_path: Path) -> None:
    """Plan a probe when GitHub, GitLab, and Harbor are all absent.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; the plan proves the provider boundary and no side effects.
    """
    action = _action(tmp_path)

    plan = action.plan("worker-a")

    assert plan["target"] == {
        "context": "unit-context",
        "server": "https://api.cluster.example.test:6443",
    }
    assert plan["selected_node"] == "worker-a"
    assert plan["claim"] == "nested_podman_runtime_readiness"
    assert plan["run_scope"] == {
        "namespace_mode": "generated",
        "namespace": None,
        "service_account": "default",
    }
    assert plan["authorization"] == {
        "provider": "openshift_scc_access",
        "constraint": "privileged",
        "service_account": "default",
        "cluster_role": "system:openshift:scc:privileged",
        "behavior": "reuse_existing_or_create_temporary_role_binding",
    }
    assert plan["side_effects_on_apply"] == [
        "temporary_namespace",
        "temporary_scc_role_binding_when_required",
        "temporary_configmap",
        "temporary_job",
    ]


def test_scc_access_action_uses_the_selected_constraint_without_allowlist() -> None:
    """Keep the shared SCC callback independent from the Podman consumer.

    :returns: None; the plan and native RoleBinding retain caller-selected names.
    """
    action = SCC_ACCESS.SCCAccessAction(
        constraint="nonroot.example.com",
        service_account="builder",
    )
    scope = RUN_SCOPE.RunScope.new(run_id="scc-action")

    assert action.plan() == {
        "provider": "openshift_scc_access",
        "constraint": "nonroot.example.com",
        "service_account": "builder",
        "cluster_role": "system:openshift:scc:nonroot.example.com",
        "behavior": "reuse_existing_or_create_temporary_role_binding",
    }
    binding = SCC_ACCESS.role_binding_document(action, scope)
    assert binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "builder",
            "namespace": scope.namespace,
        }
    ]
    assert binding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "ClusterRole",
        "name": "system:openshift:scc:nonroot.example.com",
    }
    second = SCC_ACCESS.role_binding_document(
        SCC_ACCESS.SCCAccessAction("restricted", "builder"),
        scope,
    )
    assert binding["metadata"]["name"] != second["metadata"]["name"]


def test_scc_binding_name_distinguishes_service_accounts_after_bounding() -> None:
    """Keep every SCC and ServiceAccount permission identity independent.

    :returns: None; long caller names remain bounded, stable and distinct.
    """
    scope = RUN_SCOPE.RunScope.new(run_id="scc-account")
    constraint = "s" * 200
    first = SCC_ACCESS.SCCAccessAction(constraint, "builder-a")
    second = SCC_ACCESS.SCCAccessAction(constraint, "builder-b")

    first_name = SCC_ACCESS.role_binding_document(first, scope)["metadata"]["name"]
    second_name = SCC_ACCESS.role_binding_document(second, scope)["metadata"]["name"]

    assert (
        first_name == SCC_ACCESS.role_binding_document(first, scope)["metadata"]["name"]
    )
    assert first_name != second_name
    assert len(first_name) <= RUN_SCOPE.DNS_LABEL_LIMIT
    assert len(second_name) <= RUN_SCOPE.DNS_LABEL_LIMIT


def _node(
    name: str,
    *,
    ready: bool = True,
    worker: bool = True,
    control_plane: bool = False,
    unschedulable: bool = False,
) -> JSONValue:
    """Build one native Node fragment for worker selection tests.

    :param name: Node name.
    :param ready: Whether the Ready condition is true.
    :param worker: Whether a worker role label is present.
    :param control_plane: Whether a control-plane role label is present.
    :param unschedulable: Whether the Node is cordoned.
    :returns: JSON Node fragment accepted by ``select_ready_worker``.
    """
    labels = {}
    if worker:
        labels["node-role.kubernetes.io/worker"] = ""
    if control_plane:
        labels["node-role.kubernetes.io/control-plane"] = ""
    return {
        "metadata": {"name": name, "labels": labels},
        "spec": {"unschedulable": unschedulable},
        "status": {
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
            "nodeInfo": {
                "kernelVersion": "6.8.0-unit",
                "osImage": "Unit Linux",
            },
        },
    }


def test_ready_worker_discovery_is_sorted_and_excludes_ineligible_nodes() -> None:
    """Choose the first named Ready worker after filtering control and cordoned Nodes.

    :returns: None; the selected node is asserted directly.
    """
    document = {
        "items": [
            _node("worker-z"),
            _node("worker-a"),
            _node("worker-down", ready=False),
            _node("worker-cordoned", unschedulable=True),
            _node("control-a", worker=False, control_plane=True),
        ]
    }

    discovered = PODMAN.select_ready_worker(document, None)
    requested = PODMAN.select_ready_worker(document, "worker-z")

    assert discovered == PODMAN.ReadyWorker(
        name="worker-a",
        kernel_version="6.8.0-unit",
        os_image="Unit Linux",
    )
    assert requested.evidence() == {
        "name": "worker-z",
        "kernelVersion": "6.8.0-unit",
        "osImage": "Unit Linux",
    }


def test_requested_node_must_be_a_ready_schedulable_worker() -> None:
    """Reject an exact node request that cannot run the probe.

    :returns: None; the classified Kubernetes error is asserted.
    """
    document = {"items": [_node("worker-a"), _node("worker-b", ready=False)]}

    with pytest.raises(
        KUBERNETES.KubernetesError, match="requested_node_not_ready_worker"
    ):
        PODMAN.select_ready_worker(document, "worker-b")


def test_probe_job_is_bounded_owned_and_pinned_to_selected_worker(
    tmp_path: Path,
) -> None:
    """Render the exact privileged one-shot Job constraints approved for the probe.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; assertions cover ownership, scheduling, resources, and storage.
    """
    action = _action(tmp_path)
    scope = RUN_SCOPE.RunScope.new(namespace="unit-namespace", run_id="unit123")

    document = PODMAN.probe_job(action, scope, "worker-a")
    metadata = document["metadata"]
    assert isinstance(metadata, dict)
    spec = document["spec"]
    assert isinstance(spec, dict)
    template = spec["template"]
    assert isinstance(template, dict)
    pod_spec = template["spec"]
    assert isinstance(pod_spec, dict)
    containers = pod_spec["containers"]
    assert isinstance(containers, list)
    assert len(containers) == 1
    container = containers[0]
    assert isinstance(container, dict)

    assert metadata["namespace"] == scope.namespace
    assert metadata["labels"] == {
        "ci-skills.spyroot.dev/run-id": "unit123",
        "app.kubernetes.io/name": "image_build_readiness",
    }
    assert spec["backoffLimit"] == 0
    assert spec["activeDeadlineSeconds"] == 300
    assert spec["ttlSecondsAfterFinished"] == 600
    assert pod_spec["nodeName"] == "worker-a"
    assert pod_spec["restartPolicy"] == "Never"
    assert pod_spec["serviceAccountName"] == "default"
    assert pod_spec["automountServiceAccountToken"] is False
    assert container["image"] == action.probe_image
    assert container["securityContext"] == {
        "privileged": True,
        "runAsUser": 0,
        "runAsNonRoot": False,
    }
    assert container["resources"] == {
        "requests": {"cpu": "100m", "memory": "128Mi"},
        "limits": {"cpu": "1", "memory": "512Mi"},
    }
    configmap = PODMAN.probe_configmap(scope)
    assert configmap["immutable"] is True
    assert configmap["metadata"]["namespace"] == scope.namespace
    assert configmap["metadata"]["labels"] == dict(scope.labels)
    assert configmap["data"] == PODMAN.probe_scripts()
    assert pod_spec["volumes"] == [
        {
            "name": PODMAN.PROBE_VOLUME,
            "configMap": {
                "name": configmap["metadata"]["name"],
                "defaultMode": 0o444,
            },
        },
        {
            "name": "containers-storage",
            "emptyDir": {"sizeLimit": "2Gi"},
        },
        {"name": "containers-runroot", "emptyDir": {}},
        {"name": "evidence", "emptyDir": {"sizeLimit": "16Mi"}},
    ]
    assert container["command"] == [
        "/bin/bash",
        f"{PODMAN.PROBE_MOUNT}/{PODMAN.PROBE_SCRIPT_NAME}",
    ]
    environment = {item["name"]: item["value"] for item in container["env"]}
    assert environment["PROBE_RUNTIME_DIR"] == PODMAN.PROBE_MOUNT
    assert environment["EVIDENCE_DIR"] == PODMAN.PROBE_EVIDENCE_MOUNT
    assert environment["EXPECTED_INNER_EXIT"] == str(
        PODMAN_EVIDENCE.EXPECTED_INNER_EXIT
    )
    mounts = container["volumeMounts"]
    scripts_mount = next(
        mount for mount in mounts if mount["name"] == PODMAN.PROBE_VOLUME
    )
    assert scripts_mount == {
        "name": PODMAN.PROBE_VOLUME,
        "mountPath": PODMAN.PROBE_MOUNT,
        "readOnly": True,
    }


def _pod_list(job_uid: str, exit_code: int = 0) -> JSONValue:
    """Build one Job-owned Pod termination response.

    :param job_uid: Exact controller UID referenced by the Pod.
    :param exit_code: Terminated probe-container status.
    :returns: Native PodList fragment.
    """
    return {
        "items": [
            {
                "metadata": {
                    "name": "probe-pod",
                    "uid": "pod-uid",
                    "ownerReferences": [
                        {
                            "kind": "Job",
                            "uid": job_uid,
                            "controller": True,
                        }
                    ],
                },
                "status": {
                    "containerStatuses": [
                        {
                            "name": "podman-probe",
                            "imageID": "sha256:runtime-image",
                            "state": {"terminated": {"exitCode": exit_code}},
                        }
                    ]
                },
            }
        ]
    }


@pytest.mark.parametrize("exit_code", (0, 7, 137))
def test_pod_termination_keeps_native_exit_code_authoritative(exit_code: int) -> None:
    """Read the actual probe container status without deriving success from logs.

    :param exit_code: Success, expected nested failure, or interrupted process code.
    :returns: None; the extracted termination fields are asserted.
    """
    termination = PODMAN_EVIDENCE.pod_termination(
        _pod_list("job-uid", exit_code),
        "job-uid",
    )

    assert termination is not None
    assert termination.pod_name == "probe-pod"
    assert termination.pod_uid == "pod-uid"
    assert termination.exit_code == exit_code
    assert termination.image_id == "sha256:runtime-image"


def test_pod_termination_rejects_a_pod_owned_by_another_job() -> None:
    """Refuse evidence from a Pod whose controller UID does not match the Job.

    :returns: None; the ownership mismatch is the behavior under test.
    """
    with pytest.raises(KUBERNETES.KubernetesError, match="probe_pod_owner_mismatch"):
        PODMAN_EVIDENCE.pod_termination(_pod_list("other-job"), "job-uid")


def _complete_log(**changes: str) -> str:
    """Build one complete set of unique probe marker lines.

    :param **changes: Marker values replacing valid defaults.
    :returns: Newline-delimited probe log.
    """
    values = {
        "kernel": "Linux 6.8",
        "podman_version": "podman version 5.0",
        "storage": (
            "driver=overlay;mount_program=none;cgroup_manager=cgroupfs;"
            "graph_root=/var/lib/containers/storage;cgroup_version=v2"
        ),
        "backing_filesystem": "xfs",
        "inner_runtime": (f"id={'a' * 64};marker=inner-ok;cgroup=0::/;root=/"),
        "bind_readback": "ready",
        "expected_exit": "7",
        "remaining_containers": "none",
    }
    values.update(changes)
    return "\n".join(f"CI_SKILLS_PROBE|{key}|{value}" for key, value in values.items())


def _without_marker(name: str) -> str:
    """Remove one required marker from otherwise successful evidence.

    :param name: Exact marker name to omit.
    :returns: Probe log with every other required marker retained.
    """
    return "\n".join(
        line
        for line in _complete_log().splitlines()
        if not line.startswith(f"CI_SKILLS_PROBE|{name}|")
    )


class FakeKubernetes:
    """In-memory Kubernetes boundary for callback lifecycle tests."""

    def __init__(
        self,
        target: Target,
        *,
        pod_exit: int = 0,
        log: Optional[str] = None,
        scc_rejected: bool = False,
        openshift_api: Optional[bool] = None,
        scc_available: bool = False,
        scc_access: bool = False,
        denied_authorization: Optional[str] = None,
        delete_failure: Optional[str] = None,
        namespace_readback_failure: bool = False,
    ):
        """Configure provider observations and optional cleanup failure.

        :param target: Kubernetes-only target exposed to callback configuration.
        :param pod_exit: Probe Pod termination exit code.
        :param log: Exact probe log; omitted means a complete successful log.
        :param scc_rejected: Whether Pod creation is rejected by SCC admission.
        :param openshift_api: Whether SCC API discovery identifies OpenShift;
            omitted follows ``scc_available``.
        :param scc_available: Whether the OpenShift SCC ClusterRole exists.
        :param scc_access: Whether the probe account already has SCC use.
        :param denied_authorization: Authorization verb denied to the caller.
        :param delete_failure: Resource kind whose cleanup should fail.
        :param namespace_readback_failure: Whether the post-apply Namespace read fails.
        """
        self.target = target
        self.timeout = 30
        self.pod_exit = pod_exit
        self.log = _complete_log() if log is None else log
        self.scc_rejected = scc_rejected
        self.openshift_api = scc_available if openshift_api is None else openshift_api
        self.scc_available = scc_available
        self.scc_access = scc_access
        self.denied_authorization = denied_authorization
        self.delete_failure = delete_failure
        self.namespace_readback_failure = namespace_readback_failure
        self.resources: MutableMapping[tuple[str, str, str], JSONObject] = {}
        self.mutations: list[tuple[str, str, str]] = []
        self.deletions: list[tuple[str, str, str]] = []
        self.commands: list[tuple[str, ...]] = []
        if scc_available:
            cluster_role = SCC_ACCESS.SCC_CLUSTER_ROLE_PREFIX + "privileged"
            self.resources[("securitycontextconstraint", "privileged", "")] = {
                "apiVersion": "security.openshift.io/v1",
                "kind": "SecurityContextConstraint",
                "metadata": {"name": "privileged", "uid": "scc-uid"},
            }
            self.resources[("clusterrole", cluster_role, "")] = {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "ClusterRole",
                "metadata": {"name": cluster_role, "uid": "clusterrole-uid"},
            }

    def read(self, kind: str, name: str, namespace: str) -> Optional[JSONObject]:
        """Return a copy of one retained resource.

        :param kind: Native resource kind.
        :param name: Native resource name.
        :param namespace: Resource namespace or empty cluster scope.
        :returns: Copied object, or ``None`` when absent.
        """
        key = (kind.casefold(), name, namespace)
        value = self.resources.get(key)
        if (
            self.namespace_readback_failure
            and kind.casefold() == "namespace"
            and value is not None
        ):
            self.namespace_readback_failure = False
            raise RuntimeError("fixture_namespace_readback_failed")
        return None if value is None else deepcopy(value)

    def apply(self, document: JSONObject) -> JSONObject:
        """Store one generated resource with a provider-owned UID.

        :param document: Resource selected by ``ResourceSet``.
        :returns: Copied provider read-back.
        """
        observed = deepcopy(document)
        metadata = observed["metadata"]
        assert isinstance(metadata, dict)
        kind = observed["kind"]
        assert isinstance(kind, str)
        name = metadata["name"]
        assert isinstance(name, str)
        namespace = metadata.get("namespace", "")
        assert isinstance(namespace, str)
        metadata["uid"] = f"{kind.casefold()}-uid"
        key = (kind.casefold(), name, namespace)
        self.resources[key] = observed
        self.mutations.append((kind, name, namespace))
        return deepcopy(observed)

    def delete(self, kind: str, name: str, namespace: str) -> None:
        """Delete one selected object and expose the verified absence.

        :param kind: Native resource kind.
        :param name: Native resource name.
        :param namespace: Resource namespace or empty cluster scope.
        :returns: None; retained resources expose the outcome.
        :raises RuntimeError: When this kind is configured to fail cleanup.
        """
        self.deletions.append((kind, name, namespace))
        if kind.casefold() == self.delete_failure:
            raise RuntimeError("fixture_cleanup_failed")
        self.resources.pop((kind.casefold(), name, namespace), None)

    def json(self, *args: str, openshift: bool = False) -> JSONValue:
        """Return nodes, exact Job Pods, or namespace Events.

        :param *args: Native command arguments requested by the callback.
        :param openshift: Unused native-client selector.
        :returns: Synthetic provider document for the requested resource.
        :raises AssertionError: If the callback requests an undeclared read.
        """
        assert openshift is False
        if args == ("get", "nodes", "-o", "json"):
            return {"items": [_node("worker-a")]}
        if args[:2] == ("get", "namespaces"):
            return {"items": self._labeled("namespace", args)}
        scope_resources = {
            "pods": "pod",
            "configmaps": "configmap",
            "deployments": "deployment",
            "rolebindings": "rolebinding",
        }
        if (
            len(args) >= 2
            and args[0] == "get"
            and args[1] in scope_resources
            and "--all-namespaces" in args
        ):
            return {"items": self._labeled(scope_resources[args[1]], args)}
        if "pods" in args:
            if self.scc_rejected:
                return {"items": []}
            return _pod_list("job-uid", self.pod_exit)
        if "events" in args:
            items = []
            if self.scc_rejected:
                items.append(
                    {
                        "reason": "FailedCreate",
                        "message": (
                            "unable to validate against any security context constraint"
                        ),
                        "involvedObject": {"uid": "job-uid"},
                    }
                )
            return {"items": items}
        raise AssertionError(f"unexpected provider read: {args}")

    def _labeled(self, kind: str, args: tuple[str, ...]) -> list[JSONObject]:
        """Return retained objects matching one exact native label selector.

        :param kind: Case-normalized resource kind selected by a list operation.
        :param args: Native list arguments containing one ``-l`` selector.
        :returns: Deep copies of matching retained objects.
        """
        selector = args[args.index("-l") + 1]
        expected = dict(item.split("=", 1) for item in selector.split(","))
        result = []
        for (resource_kind, _name, _namespace), document in self.resources.items():
            if resource_kind != kind:
                continue
            metadata = document.get("metadata")
            labels = metadata.get("labels", {}) if isinstance(metadata, dict) else {}
            if isinstance(labels, dict) and all(
                labels.get(key) == value for key, value in expected.items()
            ):
                result.append(deepcopy(document))
        return result

    def command(self, *args: str, openshift: bool = False) -> CommandResult:
        """Return bounded discovery, authorization, or probe-log output.

        :param *args: Native provider arguments selected by the callback.
        :param openshift: Whether the request uses the OpenShift CLI.
        :returns: Configured discovery, authorization, or log result.
        """
        self.commands.append(args)
        if args == ("get", "--raw=/apis/security.openshift.io/v1"):
            if not self.openshift_api:
                return RUNTIME.CommandResult(
                    ("kubectl", *args),
                    1,
                    "",
                    "server returned 404 not found\n",
                )
            return RUNTIME.CommandResult(
                ("kubectl", *args),
                0,
                json.dumps(
                    {
                        "kind": "APIResourceList",
                        "groupVersion": "security.openshift.io/v1",
                        "resources": [{"name": "securitycontextconstraints"}],
                    }
                ),
                "",
            )
        if args[:2] == ("auth", "can-i"):
            assert openshift is True
            verb = args[2]
            if self.denied_authorization == verb:
                return RUNTIME.CommandResult(("oc", *args), 1, "no\n", "")
            if verb == "use":
                permitted = self.scc_access or any(
                    kind == "rolebinding"
                    and namespace == args[args.index("--namespace") + 1]
                    for kind, _name, namespace in self.resources
                )
                return RUNTIME.CommandResult(
                    ("oc", *args),
                    0 if permitted else 1,
                    "yes\n" if permitted else "no\n",
                    "",
                )
            return RUNTIME.CommandResult(("oc", *args), 0, "yes\n", "")
        assert openshift is False
        return RUNTIME.CommandResult(("kubectl", *args), 0, self.log, "")


def _executor(
    action: PodmanProbeAction,
    service: FakeKubernetes,
) -> WorkflowExecutor[PodmanProbeState]:
    """Create the production callback sequence over one in-memory service.

    :param action: Concrete probe action shared by every callback.
    :param service: Fake Kubernetes boundary retained in shared state.
    :returns: Workflow executor bound to the concrete Podman state.
    """
    callbacks = [
        PODMAN_CALLBACKS.SelectReadyWorker(action),
        PODMAN_CALLBACKS.ConfirmPodmanProbe(action),
        PODMAN_CALLBACKS.OpenProbeNamespace(action),
        SCC_ACCESS.EnsureSCCAccess(action.scc_access()),
        PODMAN_CALLBACKS.RunPodmanProbe(action),
    ]
    state = PODMAN_CALLBACKS.PodmanProbeState()
    bind_kubernetes_fixture(state, service)
    return EXECUTOR.WorkflowExecutor(state, CALLBACKS.Callbacks(callbacks))


def _reported_apply_status(state: PodmanProbeState) -> str:
    """Classify one callback state through the production command controller.

    :param state: State produced by the callback executor.
    :returns: Public PASS, PARTIAL, or BLOCKED apply status.
    """
    controller = READINESS.ImageBuildReadiness(
        READINESS.build_parser().parse_args(["--json"])
    )
    controller.state = state
    return controller._apply_status()


@pytest.mark.parametrize(
    ("role", "reason"),
    (
        (None, "kubernetes_identity_preflight_missing"),
        ("viewer", "kubernetes_required_role_missing:cluster-admin"),
    ),
)
def test_probe_rejects_unestablished_or_insufficient_role_before_provider_operations(
    tmp_path: Path, role: Optional[str], reason: str
) -> None:
    """A configured workflow still requires retained cluster administration.

    :param tmp_path: Isolated target path used by the existing probe fixture.
    :param role: Missing or insufficient identity observation supplied by the caller.
    :param reason: Required failure before provider operations or probe resource writes.
    :returns: None; provider journals prove no operation crossed the access gate.
    """
    service = FakeKubernetes(_target(tmp_path))
    executor = _executor(_apply_action(tmp_path), service)
    executor.state.kubernetes_runtime = replace(
        executor.state.kubernetes_runtime, role=role
    )

    with pytest.raises(ValueError, match=reason):
        executor.run()

    assert service.commands == []
    assert service.mutations == []
    assert service.deletions == []
    assert service.resources == {}


def test_marker_parser_proves_nested_run_exit_propagation_and_cleanup() -> None:
    """Accept one complete unique marker set with expected exit and empty cleanup.

    :returns: None; marker rows and aggregate verdict are asserted.
    """
    evidence = PODMAN_EVIDENCE.parse_probe_markers(_complete_log())

    assert evidence.verified is True
    checks = [row["check"] for row in evidence.rows]
    assert checks == list(PODMAN_EVIDENCE.REQUIRED_MARKERS)
    assert len(checks) == 8
    assert "probe_exit" not in checks
    assert all(row["status"] == "PASS" for row in evidence.rows)
    assert evidence.values["expected_exit"] == "7"
    assert evidence.values["remaining_containers"] == "none"


def test_marker_parser_exposes_missing_and_failed_evidence_as_rows() -> None:
    """Report an absent row and a cleanup failure without silent success.

    :returns: None; blocked rows remain independently diagnosable.
    """
    lines = [
        line
        for line in _complete_log(remaining_containers="leaked-id").splitlines()
        if "|bind_readback|" not in line
    ]

    evidence = PODMAN_EVIDENCE.parse_probe_markers("\n".join(lines))
    rows = {row["check"]: row for row in evidence.rows}

    assert evidence.verified is False
    assert rows["bind_readback"] == {
        "check": "bind_readback",
        "status": "BLOCKED",
        "observed": "missing",
    }
    assert rows["remaining_containers"]["status"] == "BLOCKED"
    assert rows["remaining_containers"]["observed"] == "leaked-id"


def test_marker_parser_rejects_duplicate_evidence() -> None:
    """Reject ambiguous duplicate markers instead of choosing one value.

    :returns: None; duplicate rejection is the behavior under test.
    """
    log = _complete_log() + "\nCI_SKILLS_PROBE|kernel|Linux duplicate"

    with pytest.raises(ValueError, match="probe_marker_duplicate"):
        PODMAN_EVIDENCE.parse_probe_markers(log)


def test_marker_parser_does_not_replace_native_exit_with_printed_exit() -> None:
    """Ignore a legacy printed exit marker when all required evidence passes.

    :returns: None; the authoritative Pod termination remains a callback concern.
    """
    evidence = PODMAN_EVIDENCE.parse_probe_markers(
        _complete_log() + "\nCI_SKILLS_PROBE|probe_exit|1"
    )

    assert evidence.verified is True
    assert "probe_exit" not in [row["check"] for row in evidence.rows]


def test_scc_rejection_uses_actual_event_for_the_exact_job() -> None:
    """Classify SCC admission only when the generated Job emitted the event.

    :returns: None; unrelated events are ignored and no grant is requested.
    """
    events = {
        "items": [
            {
                "reason": "FailedCreate",
                "message": "unable to validate against any security context constraint",
                "involvedObject": {"uid": "other-job"},
            },
            {
                "reason": "FailedCreate",
                "message": "unable to validate against any security context constraint",
                "involvedObject": {"uid": "job-uid"},
            },
        ]
    }

    assert PODMAN_EVIDENCE.scc_admission_reason(events, "job-uid") == "scc_required"
    assert PODMAN_EVIDENCE.scc_admission_reason(events, "missing-job") is None


def _apply_action(
    tmp_path: Path,
    *,
    confirmation: Optional[str] = None,
    namespace: Optional[str] = None,
) -> PodmanProbeAction:
    """Build an apply action confirmed for the selected fixture worker.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :param confirmation: Explicit digest override; omitted computes the valid digest.
    :param namespace: Existing caller-owned namespace, or ``None`` for generation.
    :returns: Apply-mode probe action.
    """
    planned = replace(_action(tmp_path), namespace=namespace)
    digest = planned.fingerprint("worker-a") if confirmation is None else confirmation
    return replace(
        planned,
        mode=PODMAN.ProbeMode.APPLY,
        confirm_plan=digest,
    )


@pytest.mark.parametrize(
    "changed_scripts", (False, True), ids=("bad-digest", "script-change")
)
def test_confirm_mismatch_blocks_before_any_resource_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_scripts: bool,
) -> None:
    """Refuse stale confirmation or changed script bytes before resource creation.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :param monkeypatch: Fixture replacing installed script bytes after confirmation.
    :param changed_scripts: Whether only script bytes invalidate a previously valid plan.
    :returns: None; no namespace, ConfigMap, or Job was created.
    """
    service = FakeKubernetes(_target(tmp_path))
    action = _apply_action(
        tmp_path, confirmation=None if changed_scripts else "stale-plan"
    )
    if changed_scripts:
        scripts = PODMAN.probe_scripts()
        scripts[PODMAN.PROBE_SCRIPT_NAME] += "\n# changed after confirmation\n"
        monkeypatch.setattr(PODMAN, "probe_scripts", lambda: scripts)
    executor = _executor(action, service)

    with pytest.raises(ValueError, match="confirm_plan_mismatch_rerun_live_plan"):
        executor.run()

    assert _reported_apply_status(executor.state) == "BLOCKED"
    assert service.mutations == []
    assert service.deletions == []
    assert service.resources == {}


def test_apply_uses_and_preserves_caller_selected_namespace(tmp_path: Path) -> None:
    """Run in an existing namespace without adopting or deleting it.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; the plan, Job account, and cleanup retain caller ownership.
    """
    namespace = "existing-podman"
    action = _apply_action(tmp_path, namespace=namespace)
    service = FakeKubernetes(_target(tmp_path))
    service.resources[("namespace", namespace, "")] = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": namespace, "uid": "existing-namespace-uid"},
    }
    executor = _executor(action, service)

    executor.run()

    assert action.plan("worker-a")["run_scope"] == {
        "namespace_mode": "selected",
        "namespace": namespace,
        "service_account": "default",
    }
    assert action.plan("worker-a")["side_effects_on_apply"] == [
        "temporary_scc_role_binding_when_required",
        "temporary_configmap",
        "temporary_job",
    ]
    assert executor.state.run_scope.owns_namespace is False
    assert executor.state.probe_record["namespace"] == namespace
    assert executor.state.probe_record["service_account"] == (
        f"system:serviceaccount:{namespace}:default"
    )
    assert [kind for kind, _name, _namespace in service.mutations] == [
        "ConfigMap",
        "Job",
    ]
    assert [kind for kind, _name, _namespace in service.deletions] == [
        "Job",
        "ConfigMap",
    ]
    assert service.read("Namespace", namespace, "") is not None


def test_apply_verifies_probe_then_cleans_job_before_namespace(tmp_path: Path) -> None:
    """Run the complete callback chain and verify reverse cleanup with no leftovers.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; result and cleanup evidence are read from shared state.
    """
    service = FakeKubernetes(_target(tmp_path))
    executor = _executor(_apply_action(tmp_path), service)

    executor.run()

    state = executor.state
    assert state.probe_record["verified"] is True
    assert state.probe_record["nested_runtime_ready"] is True
    assert state.probe_record["node"] == {
        "name": "worker-a",
        "kernelVersion": "6.8.0-unit",
        "osImage": "Unit Linux",
    }
    assert _reported_apply_status(state) == "PASS"
    pod = state.probe_record["pod"]
    assert isinstance(pod, dict)
    assert pod["exit_code"] == 0
    assert [kind for kind, _name, _namespace in service.mutations] == [
        "Namespace",
        "ConfigMap",
        "Job",
    ]
    assert [item["kind"] for item in state.cleanup_resources] == [
        "Job",
        "ConfigMap",
        "Namespace",
    ]
    assert [kind for kind, _name, _namespace in service.deletions] == [
        "Job",
        "ConfigMap",
        "Namespace",
    ]
    assert service.resources == {}


@pytest.mark.parametrize("scc_rejected", (False, True), ids=("pass", "scc-required"))
def test_emitted_apply_report_matches_shipped_result_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scc_rejected: bool,
) -> None:
    """Validate actual successful and admission-blocked command reports offline.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :param monkeypatch: Fixture replacing configuration and the provider boundary.
    :param capsys: Fixture capturing the real JSON result emitter.
    :param scc_rejected: Whether the fake provider denies privileged Pod admission.
    :returns: None; emitted status, runtime claim, cleanup, and schema are asserted.
    """
    action = _apply_action(tmp_path)
    service = FakeKubernetes(action.target, scc_rejected=scc_rejected)
    executor = _executor(action, service)
    controller = READINESS.ImageBuildReadiness(
        READINESS.build_parser().parse_args(
            ["--apply", "--confirm-plan", action.confirm_plan, "--json"]
        )
    )
    controller.action = action
    controller.target = replace(action.target, tested_revision="a" * 40)
    controller.state = executor.state
    monkeypatch.setattr(controller, "_prepare", lambda mode: mode)
    monkeypatch.setattr(READINESS, "workflow", lambda _action, _state: executor)

    exit_code = controller.execute()
    output = json.loads(capsys.readouterr().out)
    schema_root = SKILL_ROOT / "schemas" / "results"
    schema = json.loads((schema_root / "image-build-readiness.schema.json").read_text())
    tag_schema = json.loads((schema_root / "gitlab-runner-tag.schema.json").read_text())
    registry = Registry().with_resource(
        tag_schema["$id"], Resource.from_contents(tag_schema)
    )
    Draft202012Validator(schema, registry=registry).validate(output)

    assert exit_code == (2 if scc_rejected else 0)
    assert output["status"] == ("BLOCKED" if scc_rejected else "PASS")
    probe_records = [
        item for item in output["records"] if item.get("callback") == "RunPodmanProbe"
    ]
    assert len(probe_records) == 1
    record = probe_records[0]
    assert record["outcome"] == ("scc_required" if scc_rejected else "ready")
    assert record["verified"] is (not scc_rejected)
    assert output["cleanup"]["status"] == "PASS"
    assert service.resources == {}


def test_namespace_ambiguous_apply_is_removed_after_readback_failure(
    tmp_path: Path,
) -> None:
    """Clean a Namespace dispatched before its provider read-back failed.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; the original read-back failure propagates after cleanup.
    """
    service = FakeKubernetes(
        _target(tmp_path),
        namespace_readback_failure=True,
    )
    executor = _executor(_apply_action(tmp_path), service)

    with pytest.raises(RuntimeError, match="fixture_namespace_readback_failed"):
        executor.run()

    assert len(service.mutations) == 1
    assert service.mutations[0][0] == "Namespace"
    assert len(service.deletions) == 1
    assert service.deletions[0][0] == "Namespace"
    assert service.resources == {}


@pytest.mark.parametrize(
    ("pod_exit", "log"),
    (
        (7, _complete_log()),
        (0, _complete_log(bind_readback="blocked")),
        (0, _without_marker("bind_readback")),
    ),
    ids=("pod-exit-decisive", "failed-required-marker", "missing-required-marker"),
)
def test_probe_failure_still_cleans_every_created_resource(
    tmp_path: Path, pod_exit: int, log: str
) -> None:
    """Keep the native Pod exit decisive and clean failed probe resources.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :param pod_exit: Native probe-container termination status.
    :param log: Complete marker log, optionally carrying a failed invariant.
    :returns: None; shared result and cleanup state are asserted.
    """
    service = FakeKubernetes(_target(tmp_path), pod_exit=pod_exit, log=log)
    executor = _executor(_apply_action(tmp_path), service)

    executor.run()

    assert executor.state.probe_record["verified"] is False
    assert executor.state.probe_record["nested_runtime_ready"] is False
    pod = executor.state.probe_record["pod"]
    assert isinstance(pod, dict)
    assert pod["exit_code"] == pod_exit
    assert _reported_apply_status(executor.state) == "PARTIAL"
    assert service.resources == {}


def test_scc_access_reuses_existing_authorization_without_binding(
    tmp_path: Path,
) -> None:
    """Preserve an existing SCC grant and create no run-owned binding.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; permission and resource evidence prove a verified no-op.
    """
    service = FakeKubernetes(
        _target(tmp_path),
        scc_available=True,
        scc_access=True,
    )
    executor = _executor(_apply_action(tmp_path), service)

    executor.run()

    permission = next(
        value
        for callback, value in executor.state.results.items()
        if isinstance(callback, SCC_ACCESS.EnsureSCCAccess)
    )
    assert permission["action"] == "NO_OP"
    assert permission["permission_source"] == "existing"
    assert not any(kind == "RoleBinding" for kind, _name, _ns in service.mutations)
    assert not any(kind == "RoleBinding" for kind, _name, _ns in service.deletions)
    assert set(service.resources) == {
        ("securitycontextconstraint", "privileged", ""),
        ("clusterrole", "system:openshift:scc:privileged", ""),
    }


def test_scc_access_is_not_applicable_only_when_api_is_absent(
    tmp_path: Path,
) -> None:
    """Keep native Kubernetes independent from OpenShift SCC resources.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; provider discovery is the only not-applicable condition.
    """
    service = FakeKubernetes(_target(tmp_path))
    executor = _executor(_apply_action(tmp_path), service)

    executor.run()

    access = next(
        value
        for callback, value in executor.state.results.items()
        if isinstance(callback, SCC_ACCESS.EnsureSCCAccess)
    )
    assert access["action"] == "NO_OP"
    assert access["outcome"] == "scc_not_available"
    assert access["permission_source"] == "not_applicable"
    assert not any(kind == "RoleBinding" for kind, _name, _ns in service.mutations)


def test_scc_access_creates_reads_and_removes_only_run_binding(
    tmp_path: Path,
) -> None:
    """Prepare missing SCC use through one temporary owned RoleBinding.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; apply, same-target authorization and reverse cleanup are asserted.
    """
    service = FakeKubernetes(_target(tmp_path), scc_available=True)
    executor = _executor(_apply_action(tmp_path), service)

    executor.run()

    permission = next(
        value
        for callback, value in executor.state.results.items()
        if isinstance(callback, SCC_ACCESS.EnsureSCCAccess)
    )
    assert permission["action"] == "APPLIED"
    assert permission["permission_source"] == "temporary_role_binding"
    assert permission["role_binding"]["uid"] == "rolebinding-uid"
    assert [kind for kind, _name, _namespace in service.mutations] == [
        "Namespace",
        "RoleBinding",
        "ConfigMap",
        "Job",
    ]
    assert [kind for kind, _name, _namespace in service.deletions] == [
        "Job",
        "ConfigMap",
        "RoleBinding",
        "Namespace",
    ]
    assert [item["kind"] for item in executor.state.cleanup_resources] == [
        "Job",
        "ConfigMap",
        "RoleBinding",
        "Namespace",
    ]
    assert set(service.resources) == {
        ("securitycontextconstraint", "privileged", ""),
        ("clusterrole", "system:openshift:scc:privileged", ""),
    }


def test_scc_access_denial_blocks_before_probe_and_cleans_scope(
    tmp_path: Path,
) -> None:
    """Refuse mutation when the selected identity cannot bind the SCC role.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; no Job starts and the generated namespace is absent.
    """
    service = FakeKubernetes(
        _target(tmp_path),
        scc_available=True,
        denied_authorization="bind",
    )
    executor = _executor(_apply_action(tmp_path), service)

    with pytest.raises(
        KUBERNETES.KubernetesError,
        match="scc_cluster_role_bind_denied",
    ):
        executor.run()

    assert not any(kind == "Job" for kind, _name, _namespace in service.mutations)
    assert [kind for kind, _name, _namespace in service.deletions] == ["Namespace"]
    assert not any(kind == "namespace" for kind, _name, _ns in service.resources)


def test_scc_access_blocks_when_openshift_lacks_selected_scc(
    tmp_path: Path,
) -> None:
    """Do not confuse an absent selected SCC with a non-OpenShift target.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; provider discovery succeeds and Job creation remains blocked.
    """
    service = FakeKubernetes(_target(tmp_path), openshift_api=True)
    executor = _executor(_apply_action(tmp_path), service)

    with pytest.raises(KUBERNETES.KubernetesError, match="scc_not_found"):
        executor.run()

    assert not any(kind == "Job" for kind, _name, _namespace in service.mutations)
    assert [kind for kind, _name, _namespace in service.deletions] == ["Namespace"]


def test_scc_access_blocks_when_openshift_lacks_generated_cluster_role(
    tmp_path: Path,
) -> None:
    """Require the selected SCC's exact generated ClusterRole on OpenShift.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; missing role cannot be mistaken for provider absence.
    """
    service = FakeKubernetes(_target(tmp_path), openshift_api=True)
    service.resources[("securitycontextconstraint", "privileged", "")] = {
        "apiVersion": "security.openshift.io/v1",
        "kind": "SecurityContextConstraint",
        "metadata": {"name": "privileged", "uid": "scc-uid"},
    }
    executor = _executor(_apply_action(tmp_path), service)

    with pytest.raises(
        KUBERNETES.KubernetesError,
        match="scc_cluster_role_missing",
    ):
        executor.run()

    assert not any(kind == "Job" for kind, _name, _namespace in service.mutations)
    assert [kind for kind, _name, _namespace in service.deletions] == ["Namespace"]


@pytest.mark.parametrize(
    "final_termination", (False, True), ids=("no-pod", "final-pod")
)
def test_terminal_job_failure_observes_once_more_without_waiting_for_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    final_termination: bool,
) -> None:
    """Return promptly after a final Pod/Event read when the Job cannot retry.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :param monkeypatch: Fixture replacing native observations and the polling clock.
    :param final_termination: Whether the final Pod read discovers delayed termination.
    :returns: None; final observation priority, no sleep, and cleanup are asserted.
    """
    service = FakeKubernetes(_target(tmp_path))
    read_resource = service.read
    read_json = service.json
    observations: list[str] = []
    cleanup_observations: list[str] = []
    sleeps: list[float] = []
    now = [0.0]

    def read(kind: str, name: str, namespace: str) -> Optional[JSONObject]:
        """Expose a terminal Job after the initial Pod/Event observation.

        :param kind: Resource kind selected by the callback.
        :param name: Exact generated resource name.
        :param namespace: Generated run namespace or cluster scope.
        :returns: Existing resource with a terminal Failed condition on the Job.
        """
        document = read_resource(kind, name, namespace)
        if kind == "Job" and document is not None and observations:
            # The run result is written only after _wait returns. Later Job
            # reads belong to ResourceSet's required cleanup ownership check.
            journal = (
                cleanup_observations
                if hasattr(executor.state, "probe_record")
                else observations
            )
            journal.append("job-failed")
            document["status"] = {"conditions": [{"type": "Failed", "status": "True"}]}
        return document

    def observe(*args: str, openshift: bool = False) -> JSONValue:
        """Observe no Pod until the bounded final read after terminal Job failure.

        :param *args: Requested native resource read.
        :param openshift: Native-client selector, unused by this fixture.
        :returns: Run ownership, node selection, Events, or delayed Pod termination.
        :raises AssertionError: If the callback requests an unexpected resource.
        """
        assert openshift is False
        if "namespaces" in args or "--all-namespaces" in args:
            return read_json(*args, openshift=openshift)
        if "nodes" in args:
            return {"items": [_node("worker-a")]}
        if "pods" in args:
            observations.append("pods")
            if final_termination and "job-failed" in observations:
                return _pod_list("job-uid")
            return {"items": []}
        if "events" in args:
            observations.append("events")
            return {"items": []}
        raise AssertionError(f"unexpected provider read: {args}")

    def advance(seconds: float) -> None:
        """Advance fake time so a regressed wait finishes without a real delay.

        :param seconds: Polling interval requested by the production wait loop.
        :returns: None; the elapsed time and sleep journal are updated.
        """
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(service, "read", read)
    monkeypatch.setattr(service, "json", observe)
    monkeypatch.setattr(PODMAN_CALLBACKS.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(PODMAN_CALLBACKS.time, "sleep", advance)
    executor = _executor(_apply_action(tmp_path), service)

    executor.run()

    assert observations == (
        ["pods", "events", "job-failed", "pods"]
        if final_termination
        else ["pods", "events", "job-failed", "pods", "events"]
    )
    assert cleanup_observations == ["job-failed"]
    assert sleeps == []
    assert now[0] == 0
    assert service.timeout == 30
    assert executor.state.probe_record["outcome"] == (
        "ready" if final_termination else "job_failed_without_termination"
    )
    assert _reported_apply_status(executor.state) == (
        "PASS" if final_termination else "BLOCKED"
    )
    assert service.resources == {}


@pytest.mark.parametrize(
    "outcome",
    ("probe_timeout", "job_failed_without_termination"),
)
def test_pretermination_failure_cleans_without_claiming_runtime_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """Turn no-termination outcomes into blocked records plus cleanup.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :param monkeypatch: Fixture replacing the wait loop with its terminal outcome.
    :param outcome: Timeout or failed Job outcome before Pod termination.
    :returns: None; the blocked record and absence state are asserted.
    """
    service = FakeKubernetes(_target(tmp_path))
    monkeypatch.setattr(
        PODMAN_CALLBACKS.RunPodmanProbe,
        "_wait",
        lambda _self, _scope, _uid: outcome,
    )
    executor = _executor(_apply_action(tmp_path), service)

    executor.run()

    assert executor.state.probe_record["outcome"] == outcome
    assert executor.state.probe_record["verified"] is False
    assert _reported_apply_status(executor.state) == "BLOCKED"
    assert service.resources == {}


def test_interruption_runs_reverse_cleanup_for_entered_callbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Release the temporary Job and namespace when observation is interrupted.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :param monkeypatch: Fixture injecting an interruption after Job creation.
    :returns: None; the propagated interruption and cleanup order are asserted.
    """
    service = FakeKubernetes(_target(tmp_path))

    def interrupted(_callback: RunPodmanProbe, _scope: RunScope, _uid: str) -> NoReturn:
        """Raise the same base exception delivered by an interrupted invocation.

        :param _callback: Run callback accepted by the replacement method.
        :param _scope: Generated run scope accepted by the replacement method.
        :param _uid: Applied Job UID accepted by the replacement method.
        :returns: Never returns.
        :raises KeyboardInterrupt: Always interrupts the observation phase.
        """
        raise KeyboardInterrupt

    monkeypatch.setattr(PODMAN_CALLBACKS.RunPodmanProbe, "_wait", interrupted)
    executor = _executor(_apply_action(tmp_path), service)

    with pytest.raises(KeyboardInterrupt):
        executor.run()

    assert [kind for kind, _name, _namespace in service.deletions] == [
        "Job",
        "ConfigMap",
        "Namespace",
    ]
    assert service.resources == {}


def test_cleanup_failure_blocks_success_but_continues_namespace_cleanup(
    tmp_path: Path,
) -> None:
    """Fail the workflow for a retained Job while still removing its namespace.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; required cleanup failure and continued cleanup are asserted.
    """
    service = FakeKubernetes(_target(tmp_path), delete_failure="job")
    executor = _executor(_apply_action(tmp_path), service)

    with pytest.raises(
        KUBERNETES.KubernetesError,
        match="resource_cleanup_incomplete",
    ):
        executor.run()

    assert [kind for kind, _name, _namespace in service.deletions] == [
        "Job",
        "ConfigMap",
        "Namespace",
    ]
    assert not any(key[0] == "namespace" for key in service.resources)
    assert any(key[0] == "job" for key in service.resources)
    assert executor.state.cleanup_failures[0]["callback"] == "RunPodmanProbe"
    assert _reported_apply_status(executor.state) == "BLOCKED"


def test_probe_help_and_describe_need_no_target_or_native_tools() -> None:
    """Expose human and machine contracts before target resolution.

    :returns: None; child command results prove both offline interfaces.
    """
    help_result = run_script("image_build_readiness.py", "--help")
    describe_result = run_script("image_build_readiness.py", "--describe")

    assert help_result.returncode == 0
    assert "--apply" in help_result.stdout
    assert "--confirm-plan" in help_result.stdout
    assert describe_result.returncode == 0
    description = parse_json_output(describe_result)
    assert description["command"] == "image_build_readiness.py"
    assert description["requires_authorities"] == ["kubernetes"]


@pytest.mark.parametrize("output_name", (None, "requested-reports"))
def test_offline_plan_contacts_no_provider_and_uses_only_requested_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_name: Optional[str],
) -> None:
    """Keep dry-run offline and avoid persistence without a caller-selected path.

    :param tmp_path: Directory used by the Kubernetes-only target and output selection.
    :param monkeypatch: Fixture replacing target selection and final emission.
    :param output_name: Explicit output directory name, or no persistence request.
    :returns: None; provider boundaries fail the test if contacted.
    """
    output_dir = tmp_path / output_name if output_name is not None else None
    argv = ["--dry-run", "--json"]
    if output_dir is not None:
        argv.extend(("--output-dir", str(output_dir)))
    args = READINESS.build_parser().parse_args(argv)
    emitted: list[tuple[JSONObject, Optional[str]]] = []
    monkeypatch.setattr(
        READINESS,
        "resolve_target",
        lambda _target, _binding, *, dry_run, required_surfaces: _target_file_result(
            tmp_path, dry_run, required_surfaces
        ),
    )
    monkeypatch.setattr(
        READINESS,
        "bind_sources",
        lambda *_args, **_kwargs: pytest.fail("dry-run bound live credentials"),
    )
    monkeypatch.setattr(
        READINESS,
        "workflow",
        lambda *_args, **_kwargs: pytest.fail("dry-run constructed live callbacks"),
    )

    def emit(data: JSONObject, selected: Namespace) -> int:
        """Capture the planned report and exact parsed output path.

        :param data: Offline plan report produced by the command.
        :param selected: Parsed command namespace passed to shared emission.
        :returns: Successful command status.
        """
        emitted.append((data, selected.output_dir))
        return 0

    monkeypatch.setattr(READINESS, "emit_result", emit)

    assert READINESS.ImageBuildReadiness(args).execute() == 0
    assert len(emitted) == 1
    data, selected_output = emitted[0]
    assert data["status"] == "DRY_RUN"
    plan = data["plan"]
    assert isinstance(plan, dict)
    assert plan["selected_node"] == "auto"
    assert selected_output == (str(output_dir) if output_dir is not None else None)
    assert list(tmp_path.iterdir()) == []


def test_offline_probe_rejects_receipt_without_persisting_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject a live receipt for an offline plan before target or file access.

    :param tmp_path: Scratch destination that must remain absent.
    :param capsys: Pytest fixture capturing the blocked machine result.
    :returns: None; the mode error and file absence are asserted.
    """
    receipt = tmp_path / "offline-receipt.json"

    result = READINESS.main(["--dry-run", "--receipt-out", str(receipt), "--json"])
    output = json.loads(capsys.readouterr().out)

    assert result == 2
    assert output["status"] == "BLOCKED"
    assert output["errors"] == [
        {
            "source": "arguments",
            "reason": "receipt_out_requires_live_run",
        }
    ]
    assert not receipt.exists()


def _target_file_result(
    tmp_path: Path, dry_run: bool, required_surfaces: Sequence[str]
) -> Target:
    """Validate dry-run resolution inputs and return a Kubernetes-only target.

    :param tmp_path: Directory used by the synthetic target.
    :param dry_run: Resolver mode selected by the command.
    :param required_surfaces: Exact provider set requested by the command.
    :returns: Kubernetes-only target.
    """
    assert dry_run is True
    assert tuple(required_surfaces) == ("kubernetes",)
    return _target(tmp_path)


FAKE_PROBE_PODMAN: Final = r"""import json
import os
import signal
import sys
from pathlib import Path

arguments = sys.argv[1:]
with Path(os.environ["FAKE_JOURNAL"]).open("a", encoding="utf-8") as journal:
    journal.write(json.dumps({"tool": "podman", "argv": arguments}) + "\n")
failure = os.environ["FAKE_PROBE_FAILURE"]
if arguments == ["--version"]:
    if failure == "version":
        print("fixture podman version failed", file=sys.stderr)
        raise SystemExit(23)
    if failure == "term":
        os.kill(int(os.environ["FAKE_PROBE_PARENT"]), signal.SIGTERM)
    print("podman version 5.0")
    raise SystemExit(0)
command = next(
    (item for item in arguments if item in {"info", "run", "inspect", "rm", "ps"}),
    None,
)
if command == "info":
    template = arguments[arguments.index("--format") + 1]
    print("none" if "GraphOptions" in template else
          "overlay|/var/lib/containers/storage|cgroupfs|v2")
elif command == "run":
    name = arguments[arguments.index("--name") + 1]
    if name == os.environ["FAIL_NAME"]:
        raise SystemExit(7)
    Path(os.environ["EVIDENCE_DIR"], "inner.txt").write_text("ready", encoding="utf-8")
    print("marker=inner-ok;cgroup=0::/;root=/")
elif command == "inspect":
    print("a" * 64)
elif command == "rm":
    if failure == "cleanup":
        print("fixture cleanup failed after removal", file=sys.stderr)
        raise SystemExit(31)
elif command == "ps":
    pass
else:
    print(f"unexpected podman arguments: {arguments!r}", file=sys.stderr)
    raise SystemExit(99)
"""


def _run_probe_script(
    tmp_path: Path,
    fake_bin: Path,
    call_journal: Path,
    failure: str,
) -> tuple[subprocess.CompletedProcess[str], Sequence[JSONObject]]:
    """Run shipped probe script bytes with only native external tools replaced.

    :param tmp_path: Test-owned script bundle, evidence, and working directory.
    :param fake_bin: Directory for fake podman and filesystem-info commands.
    :param call_journal: Sanitized native-call journal consumed after execution.
    :param failure: Selected external-command failure or normal successful behavior.
    :returns: Actual Bash exit/streams and recorded Podman invocations.
    """
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    for name, body in PODMAN.probe_scripts().items():
        assert isinstance(body, str)
        (script_dir / name).write_text(body, encoding="utf-8")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    install_executable(fake_bin, "podman", f"#!{sys.executable}\n{FAKE_PROBE_PODMAN}")
    install_executable(fake_bin, "stat", f"#!{sys.executable}\nprint('xfs')\n")
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "EVIDENCE_DIR": str(evidence),
        "PROBE_RUNTIME_DIR": str(script_dir),
        "EXPECTED_INNER_EXIT": str(PODMAN_EVIDENCE.EXPECTED_INNER_EXIT),
        "CI_LOG_LEVEL": "info",
        "CI_LOG_FORMAT": "text",
        "CI_LOG_FILE": "",
        "CI_SKILLS_RUNTIME_LOADED": "0",
        "INNER_IMAGE": _action(tmp_path).inner_image,
        "OK_NAME": "unit-inner-ok",
        "FAIL_NAME": "unit-inner-fail",
        "RUN_LABEL": "ci-skills.spyroot.dev/run-id=unit123",
        "MARKER_PREFIX": PODMAN_EVIDENCE.MARKER_PREFIX,
        "FAKE_JOURNAL": str(call_journal),
        "FAKE_PROBE_FAILURE": failure,
    }
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            'export FAKE_PROBE_PARENT=$$; exec /bin/bash "$1"',
            "probe-test",
            str(script_dir / PODMAN.PROBE_SCRIPT_NAME),
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, read_journal(call_journal)


@pytest.mark.parametrize("failure", ("none", "version", "cleanup", "term"))
def test_checked_probe_script_preserves_failures_and_always_checks_cleanup(
    tmp_path: Path,
    fake_bin: Path,
    call_journal: Path,
    failure: str,
) -> None:
    """Exercise real Bash lifecycle behavior while Podman itself remains offline.

    :param tmp_path: Test-owned script and evidence directories.
    :param fake_bin: Directory containing only deterministic external-command fakes.
    :param call_journal: Journal proving cleanup calls happened after each outcome.
    :param failure: Successful, early-command, cleanup-command, or signal outcome.
    :returns: None; native exit status, stderr, markers, and cleanup are asserted.
    """
    result, calls = _run_probe_script(tmp_path, fake_bin, call_journal, failure)
    argv = [call["argv"] for call in calls]
    assert any("rm" in arguments for arguments in argv)
    assert "ps" in argv[-1]
    assert f"{PODMAN_EVIDENCE.MARKER_PREFIX}|remaining_containers|" in result.stdout
    if failure == "none":
        assert result.returncode == 0, result.stderr
        assert PODMAN_EVIDENCE.parse_probe_markers(result.stdout).verified is True
        assert (tmp_path / "evidence" / "inner.txt").read_text() == "ready"
    elif failure == "version":
        assert result.returncode == 23
        assert "fixture podman version failed" in result.stderr
        assert not any("run" in arguments for arguments in argv)
    elif failure == "cleanup":
        assert result.returncode != 0
        assert "fixture cleanup failed after removal" in result.stderr
    else:
        assert result.returncode == -signal.SIGTERM
        assert not any("run" in arguments for arguments in argv)


@pytest.mark.parametrize(
    "write_evidence", (False, True), ids=("expected-exit", "evidence")
)
def test_checked_inner_script_writes_evidence_or_preserves_requested_exit(
    tmp_path: Path,
    write_evidence: bool,
) -> None:
    """Run the actual inner script with its two declared argument forms.

    :param tmp_path: Directory containing the isolated script and evidence file.
    :param write_evidence: Whether to supply an evidence filename or numeric exit code.
    :returns: None; file content, stdout markers, and native exit are asserted.
    """
    script = tmp_path / PODMAN.INNER_SCRIPT_NAME
    script.write_text(
        PODMAN.probe_scripts()[PODMAN.INNER_SCRIPT_NAME], encoding="utf-8"
    )
    evidence = tmp_path / "inner.txt"
    argument = (
        str(evidence) if write_evidence else str(PODMAN_EVIDENCE.EXPECTED_INNER_EXIT)
    )

    result = subprocess.run(
        ["/bin/sh", str(script), argument],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    if write_evidence:
        assert result.returncode == 0, result.stderr
        assert evidence.read_text() == "ready"
        assert result.stdout.startswith("marker=inner-ok;cgroup=")
        assert ";root=" in result.stdout
    else:
        assert result.returncode == PODMAN_EVIDENCE.EXPECTED_INNER_EXIT
        assert result.stdout == ""
        assert not evidence.exists()
