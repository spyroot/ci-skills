"""Mandatory credential-source and live-access gate.

The surface gate in `access.py` proves identity and authorization. This module
adds what acceptance actually requires: the effective credential source behind
each authority, the real resource reads every collector depends on, the real
non-TTY Cilium health command rather than an `auth can-i` answer about it, and
proof of the requested job, pipeline, runner and trace when job diagnostics are
in scope. It then emits one receipt for this execution host.

It is additive. `access.py` and the collectors are unchanged and keep working
on their own; this module composes them and uses the same resolved credential
sources and the same explicitly selected targets, so a gate pass and a
collector run can never be authenticated by different credentials.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import quote, urlsplit

from . import receipt
from .access import (
    check_access,
    cilium_namespace,
    gitlab_env,
    glab_argv,
    kubectl_argv,
)
from .credsource import resolve_sources

# Reused rather than reimplemented: one JSON-reading adapter for both the
# collectors and this gate, so a read cannot succeed here and fail there.
from .collect import _read_json as read_json
from .runtime import error_class, run_command, run_command_tail, sanitize
from .status import BLOCKED, PASS
from .target import Target

HEALTH_COMMAND = ("cilium-health", "status", "-o", "json")

# The union of every resource the three Kubernetes collectors read, tagged with
# the collector each one serves. Mirrors the maps in collect.py.
COLLECTOR_READS: dict[str, tuple[str, bool, tuple[str, ...]]] = {
    "nodes": ("nodes", False, ("storage",)),
    "pods": ("pods", True, ("storage", "cilium")),
    "pvcs": ("persistentvolumeclaims", True, ("storage",)),
    "pvs": ("persistentvolumes", False, ("storage",)),
    "storageclasses": ("storageclasses", False, ("storage",)),
    "csidrivers": ("csidrivers", False, ("storage",)),
    "csinodes": ("csinodes", False, ("storage",)),
    "attachments": ("volumeattachments", False, ("storage",)),
    "deployments": ("deployments", True, ("storage", "cilium")),
    "statefulsets": ("statefulsets", True, ("storage",)),
    "daemonsets": ("daemonsets", True, ("storage", "cilium")),
    "replicasets": ("replicasets", True, ("storage",)),
    "core_events": ("events", True, ("events",)),
    "events_v1": ("events.events.k8s.io", True, ("events",)),
    "ciliumnodes": ("ciliumnodes.cilium.io", False, ("cilium",)),
}


def kubernetes_live_reads(target: Target) -> list[dict[str, Any]]:
    """Perform every read the collectors need and record each one's result."""
    checks: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(10, len(COLLECTOR_READS))) as pool:
        futures = {
            pool.submit(
                read_json,
                kubectl_argv(target, "get", resource, *(["-A"] if namespaced else []), "-o", "json"),
            ): (key, resource, collectors)
            for key, (resource, namespaced, collectors) in COLLECTOR_READS.items()
        }
        for future in as_completed(futures):
            key, resource, collectors = futures[future]
            value, error = future.result()
            items = value.get("items") if isinstance(value, dict) else None
            checks.append(receipt.live_check(
                f"kubernetes_read:{key}", "kubernetes",
                BLOCKED if error else PASS,
                detail=error or f"{resource} readable",
                evidence={"resource": resource, "serves": list(collectors),
                          "item_count": len(items) if isinstance(items, list) else None},
            ))
    return sorted(checks, key=lambda item: item["name"])


def cilium_health_exec(target: Target) -> dict[str, Any]:
    """Execute the real non-TTY health command on a ready agent.

    This is the check an `auth can-i create pods/exec` answer cannot stand in
    for: permission to exec is not proof that the command runs and returns
    parseable health. A cluster with no ready agent blocks, because the Cilium
    collector cannot produce evidence there either.
    """
    try:
        namespace = cilium_namespace(target)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        return receipt.live_check("cilium_health_exec", "kubernetes", BLOCKED, detail=str(exc))

    pods, error = read_json(kubectl_argv(target, "get", "pods", "-n", namespace, "-o", "json"))
    if error or not isinstance(pods, dict):
        return receipt.live_check("cilium_health_exec", "kubernetes", BLOCKED,
                                  detail=error or "invalid_pod_response")
    ready = [
        item for item in pods.get("items") or []
        if isinstance(item, dict)
        and (item.get("metadata") or {}).get("name", "").startswith("cilium-")
        and any(condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in (item.get("status") or {}).get("conditions") or [])
    ]
    if not ready:
        return receipt.live_check("cilium_health_exec", "kubernetes", BLOCKED,
                                  detail="no_ready_cilium_agent",
                                  evidence={"namespace": namespace})
    # Exec access is a property of the cluster, not of one pod: a single agent
    # can be evicted or time out while the permission is intact, so try each
    # ready agent and block only when none answers.
    attempts: list[dict[str, Any]] = []
    for candidate in ready:
        pod = (candidate.get("metadata") or {}).get("name")
        result = run_command(
            kubectl_argv(target, "-n", namespace, "exec", pod, "--", *HEALTH_COMMAND), timeout=30,
        )
        if result.returncode:
            attempts.append({"pod": pod, "reason": error_class(result)})
            continue
        try:
            parsed = json.loads(result.stdout)
        except ValueError:
            attempts.append({"pod": pod, "reason": "invalid_health_json"})
            continue
        local = ((parsed or {}).get("local") or {}).get("name") if isinstance(parsed, dict) else None
        peers = (parsed or {}).get("nodes") if isinstance(parsed, dict) else None
        return receipt.live_check(
            "cilium_health_exec", "kubernetes", PASS,
            detail="non-TTY health command returned parseable status",
            evidence={"namespace": namespace, "pod": pod, "command": list(HEALTH_COMMAND),
                      "local_node": local,
                      "peer_count": len(peers) if isinstance(peers, list) else None,
                      "agents_attempted": len(attempts) + 1,
                      "agents_ready": len(ready)},
        )
    return receipt.live_check(
        "cilium_health_exec", "kubernetes", BLOCKED,
        detail="no_ready_agent_answered_health",
        evidence={"namespace": namespace, "command": list(HEALTH_COMMAND),
                  "agents_ready": len(ready), "attempts": attempts},
    )


