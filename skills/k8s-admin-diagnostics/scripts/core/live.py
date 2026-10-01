"""Exercise real collector reads as part of a full access receipt."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace
from typing import Any

from .collect import collect_cilium, collect_events, collect_gitlab_job, collect_storage
from .status import BLOCKED, PARTIAL, PASS
from .target import Target

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


def _access_proven(name: str, evidence: dict[str, Any]) -> bool:
    """Decide whether one collector demonstrated access, health aside.

    A read that failed blocks. A read that succeeded while reporting an
    unhealthy component does not, provided the capability it exists to prove was
    demonstrated at least once.
    """
    if evidence.get("status") == PASS:
        return True
    if evidence.get("status") != PARTIAL:
        return False
    reasons = {error.get("reason") for error in evidence.get("errors", [])}
    if not reasons or reasons & DENIAL_REASONS:
        return False
    if name == "cilium_status":
        # Cilium is read per agent, so it has redundancy the other collectors
        # do not: one agent timing out or erroring while others answer is that
        # agent's state, not a denial. The capability is proven when at least
        # one real agent returned parseable health and nothing was denied.
        return (evidence.get("agent_health") or {}).get("passed", 0) >= 1
    # A single-shot collector has no second attempt to fall back on, so any
    # reason outside the known component states blocks.
    if not reasons <= COMPONENT_STATE_REASONS:
        return False
    return evidence.get("record_count", 0) >= 1


def _evidence(result: dict[str, Any]) -> dict[str, Any]:
    """Record bounded, value-free proof of one completed resource read."""
    body = json.dumps(result, sort_keys=True, default=str).encode("utf-8")
    evidence = {
        "status": result.get("status", BLOCKED),
        "record_count": len(result.get("records", [])),
        "error_count": len(result.get("errors", [])),
        "errors": result.get("errors", []),
        "readback_sha256": hashlib.sha256(body).hexdigest(),
    }
    if result.get("kind") == "storage_report":
        evidence["resource_counts"] = result.get("inventory", {})
    elif result.get("kind") == "cilium_status":
        evidence["agent_health"] = {
            "passed": sum(
                row.get("status") == PASS for row in result.get("records", [])
            ),
            "total": len(result.get("records", [])),
        }
        evidence["ciliumnode_count"] = len(result.get("ciliumnodes", []))
    elif result.get("kind") == "gitlab_job":
        records = result.get("records", [])
        if records:
            job = records[0]
            evidence["job_readback"] = {
                "job_id": job.get("job_id"),
                "pipeline_id": (job.get("pipeline") or {}).get("id"),
                "runner_id": (job.get("runner") or {}).get("id"),
                "trace_line_count": len((job.get("trace_tail") or "").splitlines()),
            }
    return evidence


def collect_live_checks(
    target: Target, args: Any, receipt: dict[str, Any]
) -> dict[str, Any]:
    """Run independent collectors concurrently using the same bound sources."""
    tasks = {
        "storage_report": (
            collect_storage,
            SimpleNamespace(
                namespace="all", node=None, storage_class=None, phase="all", search=None
            ),
        ),
        "event_trace": (
            collect_events,
            SimpleNamespace(
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
            SimpleNamespace(namespace="auto", node=None, search=None),
        ),
    }
    if getattr(args, "job_url", None):
        tasks["gitlab_job"] = (
            collect_gitlab_job,
            SimpleNamespace(job_url=args.job_url, search=None),
        )
    checks: dict[str, dict[str, Any]] = {}
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
