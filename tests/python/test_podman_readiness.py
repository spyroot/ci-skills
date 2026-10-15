"""Focused offline coverage for the Kubernetes Podman readiness probe.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from argparse import Namespace
from collections.abc import MutableMapping, Sequence
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, Optional

import pytest
from conftest import import_script_module, parse_json_output, run_script

PODMAN = import_script_module("lib.python.core.kubernetes.podman_probe")
PODMAN_EVIDENCE = import_script_module("lib.python.core.kubernetes.podman_evidence")
PODMAN_CALLBACKS = import_script_module("lib.python.core.kubernetes.podman_callbacks")
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
    )


def test_run_scope_derives_all_owned_names_from_one_identifier() -> None:
    """Keep namespace, object names, and labels tied to one run identifier.

    :returns: None; assertions verify deterministic run ownership.
    """
    scope = RUN_SCOPE.RunScope.from_id("unit123")

    assert scope.namespace == "ci-skills-run-unit123"
    assert scope.name("podman-probe") == "ci-skills-run-unit123-podman-probe"
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
        RUN_SCOPE.RunScope.from_id(run_id)


def test_probe_action_requires_digest_pinned_images(tmp_path: Path) -> None:
    """Reject mutable probe and inner image tags before provider access.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; both image inputs are validated independently.
    """
    with pytest.raises(ValueError, match="immutable_image_required"):
        replace(_action(tmp_path), probe_image="registry.example.test/podman:latest")
    with pytest.raises(ValueError, match="immutable_image_required"):
        replace(_action(tmp_path), inner_image="registry.example.test/busybox:latest")


def test_probe_inputs_accept_positive_timeout_and_native_decimal_quantities(
    tmp_path: Path,
) -> None:
    """Accept caller-selected bounds expressed in Kubernetes quantity syntax.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; the action retains valid non-integer quantities and timeout.
    """
    resources = PODMAN.ProbeResources(
        cpu_request="0.5",
        cpu_limit="1500m",
        memory_request="0.5Gi",
        memory_limit="1.5Gi",
        storage_size="2.5Gi",
    )

    action = replace(_action(tmp_path), timeout=1800, resources=resources)

    assert action.timeout == 1800
    assert action.resources == resources


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

    assert READINESS._mode(args) == PODMAN.ProbeMode.LIVE_PLAN
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
    assert plan["side_effects_on_apply"] == [
        "temporary_namespace",
        "temporary_job",
    ]


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
    scope = RUN_SCOPE.RunScope.from_id("unit123")

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
    assert pod_spec["volumes"] == [
        {
            "name": "containers-storage",
            "emptyDir": {"sizeLimit": "2Gi"},
        },
        {"name": "containers-runroot", "emptyDir": {}},
        {"name": "evidence", "emptyDir": {"sizeLimit": "16Mi"}},
    ]
    script = container["command"][-1]
    assert "--storage-driver overlay" in script
    assert "--cgroup-manager cgroupfs" in script
    assert "--memory 64m --cpus 0.25" in script
    assert '[ "$failure_rc" -eq 7 ]' in script


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
        delete_failure: Optional[str] = None,
        namespace_readback_failure: bool = False,
    ):
        """Configure provider observations and optional cleanup failure.

        :param target: Kubernetes-only target exposed to callback configuration.
        :param pod_exit: Probe Pod termination exit code.
        :param log: Exact probe log; omitted means a complete successful log.
        :param scc_rejected: Whether Pod creation is rejected by SCC admission.
        :param delete_failure: Resource kind whose cleanup should fail.
        :param namespace_readback_failure: Whether the post-apply Namespace read fails.
        """
        self.target = target
        self.timeout = 30
        self.pod_exit = pod_exit
        self.log = _complete_log() if log is None else log
        self.scc_rejected = scc_rejected
        self.delete_failure = delete_failure
        self.namespace_readback_failure = namespace_readback_failure
        self.resources: MutableMapping[tuple[str, str, str], JSONObject] = {}
        self.mutations: list[tuple[str, str, str]] = []
        self.deletions: list[tuple[str, str, str]] = []
        self.commands: list[tuple[str, ...]] = []

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

    def command(self, *args: str, openshift: bool = False) -> CommandResult:
        """Return bounded logs for the exact probe Pod.

        :param *args: Native logs command arguments.
        :param openshift: Unused native-client selector.
        :returns: Successful command result carrying the configured log.
        """
        assert openshift is False
        self.commands.append(args)
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
        PODMAN_CALLBACKS.RunPodmanProbe(action),
    ]
    state = PODMAN_CALLBACKS.PodmanProbeState()
    state.kubernetes = service
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
    tmp_path: Path, *, confirmation: Optional[str] = None
) -> PodmanProbeAction:
    """Build an apply action confirmed for the selected fixture worker.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :param confirmation: Explicit digest override; omitted computes the valid digest.
    :returns: Apply-mode probe action.
    """
    planned = _action(tmp_path)
    digest = planned.fingerprint("worker-a") if confirmation is None else confirmation
    return replace(
        planned,
        mode=PODMAN.ProbeMode.APPLY,
        confirm_plan=digest,
    )


def test_confirm_mismatch_blocks_before_any_resource_mutation(tmp_path: Path) -> None:
    """Refuse a stale live-plan digest before namespace or Job creation.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; the mutation journal remains empty.
    """
    service = FakeKubernetes(_target(tmp_path))
    executor = _executor(
        _apply_action(tmp_path, confirmation="stale-plan"),
        service,
    )

    with pytest.raises(ValueError, match="confirm_plan_mismatch_rerun_live_plan"):
        executor.run()

    assert _reported_apply_status(executor.state) == "BLOCKED"
    assert service.mutations == []
    assert service.deletions == []
    assert service.resources == {}


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
    assert [item["kind"] for item in state.cleanup_resources] == [
        "Job",
        "Namespace",
    ]
    assert [kind for kind, _name, _namespace in service.deletions] == [
        "Job",
        "Namespace",
    ]
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


def test_scc_rejection_reports_requirement_without_granting_it(tmp_path: Path) -> None:
    """Return observed SCC evidence, clean resources, and issue no grant command.

    :param tmp_path: Directory used by the Kubernetes-only target.
    :returns: None; provider calls and the blocked record are asserted.
    """
    service = FakeKubernetes(_target(tmp_path), scc_rejected=True)
    executor = _executor(_apply_action(tmp_path), service)

    executor.run()

    assert executor.state.probe_record["verified"] is False
    assert executor.state.probe_record["outcome"] == "scc_required"
    assert executor.state.probe_record["service_account"] == (
        f"system:serviceaccount:{executor.state.run_scope.namespace}:default"
    )
    assert executor.state.probe_record["constraint"] == "privileged"
    assert _reported_apply_status(executor.state) == "BLOCKED"
    assert service.commands == []
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
