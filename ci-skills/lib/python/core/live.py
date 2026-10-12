"""Exercise real collector reads as part of a full access receipt.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
from argparse import Namespace
from collections.abc import Callable, MutableMapping
from concurrent.futures import ThreadPoolExecutor, as_completed

from lib.python.core.gitlab.job_read import collect_gitlab_job
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json,
    json_array,
    json_object,
    json_objects,
)
from lib.python.core.kubernetes.ceph_cluster import collect_ceph_cluster
from lib.python.core.kubernetes.cilium_status import collect_cilium
from lib.python.core.kubernetes.events import collect_events
from lib.python.core.kubernetes.storage import collect_storage
from lib.python.core.target import Target
from lib.python.sys.status import BLOCKED, PARTIAL, PASS

# An access gate proves access, not cluster health. This skill exists to be run
# ON a degraded cluster, so a component that is itself unhealthy must not block
# the gate that lets the diagnostics run. These reasons describe an observed
# workload state. A DENIAL -- authentication, authorization, missing_tool --
# always blocks. Any other read failure blocks a single-shot collector; for
# Cilium, which reads per agent, it is that agent's state if others answered.
COMPONENT_STATE_REASONS = frozenset(
    {
        "agent_not_ready",
        "no_matching_agent_pods",
    }
)

# A denial is never cluster state, however many other reads succeeded.
DENIAL_REASONS = frozenset({"authentication", "authorization", "missing_tool"})

# `collect_cilium` reports per-agent failures under the agent's Pod name and
# list-read or discovery failures under the batch key. Only the former have the
# redundancy that makes one failure tolerable.
CILIUM_REPORT_SOURCES = frozenset({"daemonsets", "pods", "operators", "ciliumnodes"})


def _access_proven(name: str, evidence: JSONObject) -> bool:
    """Decide whether a collector proved access despite component health findings.

    :param name: Collector kind, used for Cilium's per-agent rule.
    :param evidence: Bounded status, errors, record count, and optional agent
        health summary from one collector.
    :returns: Whether the read proved access; explicit denials and incomplete
        report-level reads block it.
    :raises TypeError: If errors or agent health do not have the declared JSON shapes.
    """  # noqa: DOC502 - Shared JSON shape readers reject malformed evidence.
    if evidence.get("status") == PASS:
        return True
    if evidence.get("status") != PARTIAL:
        return False
    errors = json_objects(evidence.get("errors") or [])
    reasons = {str(error.get("reason")) for error in errors}
    if not reasons or reasons & DENIAL_REASONS:
        return False
    if name == "cilium_status":
        # One agent failing while others answer is that agent's state. A failed
        # LIST read is not: it means the report itself is incomplete, so
        # `ciliumnode_count: 0` would otherwise certify access. Only per-agent
        # reasons are excused, and only when a real agent answered.
        report_level = {
            str(error.get("reason"))
            for error in errors
            if error.get("source") in CILIUM_REPORT_SOURCES
        }
        if not report_level <= COMPONENT_STATE_REASONS:
            return False
        passed = json_object(evidence.get("agent_health") or {}).get("passed", 0)
        return isinstance(passed, int) and not isinstance(passed, bool) and passed >= 1
    # A single-shot collector has no second attempt to fall back on, so any
    # reason outside the known component states blocks.
    if not reasons <= COMPONENT_STATE_REASONS:
        return False
    count = evidence.get("record_count", 0)
    return isinstance(count, int) and not isinstance(count, bool) and count >= 1


def _evidence(result: JSONObject) -> MutableMapping[str, JSONValue]:
    """Summarize one collector result for the live access receipt.

    :param result: Complete collector report to hash and summarize.
    :returns: Status, counts, errors, SHA-256 of the serialized report, and
        kind-specific read-back fields.
    :raises TypeError: If collector records, errors, or related fields have invalid shapes.
    :raises ValueError: If collector evidence contains nonfinite numbers.
    :raises RecursionError: If collector evidence contains a cycle or excessive nesting.
    """  # noqa: DOC502 - JSON shape readers, copying, and serialization can fail.
    body = json.dumps(copy_json(result), sort_keys=True, allow_nan=False).encode(
        "utf-8"
    )
    records = json_objects(result.get("records", []))
    errors = json_objects(result.get("errors", []))
    evidence: MutableMapping[str, JSONValue] = {
        "status": result.get("status", BLOCKED),
        "record_count": len(records),
        "error_count": len(errors),
        "errors": errors,
        "readback_sha256": hashlib.sha256(body).hexdigest(),
    }
    if result.get("kind") == "storage_report":
        evidence["resource_counts"] = result.get("inventory", {})
    elif result.get("kind") == "cilium_status":
        evidence["agent_health"] = {
            "passed": sum(row.get("status") == PASS for row in records),
            "total": len(records),
        }
        evidence["ciliumnode_count"] = len(json_array(result.get("ciliumnodes", [])))
    elif result.get("kind") == "gitlab_job":
        evidence["job_url"] = json_object(result.get("filters") or {}).get("job_url")
        if records:
            job = records[0]
            evidence["job_readback"] = {
                "job_id": job.get("job_id"),
                "pipeline_id": json_object(job.get("pipeline") or {}).get("id"),
                "runner_id": json_object(job.get("runner") or {}).get("id"),
                "trace_line_count": len(str(job.get("trace_tail") or "").splitlines()),
            }
    elif result.get("kind") == "ceph_cluster":
        evidence["namespace"] = json_object(result.get("filters") or {}).get(
            "namespace"
        )
        evidence["health"] = result.get("health")
        evidence["osd_count"] = len(json_array(result.get("osds", [])))
        evidence["inactive_pg_count"] = len(json_array(result.get("inactive_pgs", [])))
        evidence["pod_count"] = result.get("pod_count")

    return evidence


def collect_live_checks(
    target: Target, args: Namespace, receipt: MutableMapping[str, JSONValue]
) -> MutableMapping[str, JSONValue]:
    """Run independent collectors and attach their access evidence to a receipt.

    :param target: Bound target shared by all selected collectors.
    :param args: Optional GitLab job URL and Ceph namespace enabling extra reads.
    :param receipt: Mutable access receipt updated with live checks and blockers.
    :returns: Same receipt with ``live_checks``, aggregate status, and sorted
        blocking collector names.
    :raises RuntimeError: If the worker pool cannot start or submit its diagnostic reads.
    """  # noqa: DOC502 - Executor setup and task submission may fail.
    tasks: MutableMapping[
        str, tuple[Callable[[Target, Namespace], JSONObject], Namespace]
    ] = {
        "storage_report": (
            collect_storage,
            Namespace(
                namespace="all", node=None, storage_class=None, phase="all", search=None
            ),
        ),
        "event_trace": (
            collect_events,
            Namespace(
                from_time=None,
                to_time=None,
                namespace="all",
                kind=None,
                object=None,
                reason=None,
                search=None,
            ),
        ),
        "cilium_status": (
            collect_cilium,
            Namespace(namespace="auto", node=None, search=None),
        ),
    }
    if getattr(args, "job_url", None):
        tasks["gitlab_job"] = (
            collect_gitlab_job,
            Namespace(job_url=args.job_url, search=None),
        )

    if getattr(args, "ceph_namespace", None):
        tasks["ceph_cluster"] = (
            collect_ceph_cluster,
            Namespace(
                namespace=args.ceph_namespace,
                operator=None,
                conf=None,
                node=None,
                ready="all",
                condition=None,
            ),
        )

    checks: MutableMapping[str, MutableMapping[str, JSONValue]] = {}

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {
            pool.submit(function, target, options): name
            for name, (function, options) in tasks.items()
        }

        for future in as_completed(futures):
            name = futures[future]
            try:
                checks[name] = _evidence(future.result())

            except Exception:  # noqa: BLE001 - isolate one failed live collector in the receipt
                checks[name] = {
                    "status": BLOCKED,
                    "record_count": 0,
                    "error_count": 1,
                    "errors": [{"source": name, "reason": "collector_runtime_failure"}],
                    "readback_sha256": None,
                }

    for name, evidence in checks.items():
        evidence["access_proven"] = _access_proven(name, evidence)

    receipt["live_checks"] = checks
    receipt["status"] = (
        PASS if all(check["access_proven"] for check in checks.values()) else BLOCKED
    )
    receipt["blocking_live_checks"] = sorted(
        name for name, check in checks.items() if not check["access_proven"]
    )
    return receipt
