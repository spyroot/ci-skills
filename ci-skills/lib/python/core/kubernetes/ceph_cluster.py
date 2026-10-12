"""Read a selected Rook Ceph cluster and correlate OSD/monitor Pod state.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import re
from argparse import Namespace
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

from lib.python.access.kubernetes import kubernetes_env, oc_argv
from lib.python.core.cli_api import response_decoder
from lib.python.core.json_types import JSONObject, JSONValue, json_object, json_objects
from lib.python.core.kubernetes.kubernetes import KubernetesService
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.report import report
from lib.python.core.runtime import CommandResult, error_class, run_command_tail
from lib.python.core.target import Target, kubernetes_label
from lib.python.sys.status import PARTIAL, PASS

MAX_OUTPUT_BYTES = 1_048_576
MAX_OUTPUT_LINES = 20_000
POD_SELECTOR = "app in (rook-ceph-osd,rook-ceph-mon)"
POD_ROLES = {"rook-ceph-osd", "rook-ceph-mon"}
MAX_CRUSH_DEPTH = 128
KUBERNETES_NAME = re.compile(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?\Z")


def _oc_prefix(target: Target, namespace: str) -> MutableSequence[str]:
    """Select OpenShift client arguments for one Ceph namespace.

    :param target: Bound target providing context and credential sources.
    :param namespace: Namespace passed to every Ceph and Pod query.
    :returns: ``oc`` argument prefix with target context and namespace.
    :raises TargetError: The bound kubeconfig changed before command creation.
    """  # noqa: DOC502 - oc_argv verifies the selected bound kubeconfig.
    return oc_argv(target, "-n", namespace)


def _json_response(result: CommandResult, source: str) -> JSONValue:
    """Parse a bounded successful provider response as JSON.

    :param result: Native command status and captured output.
    :param source: Query name included in failure diagnostics.
    :returns: Decoded JSON value.
    :raises ValueError: The command failed, output approached the byte limit, or
        JSON is malformed, nonfinite, or exceeds decoder nesting limits.
    """
    if result.returncode:
        raise ValueError(f"{source}:{error_class(result)}")
    if len(result.stdout.encode("utf-8")) >= MAX_OUTPUT_BYTES - 4:
        raise ValueError(f"{source}:output_limit")
    try:
        return response_decoder().decode(result.stdout)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"{source}:invalid_json") from exc


def _condition_filter(value: Optional[str]) -> Optional[tuple[str, str]]:
    """Parse an optional Pod condition filter.

    :param value: ``TYPE=True|False|Unknown`` expression, or ``None``.
    :returns: Condition type and exact state, or ``None`` when absent.
    :raises ValueError: The expression has no type or unsupported state.
    """
    if value is None:
        return None
    kind, separator, state = value.partition("=")
    if not separator or not kind or state not in {"True", "False", "Unknown"}:
        raise ValueError("condition must be TYPE=True|False|Unknown")
    return kind, state


def _pods(
    payload: JSONValue,
    namespace: str,
    node: Optional[str],
    ready: str,
    condition: Optional[tuple[str, str]],
) -> tuple[int, Sequence[JSONObject]]:
    """Validate and filter Ceph OSD and monitor Pods.

    :param payload: Decoded Kubernetes Pod List response.
    :param namespace: Default namespace when an item's metadata omits it.
    :param node: Optional exact node filter.
    :param ready: ``all``, ``true``, or ``false`` readiness selector.
    :param condition: Optional condition type and exact status filter.
    :returns: Total list size and selected Pod records with role, node, phase,
        readiness, and condition states.
    :raises TypeError: The response or a selected Pod field is malformed, unnamed,
        or has a role outside the expected Ceph selector.
    """
    items = payload.get("items") if isinstance(payload, Mapping) else None
    if not isinstance(items, list):
        raise TypeError("pods:invalid_list_response")
    selected: MutableSequence[JSONObject] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise TypeError("pods:invalid_item")
        metadata = item.get("metadata")
        spec = item.get("spec")
        status = item.get("status")
        if (
            not isinstance(metadata, Mapping)
            or not isinstance(spec, Mapping)
            or not isinstance(status, Mapping)
        ):
            raise TypeError("pods:invalid_item")
        labels = metadata.get("labels")
        conditions = status.get("conditions", [])
        if not isinstance(labels, Mapping) or not isinstance(conditions, list):
            raise TypeError("pods:invalid_item")
        name = metadata.get("name")
        if not isinstance(name, str) or not name:
            raise TypeError("pods:missing_name")
        role = labels.get("app")
        if not isinstance(role, str) or role not in POD_ROLES:
            raise TypeError("pods:selector_mismatch")
        states: MutableMapping[str, JSONValue] = {}
        for entry in conditions:
            if not isinstance(entry, Mapping) or not isinstance(entry.get("type"), str):
                raise TypeError("pods:invalid_condition")
            state = entry.get("status")
            if not isinstance(state, str) or state not in {"True", "False", "Unknown"}:
                raise TypeError("pods:invalid_condition")
            states[entry["type"]] = state
        is_ready = states.get("Ready") == "True"
        pod_node = spec.get("nodeName")
        if node and pod_node != node:
            continue
        if ready != "all" and is_ready != (ready == "true"):
            continue
        if condition and states.get(condition[0]) != condition[1]:
            continue
        selected.append(
            {
                "namespace": metadata.get("namespace", namespace),
                "name": name,
                "role": role,
                "osd_id": labels.get("ceph-osd-id"),
                "node": pod_node,
                "phase": status.get("phase"),
                "ready": is_ready,
                "conditions": states,
            }
        )
    return len(items), selected


def _inactive_pgs(payload: JSONValue) -> Sequence[JSONObject]:
    """Extract inactive placement group IDs and states from Ceph JSON.

    :param payload: Placement group list or object containing one of Ceph's known
        list fields.
    :returns: Records with ``pgid`` and optional ``state``.
    :raises TypeError: The list or an item lacks the expected shape or PG ID.
    """
    if isinstance(payload, Mapping):
        payload = payload.get(
            "stuck_pg_stats", payload.get("pg_stats", payload.get("pgs"))
        )
    if not isinstance(payload, list):
        raise TypeError("inactive_pgs:invalid_response")
    result = []
    for item in payload:
        if not isinstance(item, Mapping) or not isinstance(item.get("pgid"), str):
            raise TypeError("inactive_pgs:invalid_item")
        result.append({"pgid": item["pgid"], "state": item.get("state")})
    return result


def _health(payload: JSONValue) -> tuple[str, Sequence[JSONObject]]:
    """Preserve Ceph health status and named check details.

    :param payload: Decoded Ceph health object from status output.
    :returns: Health status and sorted check records containing code, severity,
        summary, and detail messages.
    :raises TypeError: Status, checks, or a check's diagnostic fields are malformed.
    """
    if (
        not isinstance(payload, Mapping)
        or not isinstance(payload.get("status"), str)
        or payload.get("status")
        not in {
            "HEALTH_OK",
            "HEALTH_WARN",
            "HEALTH_ERR",
        }
    ):
        raise TypeError("status:invalid_health")
    checks = payload.get("checks", {})
    if not isinstance(checks, Mapping):
        raise TypeError("status:invalid_health_checks")
    findings: MutableSequence[JSONObject] = []
    for code, check in sorted(checks.items()):
        if not isinstance(code, str) or not code or not isinstance(check, Mapping):
            raise TypeError("status:invalid_health_check")
        summary = check.get("summary")
        message = summary.get("message") if isinstance(summary, Mapping) else summary
        if not isinstance(message, str) or not message:
            raise TypeError("status:invalid_health_summary")
        severity = check.get("severity")
        if severity is not None and not isinstance(severity, str):
            raise TypeError("status:invalid_health_severity")
        detail = check.get("detail", [])
        if not isinstance(detail, list) or any(
            not isinstance(item, Mapping) or not isinstance(item.get("message"), str)
            for item in detail
        ):
            raise TypeError("status:invalid_health_detail")
        findings.append(
            {
                "code": code,
                "severity": severity,
                "summary": message,
                "detail": [item["message"] for item in detail],
            }
        )
    status = payload["status"]
    if not isinstance(status, str):
        raise TypeError("status:invalid_health")
    return status, findings


@dataclass(frozen=True)
class CephOsdNode:
    """One validated CRUSH hierarchy node before child expansion.

    :param identifier: Unique integer OSD or CRUSH bucket ID.
    :param name: Nonempty provider node name.
    :param kind: Provider hierarchy type such as root, host, or osd.
    :param children: Child IDs in provider order.
    :param status: Optional native OSD state; buckets may omit it.

    .. attribute :: identifier
        :type: int

        Unique integer OSD or CRUSH bucket ID.

    .. attribute :: name
        :type: str

        Nonempty provider node name.

    .. attribute :: kind
        :type: str

        Provider hierarchy type such as root, host, or osd.

    .. attribute :: children
        :type: Sequence[int]

        Child IDs in provider order.

    .. attribute :: status
        :type: Optional[str]

        Optional native OSD state; buckets may omit it.
    """

    identifier: int
    name: str
    kind: str
    children: Sequence[int]
    status: Optional[str] = None

    @classmethod
    def parse(cls, value: JSONValue) -> CephOsdNode:
        """Validate one Ceph OSD tree entry without coercing its identity.

        :param value: One element of the ceph osd tree nodes array.
        :returns: Typed node with integer child references.
        :raises TypeError: If identity, name, type, children, or status is malformed.
        """
        if not isinstance(value, Mapping):
            raise TypeError("osd_tree:invalid_node")
        identifier, name, kind = value.get("id"), value.get("name"), value.get("type")
        if (
            type(identifier) is not int
            or not isinstance(name, str)
            or not name
            or not isinstance(kind, str)
            or not kind
        ):
            raise TypeError("osd_tree:invalid_node")
        raw_children = value.get("children", [])
        if not isinstance(raw_children, list):
            raise TypeError("osd_tree:invalid_children")
        children: MutableSequence[int] = []
        for child in raw_children:
            if type(child) is not int:
                raise TypeError("osd_tree:invalid_children")
            children.append(child)
        status = value.get("status")
        if status is not None and (not isinstance(status, str) or not status):
            raise TypeError("osd_tree:invalid_status")
        return cls(identifier, name, kind, children, status)


class CephOsdTree:
    """Validate child ownership and expand a CRUSH forest without recursion."""

    def __init__(self, payload: JSONValue):
        """Index one native OSD hierarchy and validate every child reference.

        :param payload: Decoded ceph osd tree object containing its nodes array.
        :raises TypeError: If nodes are malformed or duplicated, or a child is
            missing or owned by more than one parent.
        """
        nodes = payload.get("nodes") if isinstance(payload, Mapping) else None
        if not isinstance(nodes, list):
            raise TypeError("osd_tree:invalid_response")
        self.nodes: MutableMapping[int, CephOsdNode] = {}
        self.parents: set[int] = set()
        for value in nodes:
            node = CephOsdNode.parse(value)
            if node.identifier in self.nodes:
                raise TypeError("osd_tree:invalid_node")
            self.nodes[node.identifier] = node
        for node in self.nodes.values():
            for child in node.children:
                if child not in self.nodes or child in self.parents:
                    raise TypeError("osd_tree:invalid_child_reference")
                self.parents.add(child)

    def observations(self) -> tuple[Sequence[JSONObject], Sequence[JSONObject]]:
        """Produce nested CRUSH roots and flat OSD observations.

        :returns: Provider-ordered root subtrees and OSD ID/name/status records.
        :raises TypeError: If a cycle leaves nodes unreachable or hierarchy depth
            exceeds the safe bound for serializing nested report evidence.
        """
        roots: MutableSequence[JSONObject] = []
        pending: MutableSequence[tuple[int, MutableSequence[JSONObject], int]] = [
            (identifier, roots, 0)
            for identifier in reversed(tuple(self.nodes))
            if identifier not in self.parents
        ]
        visited: set[int] = set()
        while pending:
            identifier, destination, depth = pending.pop()
            if depth > MAX_CRUSH_DEPTH:
                raise TypeError("osd_tree:depth_limit")
            if identifier in visited:
                raise TypeError("osd_tree:cycle")
            visited.add(identifier)
            node = self.nodes[identifier]
            children: MutableSequence[JSONObject] = []
            document: MutableMapping[str, JSONValue] = {
                "id": identifier,
                "name": node.name,
                "type": node.kind,
            }
            if node.status is not None:
                document["status"] = node.status
            document["children"] = children
            destination.append(document)
            pending.extend(
                (child, children, depth + 1) for child in reversed(node.children)
            )
        if len(visited) != len(self.nodes):
            raise TypeError("osd_tree:cycle")
        return roots, [
            {"id": node.identifier, "name": node.name, "status": node.status}
            for node in self.nodes.values()
            if node.kind == "osd"
        ]


def _osd_tree(payload: JSONValue) -> tuple[Sequence[JSONObject], Sequence[JSONObject]]:
    """Validate and expand native Ceph OSD hierarchy links.

    :param payload: Decoded ceph osd tree response containing node records.
    :returns: Root subtrees and a flat OSD list with ID, name, and status.
    :raises TypeError: If nodes or child links are malformed, duplicated, missing,
        multiply parented, cyclic, or exceed the report nesting bound.
    """  # noqa: DOC502 - CephOsdTree validates nodes, links, and ownership.
    return CephOsdTree(payload).observations()


def discover_ceph_operator(target: Target, namespace: str) -> str:
    """Discover the unique Rook operator Deployment in the selected namespace.

    :param target: Bound Kubernetes authority for the read-only Deployment query.
    :param namespace: Caller-selected Ceph namespace used to scope discovery.
    :returns: Exact live Deployment name bearing Rook's operator app label.
    :raises ValueError: If discovery yields zero/multiple Deployments or a name,
        namespace, or label does not match the requested selection.
    :raises TypeError: If the native List envelope or resource fields are malformed.
    :raises KubernetesError: If the bounded Deployment query fails or returns invalid JSON.
    :raises TargetError: If the target lacks Kubernetes authority or bound sources change.
    :raises OSError: If the native discovery process cannot be started.
    """  # noqa: DOC503 - Discovery, field readers, and native I/O can fail.
    payload = KubernetesService(target).json(
        "-n",
        namespace,
        "get",
        "deployments",
        "-l",
        "app=rook-ceph-operator",
        "-o",
        "json",
    )
    envelope = json_object(payload)
    if "items" not in envelope:
        raise TypeError("ceph_operator:invalid_list_response")
    items = json_objects(envelope["items"])
    if len(items) != 1:
        raise ValueError("ceph_operator:not_unique")
    deployment = KubernetesObject(items[0])
    name = deployment.name
    if (
        deployment.namespace != namespace
        or deployment.metadata.object("labels").text("app") != "rook-ceph-operator"
        or not KUBERNETES_NAME.fullmatch(name)
        or len(name) > 63
    ):
        raise ValueError("ceph_operator:selection_mismatch")
    return name


class CephClusterRead:
    """Collect independent Ceph queries and combine their validated observations."""

    def __init__(self, target: Target, args: Namespace):
        """Validate caller selection and construct the known read-only queries.

        :param target: Bound Kubernetes authority used by every oc command.
        :param args: Namespace, optional operator, config path, node and Pod filters.
            An omitted operator is discovered by its Rook app label; an omitted
            config path follows /var/lib/rook/<namespace>/<namespace>.config.
        :raises ValueError: If namespace, operator, config path, or condition is invalid.
        :raises TypeError: If operator discovery returns malformed resource fields.
        :raises KubernetesError: If operator discovery fails or returns invalid JSON.
        :raises OSError: If the native discovery process cannot be started.
        :raises TargetError: If bound kubeconfig sources change while creating queries.
        """  # noqa: DOC503 - Discovery, field readers, and native I/O can fail.
        self.target = target
        self.args = args
        namespace = self.args.namespace
        operator = self.args.operator
        config = self.args.conf or f"/var/lib/rook/{namespace}/{namespace}.config"
        if (
            not isinstance(namespace, str)
            or not KUBERNETES_NAME.fullmatch(namespace)
            or len(namespace) > 63
        ):
            raise ValueError("invalid_ceph_namespace")
        if not isinstance(config, str) or not config.startswith("/"):
            raise ValueError("invalid_ceph_config_path")
        self.condition = _condition_filter(self.args.condition)
        if operator is None:
            operator = discover_ceph_operator(target, namespace)
        if (
            not isinstance(operator, str)
            or not KUBERNETES_NAME.fullmatch(operator)
            or len(operator) > 63
        ):
            raise ValueError("invalid_ceph_operator")
        prefix = _oc_prefix(self.target, namespace)
        ceph = [*prefix, "exec", f"deploy/{operator}", "--", "ceph", f"--conf={config}"]
        self.commands = {
            "status": [*ceph, "-s", "--format=json"],
            "osd_tree": [*ceph, "osd", "tree", "--format=json"],
            "inactive_pgs": [*ceph, "pg", "dump_stuck", "inactive", "--format=json"],
            "pods": [*prefix, "get", "pods", "-l", POD_SELECTOR, "-o", "json"],
        }

    def read(self) -> MutableMapping[str, JSONValue]:
        """Execute the four independent reads and report all recoverable failures.

        :returns: Ceph health, CRUSH hierarchy, inactive PGs, filtered Pods, and errors.
        :raises TargetError: If bound credential sources change before execution.
        :raises OSError: If a native query process cannot be started.
        """  # noqa: DOC502 - Native reads and selection helpers propagate failures.
        environment = kubernetes_env(self.target)
        with ThreadPoolExecutor(max_workers=len(self.commands)) as pool:
            futures = {
                key: pool.submit(
                    run_command_tail,
                    command,
                    timeout=40,
                    max_bytes=MAX_OUTPUT_BYTES,
                    max_lines=MAX_OUTPUT_LINES,
                    env=environment,
                )
                for key, command in self.commands.items()
            }
            outputs = {key: future.result() for key, future in futures.items()}

        records: MutableSequence[JSONObject] = []
        errors: MutableSequence[Mapping[str, str]] = []
        result = report(
            "ceph_cluster",
            kubernetes_label(self.target),
            {
                "namespace": self.args.namespace,
                "node": self.args.node,
                "ready": self.args.ready,
                "condition": self.args.condition,
            },
            records,
            errors,
        )
        result["queries"] = {key: command for key, command in self.commands.items()}
        self.integrate(outputs, result, records, errors)
        return self.summarize(result, records, errors)

    def integrate(
        self,
        outputs: Mapping[str, CommandResult],
        result: MutableMapping[str, JSONValue],
        records: MutableSequence[JSONObject],
        errors: MutableSequence[Mapping[str, str]],
    ) -> None:
        """Validate each native response and retain independent query errors.

        :param outputs: Completed native results keyed by the declared query name.
        :param result: Mutable report receiving validated domain observations.
        :param records: Pod records extended after condition and node filtering.
        :param errors: Command, JSON, and domain validation failures to extend.
        """
        decoded: MutableMapping[str, JSONValue] = {}
        for key, response in outputs.items():
            try:
                decoded[key] = _json_response(response, key)
            except ValueError as exc:
                errors.append({"source": key, "reason": str(exc)})
        if "status" in decoded:
            health = (
                decoded["status"].get("health")
                if isinstance(decoded["status"], Mapping)
                else None
            )
            try:
                result["health"], result["health_checks"] = _health(health)
            except TypeError as exc:
                errors.append({"source": "status", "reason": str(exc)})
        if "osd_tree" in decoded:
            try:
                result["osd_tree"], result["osds"] = _osd_tree(decoded["osd_tree"])
            except TypeError as exc:
                errors.append({"source": "osd_tree", "reason": str(exc)})
        if "inactive_pgs" in decoded:
            try:
                result["inactive_pgs"] = _inactive_pgs(decoded["inactive_pgs"])
            except TypeError as exc:
                errors.append({"source": "inactive_pgs", "reason": str(exc)})
        if "pods" in decoded:
            try:
                total, selected = _pods(
                    decoded["pods"],
                    namespace=self.args.namespace,
                    node=self.args.node,
                    ready=self.args.ready,
                    condition=self.condition,
                )
                result["pod_count"] = total
                records.extend(selected)
            except TypeError as exc:
                errors.append({"source": "pods", "reason": str(exc)})

    @staticmethod
    def summarize(
        result: MutableMapping[str, JSONValue],
        records: Sequence[JSONObject],
        errors: Sequence[Mapping[str, str]],
    ) -> MutableMapping[str, JSONValue]:
        """Derive read suggestions and collection status from observed evidence.

        :param result: Mutable report with validated Ceph observations.
        :param records: Filtered Pod observations used for readiness findings.
        :param errors: Query failures determining PARTIAL versus PASS collection.
        :returns: The same report with actions, condition, and final counts.
        """
        actions: set[str] = set()
        health = result.get("health")
        if isinstance(health, str) and health in {"HEALTH_WARN", "HEALTH_ERR"}:
            actions.add("inspect_ceph_health_detail")
        osds = result.get("osds", [])
        if isinstance(osds, list) and any(
            isinstance(item, Mapping) and item.get("status") == "down" for item in osds
        ):
            actions.add("inspect_down_osd_pods_and_nodes")
        if result.get("inactive_pgs"):
            actions.add("inspect_inactive_pg_peering")
        if any(not item["ready"] for item in records):
            actions.add("inspect_unready_ceph_pods")
        result["actions"] = sorted(actions)
        result["condition"] = (
            "UNKNOWN" if errors else "ATTENTION" if actions else "CLEAR"
        )
        result["summary"] = {"record_count": len(records), "error_count": len(errors)}
        result["status"] = PARTIAL if errors else PASS
        if errors:
            result["safe_next_step"] = (
                "Review failed Ceph queries on the selected target."
            )
        return result


def collect_ceph_cluster(
    target: Target, args: Namespace
) -> MutableMapping[str, JSONValue]:
    """Read selected Ceph health, OSD, placement-group, and Pod evidence.

    :param target: Bound Kubernetes target used by all read-only oc queries.
    :param args: Namespace, operator, config path, node, readiness and condition filters.
    :returns: Ceph report with independent query errors and PASS or PARTIAL status.
    :raises ValueError: If caller selection is invalid before query dispatch.
    :raises TypeError: If optional operator discovery returns malformed resource fields.
    :raises KubernetesError: If optional operator discovery fails or returns invalid JSON.
    :raises TargetError: If bound Kubernetes credentials change before queries.
    :raises OSError: If a native query process cannot be started.
    """  # noqa: DOC502 - Native reads and selection helpers propagate failures.
    return CephClusterRead(target, args).read()
