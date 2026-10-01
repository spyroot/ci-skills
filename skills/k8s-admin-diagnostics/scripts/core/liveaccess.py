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
from typing import Any
from urllib.parse import quote, urlsplit

from . import receipt
from .access import (
    check_access,
    gitlab_env,
    glab_argv,
    kubectl_argv,
)
from .credsource import resolve_sources
from .reads import (
    HEALTH_COMMAND,
    agent_selector,
    collector_reads,
    read_resources,
    ready_agent_pods,
)
from .runtime import error_class, read_json, run_command, run_command_tail
from .status import BLOCKED, PASS
from .target import Target

MAX_HEALTH_ATTEMPTS = 3


def kubernetes_live_reads(
    target: Target,
) -> tuple[list[dict[str, Any]], dict[str, tuple[Any | None, str | None]]]:
    """Perform every read the collectors need and record each one's result.

    Returns the checks and the raw results, so a later check reuses these
    cluster-wide lists instead of fetching them again.
    """
    union = collector_reads()
    results = read_resources(target, {key: (key, namespaced)
                                      for key, (_r, namespaced, _s) in union.items()})
    checks: list[dict[str, Any]] = []
    for resource, (_r, _namespaced, serves) in union.items():
        value, error = results.get(resource, (None, "not_attempted"))
        items = value.get("items") if isinstance(value, dict) else None
        checks.append(receipt.live_check(
            f"kubernetes_read:{resource}", "kubernetes",
            BLOCKED if error else PASS,
            detail=error or f"{resource} readable",
            evidence={"resource": resource, "serves": list(serves),
                      "item_count": len(items) if isinstance(items, list) else None},
        ))
    return sorted(checks, key=lambda item: item["name"]), results


def cilium_health_exec(
    target: Target,
    reads: dict[str, tuple[Any | None, str | None]] | None = None,
) -> dict[str, Any]:
    """Execute the real non-TTY health command on a ready agent.

    This is the check an `auth can-i create pods/exec` answer cannot stand in
    for: permission to exec is not proof that the command runs and returns
    parseable health. Agents are selected by the `cilium` DaemonSet's own
    selector, so `cilium-operator-*` and `cilium-envoy-*` are never execed
    into, and attempts are capped so a cluster whose health is broken
    everywhere reports instead of serially timing out on every agent.
    """
    if reads is None:
        reads = read_resources(target, {"daemonsets": ("daemonsets", True),
                                        "pods": ("pods", True)})
    daemonsets, daemonset_error = reads.get("daemonsets", (None, "not_attempted"))
    pods, pod_error = reads.get("pods", (None, "not_attempted"))
    if daemonset_error or pod_error:
        return receipt.live_check("cilium_health_exec", "kubernetes", BLOCKED,
                                  detail=daemonset_error or pod_error)

    items = (daemonsets or {}).get("items") or [] if isinstance(daemonsets, dict) else []
    namespaces = sorted({
        (item.get("metadata") or {}).get("namespace") for item in items
        if (item.get("metadata") or {}).get("name") == "cilium"
        and (item.get("metadata") or {}).get("namespace")
    })
    if len(namespaces) != 1:
        return receipt.live_check("cilium_health_exec", "kubernetes", BLOCKED,
                                  detail="cilium_namespace_not_unique")
    namespace = namespaces[0]
    selector = agent_selector(items, namespace)
    if selector is None:
        return receipt.live_check("cilium_health_exec", "kubernetes", BLOCKED,
                                  detail="cilium_daemonset_selector_missing",
                                  evidence={"namespace": namespace})
    ready = ready_agent_pods(
        (pods or {}).get("items") or [] if isinstance(pods, dict) else [], selector, namespace,
    )
    if not ready:
        return receipt.live_check("cilium_health_exec", "kubernetes", BLOCKED,
                                  detail="no_ready_cilium_agent",
                                  evidence={"namespace": namespace})

    attempts: list[dict[str, Any]] = []
    for candidate in ready[:MAX_HEALTH_ATTEMPTS]:
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
                      "agents_ready": len(ready),
                      "attempt_cap": MAX_HEALTH_ATTEMPTS},
        )
    return receipt.live_check(
        "cilium_health_exec", "kubernetes", BLOCKED,
        detail="no_ready_agent_answered_health",
        evidence={"namespace": namespace, "command": list(HEALTH_COMMAND),
                  "agents_ready": len(ready), "attempt_cap": MAX_HEALTH_ATTEMPTS,
                  "attempts": attempts},
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
                      "tail": lines[-1] if lines else ""},
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
        read_checks, results = kubernetes_live_reads(target)
        checks.extend(read_checks)
        checks.append(cilium_health_exec(target, results))
    if job_url and surfaces.get("gitlab", {}).get("status") == PASS:
        checks.extend(gitlab_job_access(target, job_url))

    return receipt.build(target, sources=sources, surfaces=surfaces,
                         checks=checks, publication=publication)
