"""Collect Cilium resource and per-agent health observations.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from lib.python.access.kubernetes import kubectl_argv, kubernetes_env
from lib.python.core.cli_api import response_decoder
from lib.python.core.collection import _search
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.kubernetes.cilium import (
    AGENT_DAEMONSET,
    HEALTH_COMMAND,
    agent_selector,
    health_detail,
    health_findings,
    is_ready,
    matches_selector,
    valid_health,
)
from lib.python.core.kubernetes.collect import _batch, _meta
from lib.python.core.kubernetes.kubernetes import KubernetesService
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.report import report
from lib.python.core.runtime import error_class, run_command
from lib.python.core.target import Target, kubernetes_label
from lib.python.sys.status import PASS, UNKNOWN


class CiliumInventory:
    """Select Cilium resources and collect bounded health evidence per agent."""

    def __init__(
        self,
        target: Target,
        args: Namespace,
        data: Mapping[str, Sequence[JSONObject]],
        errors: MutableSequence[Mapping[str, str]],
        *,
        state: DeliveryState | None = None,
    ):
        """Keep one resource snapshot and the caller's report filters.

        :param target: Bound Kubernetes authority used for health commands.
        :param args: Namespace selection, node restriction, and report text filter.
        :param data: Validated resource lists from the shared concurrent reader.
        :param errors: Collection failures extended with selection and health errors.
        :param state: Shared declaration and previously observed Kubernetes access.
        """
        self.target = target
        self.state = state
        self.args = args
        self.errors = errors
        self.data: MutableMapping[str, Sequence[JSONObject]] = {}
        for source, records in data.items():
            try:
                for record in records:
                    resource = KubernetesObject(record)
                    if source != "ciliumnodes":
                        resource.metadata.required_text("namespace")
                self.data[source] = records
            except TypeError:
                self.data[source] = []
                errors.append({"source": source, "reason": "invalid_item_response"})
        self.daemonsets = [
            item
            for item in self.data.get("daemonsets", [])
            if _meta(item).get("name") == AGENT_DAEMONSET
        ]
        self.namespace = self.select_namespace()

    def select_namespace(self) -> Optional[str]:
        """Resolve the caller's namespace or the unique observed Cilium namespace.

        :returns: Selected namespace, or None with a recorded selection error when
            automatic discovery cannot identify exactly one namespace.
        """
        if self.args.namespace != "auto":
            return self.args.namespace
        namespaces = sorted(
            {
                name
                for item in self.daemonsets
                if isinstance(name := _meta(item).get("namespace"), str) and name
            }
        )
        if len(namespaces) == 1:
            return namespaces[0]
        self.errors.append(
            {"source": "daemonsets", "reason": "cilium_namespace_not_unique"}
        )
        return None

    def agent_pods(self) -> Sequence[JSONObject]:
        """Select agents using the observed DaemonSet selector and node filter.

        :returns: Matching Pod objects in API order. Missing DaemonSet or selector
            evidence adds a classified error to this inventory.
        """
        selected = [
            item
            for item in self.daemonsets
            if _meta(item).get("namespace") == self.namespace
        ]
        if len(selected) != 1:
            self.errors.append(
                {"source": "daemonsets", "reason": "cilium_daemonset_missing"}
            )
        selector = (
            agent_selector(self.daemonsets, self.namespace)
            if self.namespace is not None
            else None
        ) or {}
        if selected and not selector:
            self.errors.append(
                {"source": "daemonsets", "reason": "cilium_selector_missing"}
            )
        pods: MutableSequence[JSONObject] = []
        for item in self.data.get("pods", []):
            pod = KubernetesObject(item)
            try:
                if pod.metadata.text("namespace") != self.namespace:
                    continue
                labels = pod.metadata.object("labels").document
                node = pod.object("spec").text("nodeName")
                pod.object("status").text("phase")
                pod.metadata.text("uid")
                if matches_selector(labels, selector) and (
                    not self.args.node or node == self.args.node
                ):
                    pods.append(item)
            except TypeError:
                self.errors.append(
                    {"source": "pods", "reason": "invalid_item_response"}
                )
        return pods

    def read_health(self, pod: JSONObject) -> JSONObject:
        """Read and classify one selected agent's health without changing the Pod.

        :param pod: Validated agent Pod from this inventory's namespace selection.
        :returns: Agent identity, readiness, native exit status, and validated health
            details; unready agents or failed reads report UNKNOWN with a reason.
        :raises TypeError: If called directly with malformed Pod fields.
        :raises TargetError: If bound kubeconfig sources changed or are unreadable.
        :raises OSError: If the native process cannot be started.
        """  # noqa: DOC502 - Field readers and native health queries can fail.
        KubernetesService(self.target, state=self.state).require_access()
        resource = KubernetesObject(pod)
        ready = is_ready(pod)
        row: MutableMapping[str, JSONValue] = {
            "namespace": self.namespace,
            "name": resource.name,
            "pod_uid": resource.metadata.text("uid"),
            "node": resource.object("spec").text("nodeName"),
            "phase": resource.object("status").text("phase"),
            "ready": ready,
            "command": list(HEALTH_COMMAND),
            "status": UNKNOWN,
            "exit_status": None,
            "health": None,
        }
        if not ready:
            return row
        result = run_command(
            kubectl_argv(
                self.target,
                "-n",
                resource.namespace,
                "exec",
                resource.name,
                "--",
                *HEALTH_COMMAND,
            ),
            timeout=30,
            env=kubernetes_env(self.target),
        )
        row["exit_status"] = result.returncode
        if result.returncode:
            row["reason"] = error_class(result)
            return row
        try:
            decoded = response_decoder().decode(result.stdout)
        except (ValueError, RecursionError):
            row["reason"] = "invalid_health_json"
            return row
        if not valid_health(decoded):
            row["reason"] = "invalid_health_response"
            return row
        row.update(
            health=decoded,
            health_detail=health_detail(decoded),
            findings=health_findings(decoded),
            status=PASS,
        )
        return row

    def rows(self) -> Sequence[JSONObject]:
        """Read matching agents concurrently and retain health errors before filtering.

        :returns: Sorted agent rows, or one UNKNOWN row when no agent matches.
        :raises TargetError: If bound kubeconfig sources changed or are unreadable.
        :raises OSError: If a native health process cannot be started.
        """  # noqa: DOC502 - Field readers and native health queries can fail.
        pods = self.agent_pods()
        if not pods:
            self.errors.append(
                {"source": "pods", "reason": "cilium_agent_pods_missing"}
            )
            return [
                {
                    "namespace": self.namespace,
                    "name": "cilium-agent",
                    "node": self.args.node,
                    "pod_uid": None,
                    "status": UNKNOWN,
                    "reason": "no_matching_agent_pods",
                    "command": list(HEALTH_COMMAND),
                    "exit_status": None,
                }
            ]
        rows: MutableSequence[JSONObject] = []
        with ThreadPoolExecutor(max_workers=min(8, len(pods))) as pool:
            for row in pool.map(self.read_health, pods):
                if row["status"] == UNKNOWN:
                    self.errors.append(
                        {
                            "source": str(row["name"]),
                            "reason": str(row.get("reason") or "agent_not_ready"),
                        }
                    )
                elif row["findings"]:
                    self.errors.append(
                        {"source": str(row["name"]), "reason": "component_unhealthy"}
                    )
                if _search(row, self.args.search):
                    rows.append(row)
        return sorted(
            rows, key=lambda row: (str(row["node"] or ""), str(row["name"] or ""))
        )

    def report(self) -> MutableMapping[str, JSONValue]:
        """Add related Cilium resource observations to the shared report envelope.

        :returns: Cilium agent rows, component findings, related inventories, and
            all resource-selection or health-read errors collected for this target.
        :raises TargetError: If bound kubeconfig sources changed or are unreadable.
        :raises OSError: If a native health process cannot be started.
        """  # noqa: DOC502 - Field readers and native health queries can fail.
        result = report(
            "cilium_status",
            kubernetes_label(self.target),
            {
                "namespace": self.namespace,
                "node": self.args.node,
                "search": self.args.search,
            },
            self.rows(),
            self.errors,
        )
        result["daemonsets"] = [
            {
                "namespace": item.metadata.text("namespace"),
                "name": item.name,
                **item.replica_counts("DaemonSet"),
            }
            for document in self.daemonsets
            if (item := KubernetesObject(document)).metadata.text("namespace")
            == self.namespace
        ]
        result["operators"] = [
            {
                "namespace": item.metadata.text("namespace"),
                "name": item.name,
                "ready": item.object("status").document.get("readyReplicas", 0),
            }
            for document in self.data.get("operators", [])
            if (item := KubernetesObject(document)).metadata.text("namespace")
            == self.namespace
            and "cilium" in item.name
        ]
        result["ciliumnodes"] = [
            {"name": _meta(item).get("name"), "status": item.get("status", {})}
            for item in self.data.get("ciliumnodes", [])
        ]
        return result


def collect_cilium(
    target: Target, args: Namespace, *, state: DeliveryState | None = None
) -> JSONObject:
    """Read Cilium agent, operator, and CiliumNode status on one target.

    :param target: Bound Kubernetes target for resource lists and agent health.
    :param args: Namespace selector, optional node, and text-search filter.
    :param state: Shared declaration and previously observed Kubernetes access.
    :returns: Cilium report with agent records, component findings, related
        inventories, and errors when selection or health reads fail.
    :raises TargetError: If bound kubeconfig sources changed or are unreadable.
    :raises ValueError: If the target has no Kubernetes authority.
    :raises OSError: If a native resource or health process cannot be started.
    """  # noqa: DOC502 - Target/query helpers and worker reads propagate failures.
    KubernetesService(target, state=state).require_access()
    data, errors = _batch(
        target,
        {
            "daemonsets": ("daemonsets", True),
            "pods": ("pods", True),
            "operators": ("deployments", True),
            "ciliumnodes": ("ciliumnodes.cilium.io", False),
        },
    )
    return CiliumInventory(target, args, data, errors, state=state).report()