def parse_job_url(target: Target, job_url: str) -> tuple[str, str]:
    """Split a job URL, requiring the exact selected GitLab FQDN."""
    parsed = urlsplit(job_url)
    if parsed.scheme != "https" or parsed.netloc.lower() != target.gitlab.host:
        raise ValueError("job_url_wrong_host")
    prefix, marker, job_id = parsed.path.rpartition("/-/jobs/")
    if not marker or not job_id.isdecimal() or not prefix.strip("/"):
        raise ValueError("job_url_malformed")
    return prefix.strip("/"), job_id


def gitlab_job_access(target: Target, job_url: str) -> list[dict[str, Any]]:
    """Prove read access to the requested job, pipeline, runner and trace."""
    try:
        project, job_id = parse_job_url(target, job_url)
    except ValueError as exc:
        return [receipt.live_check("gitlab_job_access", "gitlab", BLOCKED, detail=str(exc))]

    credential = gitlab_env(target)
    base = f"projects/{quote(project, safe='')}"
    job, error = read_json(glab_argv(target, f"{base}/jobs/{job_id}"), env=credential)
    if error or not isinstance(job, dict):
        return [receipt.live_check("gitlab_job_access", "gitlab", BLOCKED,
                                   detail=error or "invalid_job_response",
                                   evidence={"job_url": job_url})]
    checks = [receipt.live_check(
        "gitlab_job_access", "gitlab", PASS,
        detail="job readable",
        evidence={"job_id": job.get("id"), "name": job.get("name"),
                  "status": job.get("status"), "failure_reason": job.get("failure_reason")},
    )]

    pipeline_id = (job.get("pipeline") or {}).get("id")
    runner_id = (job.get("runner") or {}).get("id")
    for name, endpoint, keys in (
        ("gitlab_pipeline_access", f"{base}/pipelines/{pipeline_id}" if pipeline_id else None,
         ("id", "ref", "sha", "status")),
        ("gitlab_runner_access", f"runners/{runner_id}" if runner_id else None,
         ("id", "description", "online", "tag_list")),
    ):
        if endpoint is None:
            checks.append(receipt.live_check(name, "gitlab", BLOCKED, detail="not_referenced_by_job"))
            continue
        value, problem = read_json(glab_argv(target, endpoint), env=credential)
        if problem or not isinstance(value, dict):
            checks.append(receipt.live_check(name, "gitlab", BLOCKED,
                                             detail=problem or "invalid_response"))
            continue
        checks.append(receipt.live_check(
            name, "gitlab", PASS, detail="readable",
            evidence={key: value.get(key) for key in keys},
        ))

    trace = run_command_tail(glab_argv(target, f"{base}/jobs/{job_id}/trace"),
                             timeout=30, env=credential)
    if trace.returncode:
        checks.append(receipt.live_check("gitlab_trace_access", "gitlab", BLOCKED,
                                         detail=error_class(trace)))
    else:
        lines = trace.stdout.splitlines()
        checks.append(receipt.live_check(
            "gitlab_trace_access", "gitlab", PASS, detail="trace readable",
            evidence={"line_count": len(lines),
                      "tail": sanitize(lines[-1], 200) if lines else ""},
        ))
    return checks


def verify(
    target: Target,
    *,
    job_url: str | None = None,
    publication: bool = False,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve sources, run every required live check, and build the receipt."""
    sources = resolve_sources(target, environ)
    surfaces = check_access(target, publication=publication)["surfaces"]

    checks: list[dict[str, Any]] = []
    for name, surface in surfaces.items():
        checks.append(receipt.live_check(
            f"surface:{name}", name, surface.get("status", BLOCKED),
            detail=surface.get("reason") or "surface gate passed",
            evidence={"identity": surface.get("identity"),
                      "target": surface.get("target"),
                      "observed_capability": surface.get("observed_capability")},
        ))

    # Only exercise the deeper live reads once the surface they belong to is
    # authorized; otherwise every read repeats the same denial.
    if surfaces.get("kubernetes", {}).get("status") == PASS:
        checks.extend(kubernetes_live_reads(target))
        checks.append(cilium_health_exec(target))
    if job_url and surfaces.get("gitlab", {}).get("status") == PASS:
        checks.extend(gitlab_job_access(target, job_url))

    return receipt.build(target, sources=sources, surfaces=surfaces,
                         checks=checks, publication=publication)
