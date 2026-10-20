"""Atomic callbacks for planning and running a disposable Podman readiness Job.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Optional

from lib.python.core.abstract.skill_action import ActionT_co
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import (
    DEFAULT_COMMAND_TIMEOUT,
    K8SCallback,
    KubernetesError,
    KubernetesService,
)
from lib.python.core.kubernetes.podman_evidence import (
    PROBE_CONTAINER,
    MarkerEvidence,
    PodTermination,
    parse_probe_markers,
    pod_termination,
    scc_admission_reason,
)
from lib.python.core.kubernetes.podman_probe import (
    DEFAULT_SERVICE_ACCOUNT,
    NESTED_RUNTIME_CLAIM,
    PROBE_POLL_SECONDS,
    PodmanProbeAction,
    ProbeMode,
    probe_configmap,
    probe_job,
    select_ready_worker,
)
from lib.python.core.kubernetes.resources import ResourceSet
from lib.python.core.kubernetes.run_scope import (
    OpenRunScope,
    RunScope,
    RunScopeAction,
    RunScopeState,
)
from lib.python.core.runtime import error_class
from lib.python.sys.status import APPLIED, PASS, VERIFIED


@dataclass
class PodmanProbeState(RunScopeState):
    """Typed shared state for the ordered Podman readiness callbacks.

    Values with ordering dependencies use ``init=False``: a callback cannot read
    them until the preceding callback has produced them. Collections are always
    initialized and never use ``None`` as an empty state.

    .. attribute :: selected_node
        :type: str

        Ready worker selected before plan confirmation.

    .. attribute :: node_evidence
        :type: JSONObject

        Native kernel and operating-system information from the selected Node.

    .. attribute :: plan
        :type: JSONObject

        Canonical nonsecret live plan.

    .. attribute :: plan_digest
        :type: str

        SHA-256 digest required to confirm the live plan.

    .. attribute :: job_uid
        :type: str

        UID read back from the applied probe Job.

    .. attribute :: termination
        :type: PodTermination

        Authoritative termination of the Job-owned Pod.

    .. attribute :: probe_log
        :type: str

        Bounded log read from the exact probe Pod.

    .. attribute :: marker_evidence
        :type: MarkerEvidence

        Parsed nested-runtime evidence rows.

    .. attribute :: probe_record
        :type: JSONObject

        Final probe outcome emitted by the run callback.

    """

    selected_node: str = field(init=False)
    node_evidence: JSONObject = field(init=False)
    plan: JSONObject = field(init=False)
    plan_digest: str = field(init=False)
    job_uid: str = field(init=False)
    termination: PodTermination = field(init=False)
    probe_log: str = field(init=False, repr=False)
    marker_evidence: MarkerEvidence = field(init=False)
    probe_record: JSONObject = field(init=False)


class PodmanK8SCallback(K8SCallback[ActionT_co]):
    """Expose the exact Podman state while retaining shared Kubernetes behavior."""

    @property
    def probe_state(self) -> PodmanProbeState:
        """Read the one state instance bound by the workflow executor.

        :returns: Bound Podman probe state.
        :raises TypeError: If a caller composes this callback with another state type.
        """
        state = self.state
        if not isinstance(state, PodmanProbeState):
            raise TypeError("podman_probe_state_required")
        return state


class SelectReadyWorker(PodmanK8SCallback[PodmanProbeAction]):
    """Select or verify one Ready schedulable worker before plan confirmation."""

    def run(self) -> JSONObject:
        """Read the NodeList once and retain the concrete selected worker.

        :returns: Verified worker-selection evidence.
        :raises KubernetesError: If no eligible worker exists or the list is malformed.
        """  # noqa: DOC502 - Provider reads and worker parsing propagate failures.
        nodes = self.kubernetes.json("get", "nodes", "-o", "json")
        selected = select_ready_worker(nodes, self.action.node)
        node_evidence = selected.evidence()
        self.probe_state.selected_node = selected.name
        self.probe_state.node_evidence = node_evidence
        return {
            "action": VERIFIED,
            "verified": True,
            "selected_node": selected.name,
            "selection": "requested" if self.action.node is not None else "discovered",
            "node": node_evidence,
        }


class ConfirmPodmanProbe(PodmanK8SCallback[PodmanProbeAction]):
    """Bind the live-selected node into the plan before any mutation callback."""

    def run(self) -> JSONObject:
        """Compute and, for apply, confirm the exact live plan digest.

        :returns: Plan and canonical digest for the selected node.
        :raises ValueError: If apply confirmation differs from the live plan.
        """
        state = self.probe_state
        selected = state.selected_node
        state.plan = self.action.plan(selected)
        state.plan_digest = self.action.fingerprint(selected)
        if (
            self.action.mode == ProbeMode.APPLY
            and self.action.confirm_plan != state.plan_digest
        ):
            raise ValueError("confirm_plan_mismatch_rerun_live_plan")
        return {
            "action": VERIFIED,
            "verified": True,
            "plan_digest": state.plan_digest,
            "selected_node": selected,
        }


class OpenProbeNamespace(PodmanK8SCallback[PodmanProbeAction]):
    """Open a generated or selected namespace through the shared run scope."""

    def __init__(self, action: PodmanProbeAction):
        """Retain the action and initialize the deferred shared callback.

        :param action: Confirmed probe inputs.
        """
        super().__init__(action)
        self.scope_callback: Optional[OpenRunScope] = None

    def run(self) -> JSONObject:
        """Create the run identity after confirmation and open its namespace.

        :returns: Shared scope identity, ownership, reuse, and namespace action.
        :raises KubernetesError: If a generated namespace cannot be created and
            re-resolved or a selected namespace is absent.
        :raises TypeError: If the shared state cannot retain run-scope evidence.
        """  # noqa: DOC502 - The shared callback propagates declared failures.
        callback = OpenRunScope(
            RunScopeAction(scope=RunScope.new(namespace=self.action.namespace))
        )
        callback.bind(self.probe_state)
        self.scope_callback = callback
        return callback.run()

    def cleanup(self) -> JSONObject:
        """Delegate generated-namespace cleanup and preserve selected namespaces.

        :returns: Shared removed and retained resource evidence, or an empty mapping.
        :raises KubernetesError: If owned namespace cleanup cannot prove absence.
        :raises TypeError: If the shared state cannot retain run-scope evidence.
        """  # noqa: DOC502,DOC503 - Shared cleanup propagates declared failures.
        if self.scope_callback is None:
            return {}
        return self.scope_callback.cleanup()


class RunPodmanProbe(PodmanK8SCallback[PodmanProbeAction]):
    """Run the one-shot Job and turn Pod termination plus markers into evidence."""

    def __init__(self, action: PodmanProbeAction):
        """Retain the action and initialize empty resource ownership.

        :param action: Confirmed probe inputs.
        """
        super().__init__(action)
        self.resources: Optional[ResourceSet] = None

    def run(self) -> JSONObject:
        """Apply, observe, and verify the exact Job-owned Pod.

        :returns: Runtime readiness, SCC requirement, or bounded failure evidence.
        :raises KubernetesError: If resource ownership or native responses are invalid.
        :raises OSError: If a bounded native command cannot start or read output.
        :raises ValueError: If probe markers are malformed or duplicated.
        """  # noqa: DOC503 - Provider, log, and marker helpers propagate failures.
        state, scope = self.probe_state, self.probe_state.run_scope
        resources = scope.resources(self.kubernetes)
        self.resources = resources
        resources.apply(probe_configmap(scope))
        job_evidence = resources.apply(
            probe_job(self.action, scope, state.selected_node)
        )
        uid = job_evidence.get("uid")
        if not isinstance(uid, str) or not uid:
            raise KubernetesError("probe_job_uid_missing")
        state.job_uid = uid
        observed = self._wait(scope, uid)
        if isinstance(observed, str):
            record: JSONObject = {
                "action": APPLIED,
                "verified": False,
                "outcome": observed,
                "claim": NESTED_RUNTIME_CLAIM,
                "selected_node": state.selected_node,
                "node": state.node_evidence,
                "namespace": scope.namespace,
                "service_account": _service_account(
                    scope.namespace, self.action.service_account
                ),
                "owns_namespace": scope.owns_namespace,
                "job": {"name": scope.name("podman-probe"), "uid": uid},
                "rows": [],
            }
            if observed == "scc_required":
                record["constraint"] = self.action.scc_access().constraint
            state.probe_record = record
            return record
        state.termination = observed
        log = self._logs(scope, observed)
        state.probe_log = log
        markers = parse_probe_markers(log)
        state.marker_evidence = markers
        verified = observed.exit_code == 0 and markers.verified
        record = {
            "action": APPLIED,
            "verified": verified,
            "outcome": "ready" if verified else "probe_failed",
            "claim": NESTED_RUNTIME_CLAIM,
            "nested_runtime_ready": verified,
            "selected_node": state.selected_node,
            "node": state.node_evidence,
            "namespace": scope.namespace,
            "service_account": _service_account(
                scope.namespace, self.action.service_account
            ),
            "owns_namespace": scope.owns_namespace,
            "job": {"name": scope.name("podman-probe"), "uid": uid},
            "pod": {
                "name": observed.pod_name,
                "uid": observed.pod_uid,
                "exit_code": observed.exit_code,
                "image_id": observed.image_id,
            },
            "rows": list(markers.rows),
        }
        state.probe_record = record
        return record

    @property
    def complete(self) -> bool:
        """Stop the workflow when the probe did not prove nested runtime readiness.

        :returns: True only for a verified probe result.
        """
        return self.result.get("verified") is True

    def cleanup(self) -> JSONObject:
        """Delete the Job then its scripts and prove absence before the namespace.

        :returns: Verified cleanup identities, or an empty mapping before creation.
        :raises KubernetesError: If deletion or absence read-back fails.
        """  # noqa: DOC502 - ResourceSet propagates cleanup and read-back failures.
        if self.resources is None:
            return {}
        scope = self.probe_state.run_scope
        created = tuple(reversed(self.resources.created))
        self.resources.cleanup_created()
        evidence: list[JSONObject] = [
            {
                "kind": kind,
                "name": name,
                "namespace": scope.namespace,
                "status": PASS,
                "absent": True,
            }
            for kind, name in created
        ]
        self.probe_state.cleanup_resources.extend(evidence)
        return {"resources": evidence}

    def _wait(self, scope: RunScope, job_uid: str) -> PodTermination | str:
        """Wait for termination or a directly observed SCC admission rejection.

        :param scope: Generated namespace and Job name.
        :param job_uid: UID independently read after apply.
        :returns: Authoritative Pod termination or stable blocked outcome.
        :raises KubernetesError: If provider response shapes are malformed.
        """
        deadline = time.monotonic() + self.action.timeout
        name = scope.name("podman-probe")
        service = self.kubernetes
        original_timeout = service.timeout
        job_failed = False
        try:
            while True:
                if not _bind_remaining_timeout(service, deadline):
                    return (
                        "job_failed_without_termination"
                        if job_failed
                        else "probe_timeout"
                    )
                pods = service.json(
                    "-n",
                    scope.namespace,
                    "get",
                    "pods",
                    "-l",
                    f"job-name={name}",
                    "-o",
                    "json",
                )
                termination = pod_termination(pods, job_uid)
                if termination is not None:
                    return termination
                if not _bind_remaining_timeout(service, deadline):
                    return (
                        "job_failed_without_termination"
                        if job_failed
                        else "probe_timeout"
                    )
                events = service.json(
                    "-n", scope.namespace, "get", "events", "-o", "json"
                )
                classified = scc_admission_reason(events, job_uid)
                if classified is not None:
                    return classified
                if not _bind_remaining_timeout(service, deadline):
                    return (
                        "job_failed_without_termination"
                        if job_failed
                        else "probe_timeout"
                    )
                if job_failed:
                    return "job_failed_without_termination"
                job = service.read("Job", name, scope.namespace)
                if job is None:
                    raise KubernetesError("probe_job_missing")
                job_failed = _job_failed(job)
                if job_failed:
                    # One final Pod/Event read can expose termination or denial
                    # that arrived with the terminal Job condition.
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return (
                        "job_failed_without_termination"
                        if job_failed
                        else "probe_timeout"
                    )
                time.sleep(min(PROBE_POLL_SECONDS, remaining))
        finally:
            service.timeout = original_timeout

    def _logs(self, scope: RunScope, termination: PodTermination) -> str:
        """Read bounded logs from the exact terminated probe container.

        :param scope: Generated namespace containing the Pod.
        :param termination: Exact Pod identity already verified against Job ownership.
        :returns: Probe log text containing marker lines.
        :raises KubernetesError: If native log retrieval fails.
        """
        result = self.kubernetes.command(
            "-n",
            scope.namespace,
            "logs",
            f"pod/{termination.pod_name}",
            "-c",
            PROBE_CONTAINER,
        )
        if result.returncode:
            raise KubernetesError(error_class(result))
        return result.stdout


def _service_account(namespace: str, name: str = DEFAULT_SERVICE_ACCOUNT) -> str:
    """Name the selected service account in Kubernetes authorization form.

    :param namespace: Generated or caller-selected run namespace.
    :param name: ServiceAccount name fixed by the execution contract.
    :returns: Exact service-account subject evaluated by OpenShift admission.
    """
    return f"system:serviceaccount:{namespace}:{name}"


def _job_failed(document: JSONObject) -> bool:
    """Read whether a Job has a true Failed condition.

    :param document: Exact Job read-back.
    :returns: Whether Kubernetes marked the Job failed.
    """
    status = document.get("status")
    conditions = status.get("conditions", []) if isinstance(status, Mapping) else []
    return isinstance(conditions, list) and any(
        isinstance(condition, Mapping)
        and condition.get("type") == "Failed"
        and condition.get("status") == "True"
        for condition in conditions
    )


def _bind_remaining_timeout(service: KubernetesService, deadline: float) -> bool:
    """Bound the next native read to the remaining terminal-wait interval.

    :param service: Shared verified Kubernetes service used by the callback.
    :param deadline: Monotonic terminal-wait deadline.
    :returns: Whether time remains for another provider read.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    service.timeout = min(DEFAULT_COMMAND_TIMEOUT, max(1, math.ceil(remaining)))
    return True
