"""Concurrent read-only collectors with typed records and in-process filters."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlsplit

from .access import gitlab_env, glab_argv, kubectl_argv, kubernetes_env
from .report import report
from .runtime import error_class, run_command, run_command_tail, sanitize
from .status import PASS, UNKNOWN
from .target import Target


def _read_json(
    command: list[str], *, env: dict[str, str | None] | None = None
) -> tuple[Any | None, str | None]:
    result = run_command(command, env=env)
    if result.returncode:
        return None, error_class(result)
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError:
        return None, "invalid_json"


def _items(value: Any) -> list[dict[str, Any]]:
    """Reject malformed Kubernetes List envelopes rather than hiding data loss."""
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise TypeError("invalid_list_response")
    items = value["items"]
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("metadata"), dict)
        or not isinstance(item["metadata"].get("name"), str)
        or not item["metadata"]["name"]
        for item in items
    ):
        raise TypeError("invalid_list_response")
    return items


def _meta(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("metadata") or {}


def _search(value: dict[str, Any], needle: str | None) -> bool:
    return (
        not needle
        or needle.casefold() in json.dumps(value, ensure_ascii=False).casefold()
    )


def _matches_selector(labels: dict[str, str], selector: dict[str, Any]) -> bool:
    """Match a DaemonSet's declared Pod selector without guessing labels."""
    if not selector:
        return False
    if any(
        labels.get(key) != value
        for key, value in selector.get("matchLabels", {}).items()
    ):
        return False
    for expression in selector.get("matchExpressions", []):
        key, operator, values = (
            expression.get("key"),
            expression.get("operator"),
            expression.get("values", []),
        )
        if operator == "In" and labels.get(key) not in values:
            return False
        if operator == "NotIn" and labels.get(key) in values:
            return False
        if operator == "Exists" and key not in labels:
            return False
        if operator == "DoesNotExist" and key in labels:
            return False
        if operator not in {"In", "NotIn", "Exists", "DoesNotExist"}:
            return False
    return True


