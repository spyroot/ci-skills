"""Exercise real collector reads as part of a full access receipt."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace
from typing import Any

from .collect import collect_cilium, collect_events, collect_gitlab_job, collect_storage
from .status import BLOCKED, PASS
from .target import Target


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
    receipt["live_checks"] = checks
    receipt["status"] = (
        PASS if all(check["status"] == PASS for check in checks.values()) else BLOCKED
    )
    return receipt