def _batch(
    target: Target, resources: dict[str, tuple[str, bool]]
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    data: dict[str, list[dict[str, Any]]] = {}
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=min(10, len(resources))) as pool:
        futures = {
            pool.submit(
                _read_json,
                kubectl_argv(
                    target,
                    "get",
                    resource,
                    *(["-A"] if namespaced else []),
                    "-o",
                    "json",
                ),
                env=kubernetes_env(target),
            ): key
            for key, (resource, namespaced) in resources.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            value, error = future.result()
            try:
                data[key] = _items(value) if not error else []
            except (ValueError, TypeError) as exc:
                data[key] = []
                error = str(exc)
            if error:
                errors.append({"source": key, "reason": error})
    return data, errors


def collect_storage(target: Target, args: Any) -> dict[str, Any]:
    resources = {
        "nodes": ("nodes", False),
        "pods": ("pods", True),
        "pvcs": ("persistentvolumeclaims", True),
        "pvs": ("persistentvolumes", False),
        "storageclasses": ("storageclasses", False),
        "csidrivers": ("csidrivers", False),
        "csinodes": ("csinodes", False),
        "attachments": ("volumeattachments", False),
        "deployments": ("deployments", True),
        "statefulsets": ("statefulsets", True),
        "daemonsets": ("daemonsets", True),
        "replicasets": ("replicasets", True),
    }
    data, errors = _batch(target, resources)
    pv_by_name = {_meta(item).get("name"): item for item in data.get("pvs", [])}
    attachments: dict[str, list[dict[str, Any]]] = {}
    for item in data.get("attachments", []):
        pv_name = item.get("spec", {}).get("source", {}).get("persistentVolumeName")
        if pv_name:
            attachments.setdefault(pv_name, []).append(
                {
                    "name": _meta(item).get("name"),
                    "node": item.get("spec", {}).get("nodeName"),
                    "attached": item.get("status", {}).get("attached"),
                }
            )
    controller_kinds = ("deployments", "statefulsets", "daemonsets")
    controller_items = {
        (_meta(item).get("namespace"), _meta(item).get("name")): item
        for kind in controller_kinds
        for item in data.get(kind, [])
    }
    replica_owners = {
        (_meta(item).get("namespace"), _meta(item).get("name")): [
            ref.get("name")
            for ref in (_meta(item).get("ownerReferences") or [])
            if ref.get("kind") == "Deployment"
        ]
        for item in data.get("replicasets", [])
    }
    pods_by_claim: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for pod in data.get("pods", []):
        meta = _meta(pod)
        namespace = meta.get("namespace")
        owners = meta.get("ownerReferences") or []
        owner_names = [ref.get("name") for ref in owners if isinstance(ref, dict)]
        for ref in owners:
            if ref.get("kind") == "ReplicaSet":
                owner_names += replica_owners.get((namespace, ref.get("name")), [])
        for volume in pod.get("spec", {}).get("volumes", []):
            claim = volume.get("persistentVolumeClaim", {}).get("claimName")
            if claim:
                pods_by_claim.setdefault((namespace, claim), []).append(
                    {
                        "pod": meta.get("name"),
                        "pod_uid": meta.get("uid"),
                        "node": pod.get("spec", {}).get("nodeName"),
                        "owners": owner_names,
                        "phase": pod.get("status", {}).get("phase"),
                    }
                )
    rows: list[dict[str, Any]] = []
    claimed_pvs: set[str] = set()
    for pvc in data.get("pvcs", []):
        meta = _meta(pvc)
        namespace, name = meta.get("namespace"), meta.get("name")
        spec, status = pvc.get("spec", {}), pvc.get("status", {})
        pv_name = spec.get("volumeName")
        if isinstance(pv_name, str) and pv_name:
            claimed_pvs.add(pv_name)
        pv = pv_by_name.get(pv_name, {})
        pods = pods_by_claim.get((namespace, name), [])
        nodes = sorted({pod["node"] for pod in pods if pod.get("node")})
        phase = status.get("phase") or "Unknown"
        pv_phase = pv.get("status", {}).get("phase")
        if args.namespace != "all" and namespace != args.namespace:
            continue
        if args.node and args.node not in nodes:
            continue
        storage_class = spec.get("storageClassName") or pv.get("spec", {}).get(
            "storageClassName"
        )
        if args.storage_class and storage_class != args.storage_class:
            continue
        if args.phase != "all" and args.phase not in (phase, pv_phase):
            continue
        related_controllers = sorted(
            {owner for pod in pods for owner in pod["owners"] if owner}
        )
        row = {
            "resource_kind": "PersistentVolumeClaim",
            "namespace": namespace,
            "name": name,
            "phase": phase,
            "pv_phase": pv_phase,
            "storage_class": storage_class,
            "volume": pv_name,
            "capacity": status.get("capacity"),
            "access_modes": spec.get("accessModes", []),
            "pods": pods,
            "nodes": nodes,
            "attachments": attachments.get(pv_name, []),
            "csi_driver": pv.get("spec", {}).get("csi", {}).get("driver"),
            "controllers": [
                {
                    "name": owner,
                    "desired": controller_items[(namespace, owner)]
                    .get("spec", {})
                    .get("replicas"),
                    "ready": controller_items[(namespace, owner)]
                    .get("status", {})
                    .get("readyReplicas"),
                }
                for owner in related_controllers
                if (namespace, owner) in controller_items
            ],
        }
        if _search(row, args.search):
            rows.append(row)
    if args.namespace == "all":
        for pv in data.get("pvs", []):
            meta = _meta(pv)
            name = meta.get("name")
            if name in claimed_pvs:
                continue
            phase = pv.get("status", {}).get("phase") or "Unknown"
            if args.phase != "all" and phase != args.phase:
                continue
            pv_attachments = attachments.get(name, [])
            nodes = sorted(
                {item["node"] for item in pv_attachments if item.get("node")}
            )
            if args.node and args.node not in nodes:
                continue
            spec = pv.get("spec", {})
            storage_class = spec.get("storageClassName")
            if args.storage_class and storage_class != args.storage_class:
                continue
            row = {
                "resource_kind": "PersistentVolume",
                "namespace": None,
                "name": name,
                "phase": phase,
                "pv_phase": phase,
                "storage_class": storage_class,
                "volume": name,
                "capacity": spec.get("capacity"),
                "access_modes": spec.get("accessModes", []),
                "pods": [],
                "nodes": nodes,
                "attachments": pv_attachments,
                "csi_driver": spec.get("csi", {}).get("driver"),
                "controllers": [],
            }
            if _search(row, args.search):
                rows.append(row)
    rows.sort(key=lambda row: (row["namespace"] or "", row["name"] or ""))
    inventory = {key: len(data.get(key, [])) for key in resources}
    result = report(
        "storage_report",
        target.kubernetes.context,
        {
            "namespace": args.namespace,
            "node": args.node,
            "storage_class": args.storage_class,
            "phase": args.phase,
            "search": args.search,
        },
        rows,
        errors,
    )
    result["inventory"] = inventory
    result["nodes"] = [
        {
            "name": _meta(node).get("name"),
            "conditions": {
                condition.get("type"): condition.get("status")
                for condition in node.get("status", {}).get("conditions", [])
            },
        }
        for node in data.get("nodes", [])
    ]
    result["storage_classes"] = [
        {"name": _meta(item).get("name"), "provisioner": item.get("provisioner")}
        for item in data.get("storageclasses", [])
    ]
    result["csi_drivers"] = [
        _meta(item).get("name") for item in data.get("csidrivers", [])
    ]
    result["controller_inventory"] = {
        kind: len(data.get(kind, [])) for kind in controller_kinds
    }
    return result


def _timestamp(value: str) -> datetime:
    candidate = value.replace("Z", "+00:00")
    stamp = datetime.fromisoformat(candidate)
    if stamp.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return stamp.astimezone(timezone.utc)


def collect_events(target: Target, args: Any) -> dict[str, Any]:
    end = _timestamp(args.to_time) if args.to_time else datetime.now(timezone.utc)
    start = _timestamp(args.from_time) if args.from_time else end - timedelta(hours=1)
    if start > end:
        raise ValueError("--from must not be after --to")
    data, errors = _batch(
        target,
        {
            "core_events": ("events", True),
            "events_v1": ("events.events.k8s.io", True),
        },
    )
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for source in ("core_events", "events_v1"):
        for event in data.get(source, []):
            meta = _meta(event)
            obj = event.get("regarding") or event.get("involvedObject") or {}
            first_source = (
                event.get("eventTime")
                or event.get("firstTimestamp")
                or meta.get("creationTimestamp")
            )
            last_source = (
                (event.get("series") or {}).get("lastObservedTime")
                or event.get("lastTimestamp")
                or event.get("deprecatedLastTimestamp")
                or first_source
            )
            if not last_source:
                continue
            try:
                event_time = _timestamp(last_source)
                first_time = _timestamp(first_source) if first_source else event_time
            except ValueError:
                errors.append({"source": source, "reason": "invalid_event_timestamp"})
                continue
            if event_time < start or event_time > end:
                continue
            namespace = meta.get("namespace") or obj.get("namespace")
            if args.namespace != "all" and namespace != args.namespace:
                continue
            if args.kind and (obj.get("kind") or "").casefold() != args.kind.casefold():
                continue
            if args.object and obj.get("name") != args.object:
                continue
            reason = event.get("reason")
            if args.reason and args.reason.casefold() not in (reason or "").casefold():
                continue
            row = {
                "timestamp": event_time.isoformat(),
                "namespace": namespace,
                "first_timestamp": first_time.isoformat(),
                "last_timestamp": event_time.isoformat(),
                "kind": obj.get("kind"),
                "name": obj.get("name"),
                "object_uid": obj.get("uid"),
                "reason": reason,
                "type": event.get("type"),
                "message": sanitize(
                    event.get("note") or event.get("message") or "", 1000
                ),
                "count": event.get("deprecatedCount") or event.get("count") or 1,
                "source": source,
            }
            key = (row["timestamp"], row["object_uid"], row["reason"], row["message"])
            if key not in seen and _search(row, args.search):
                rows.append(row)
                seen.add(key)
    rows.sort(
        key=lambda item: (
            item["timestamp"],
            item["namespace"] or "",
            item["name"] or "",
        )
    )
    return report(
        "event_trace",
        target.kubernetes.context,
        {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "namespace": args.namespace,
            "kind": args.kind,
            "object": args.object,
            "reason": args.reason,
            "search": args.search,
        },
        rows,
        errors,
    )


def collect_cilium(target: Target, args: Any) -> dict[str, Any]:
    data, errors = _batch(
        target,
        {
            "daemonsets": ("daemonsets", True),
            "pods": ("pods", True),
            "operators": ("deployments", True),
            "ciliumnodes": ("ciliumnodes.cilium.io", False),
        },
    )
    daemonsets = [
        item
        for item in data.get("daemonsets", [])
        if _meta(item).get("name", "").startswith("cilium")
    ]
    namespaces = sorted(
        {
            _meta(item).get("namespace")
            for item in daemonsets
            if _meta(item).get("namespace")
        }
    )
    if args.namespace == "auto":
        if len(namespaces) != 1:
            errors.append(
                {"source": "daemonsets", "reason": "cilium_namespace_not_unique"}
            )
            namespace = None
        else:
            namespace = namespaces[0]
    else:
        namespace = args.namespace
    selected = [
        item
        for item in daemonsets
        if _meta(item).get("namespace") == namespace
        and _meta(item).get("name") == "cilium"
    ]
    if len(selected) != 1:
        errors.append({"source": "daemonsets", "reason": "cilium_daemonset_missing"})
    selector = selected[0].get("spec", {}).get("selector", {}) if selected else {}
    if selected and not selector:
        errors.append({"source": "daemonsets", "reason": "cilium_selector_missing"})
    agent_pods = [
        item
        for item in data.get("pods", [])
        if _meta(item).get("namespace") == namespace
        and _matches_selector(_meta(item).get("labels") or {}, selector)
        and (not args.node or item.get("spec", {}).get("nodeName") == args.node)
    ]
    rows: list[dict[str, Any]] = []
    if not agent_pods:
        errors.append({"source": "pods", "reason": "cilium_agent_pods_missing"})
        rows.append(
            {
                "namespace": namespace,
                "name": "cilium-agent",
                "node": args.node,
                "pod_uid": None,
                "status": UNKNOWN,
                "reason": "no_matching_agent_pods",
                "command": ["cilium-health", "status", "-o", "json"],
                "exit_status": None,
            }
        )

    def health(pod: dict[str, Any]) -> dict[str, Any]:
        meta = _meta(pod)
        node = pod.get("spec", {}).get("nodeName")
        ready = any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in pod.get("status", {}).get("conditions", [])
        )
        command = ["cilium-health", "status", "-o", "json"]
        row = {
            "namespace": namespace,
            "name": meta.get("name"),
            "pod_uid": meta.get("uid"),
            "node": node,
            "phase": pod.get("status", {}).get("phase"),
            "ready": ready,
            "command": command,
            "status": UNKNOWN,
            "exit_status": None,
            "health": None,
        }
        if not ready:
            return row
        result = run_command(
            kubectl_argv(
                target, "-n", namespace, "exec", meta.get("name"), "--", *command
            ),
            timeout=30,
            env=kubernetes_env(target),
        )
        row["exit_status"] = result.returncode
        if result.returncode:
            row["reason"] = error_class(result)
            return row
        try:
            row["health"] = json.loads(result.stdout)
            row["status"] = PASS
        except json.JSONDecodeError:
            row["reason"] = "invalid_health_json"
        return row

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(agent_pods)))) as pool:
        for row in pool.map(health, agent_pods):
            if row["status"] == UNKNOWN:
                errors.append(
                    {
                        "source": str(row["name"]),
                        "reason": row.get("reason") or "agent_not_ready",
                    }
                )
            if _search(row, args.search):
                rows.append(row)
    rows.sort(key=lambda row: (row["node"] or "", row["name"] or ""))
    result = report(
        "cilium_status",
        target.kubernetes.context,
        {
            "namespace": namespace,
            "node": args.node,
            "search": args.search,
        },
        rows,
        errors,
    )
    result["daemonsets"] = [
        {
            "namespace": _meta(item).get("namespace"),
            "name": _meta(item).get("name"),
            "desired": item.get("status", {}).get("desiredNumberScheduled"),
            "ready": item.get("status", {}).get("numberReady"),
        }
        for item in daemonsets
        if _meta(item).get("namespace") == namespace
    ]
    result["operators"] = [
        {
            "namespace": _meta(item).get("namespace"),
            "name": _meta(item).get("name"),
            "ready": item.get("status", {}).get("readyReplicas", 0),
        }
        for item in data.get("operators", [])
        if _meta(item).get("namespace") == namespace
        and "cilium" in (_meta(item).get("name") or "")
    ]
    result["ciliumnodes"] = [
        {"name": _meta(item).get("name"), "status": item.get("status", {})}
        for item in data.get("ciliumnodes", [])
    ]
    return result


def collect_gitlab_job(target: Target, args: Any) -> dict[str, Any]:
    parsed = urlsplit(args.job_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != target.gitlab.host
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("job URL must use the exact selected GitLab FQDN")
    prefix, marker, job_id = parsed.path.rpartition("/-/jobs/")
    if not marker or not job_id.isdecimal() or not prefix.strip("/"):
        raise ValueError("job URL must contain a project path and numeric job ID")
    project = prefix.strip("/")
    credential = gitlab_env(target)
    encoded = quote(project, safe="")
    base = f"projects/{encoded}"
    job, error = _read_json(glab_argv(target, f"{base}/jobs/{job_id}"), env=credential)
    if error or not isinstance(job, dict) or str(job.get("id")) != job_id:
        return report(
            "gitlab_job",
            target.gitlab.url,
            {"job_url": args.job_url, "search": args.search},
            [],
            [{"source": "job", "reason": error or "invalid_response"}],
        )
    pipeline_ref, runner_ref = job.get("pipeline"), job.get("runner")
    pipeline_id = pipeline_ref.get("id") if isinstance(pipeline_ref, dict) else None
    runner_id = runner_ref.get("id") if isinstance(runner_ref, dict) else None
    endpoints = {
        "pipeline": f"{base}/pipelines/{pipeline_id}" if pipeline_id else None,
        "runner": f"runners/{runner_id}" if runner_id else None,
    }
    metadata: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    if not pipeline_id:
        errors.append({"source": "pipeline", "reason": "pipeline_reference_missing"})
    if not runner_id:
        errors.append({"source": "runner", "reason": "runner_reference_missing"})
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_read_json, glab_argv(target, endpoint), env=credential): name
            for name, endpoint in endpoints.items()
            if endpoint
        }
        for future in as_completed(futures):
            value, problem = future.result()
            if problem:
                errors.append({"source": futures[future], "reason": problem})
            elif not isinstance(value, dict) or str(value.get("id")) != str(
                pipeline_id if futures[future] == "pipeline" else runner_id
            ):
                errors.append({"source": futures[future], "reason": "invalid_response"})
            else:
                fields = (
                    ("id", "status", "sha", "ref", "created_at", "updated_at")
                    if futures[future] == "pipeline"
                    else (
                        "id",
                        "description",
                        "runner_type",
                        "status",
                        "tag_list",
                        "active",
                        "paused",
                        "online",
                        "contacted_at",
                    )
                )
                metadata[futures[future]] = {
                    field: value[field] for field in fields if field in value
                }
    trace_result = run_command_tail(
        glab_argv(target, f"{base}/jobs/{job_id}/trace"), timeout=30, env=credential
    )
    if trace_result.returncode:
        errors.append({"source": "trace", "reason": error_class(trace_result)})
        trace = ""
    else:
        trace = sanitize("\n".join(trace_result.stdout.splitlines()[-200:]), 64000)
    row = {
        "job_id": job.get("id"),
        "name": job.get("name"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "failure_reason": job.get("failure_reason"),
        "pipeline": metadata.get("pipeline"),
        "runner": metadata.get("runner"),
        "trace_tail": trace,
    }
    rows = [row] if _search(row, args.search) else []
    return report(
        "gitlab_job",
        target.gitlab.url,
        {"job_url": args.job_url, "search": args.search},
        rows,
        errors,
    )
