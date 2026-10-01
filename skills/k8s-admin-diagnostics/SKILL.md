---
name: k8s-admin-diagnostics
description: Collect read-only, correlated GitLab CI, Kubernetes storage, event, and Cilium evidence after explicit GitHub, GitLab, and cluster administrator access checks.
---

# Kubernetes admin diagnostics

Use this skill to diagnose a Kubernetes-backed CI failure or cluster storage and network symptom. The scripts run on the computer or runner where you invoke them. Installation provides no credentials or API permissions.

1. Obtain the operator's nonsecret `--target PATH` TOML file and run `scripts/access_check.py --target PATH --json`. Read [access.md](references/access.md) for the exact identity and authority checks. A `DRY_RUN` is a probe plan, never access evidence. Stop live collection if any surface is `BLOCKED`.
2. For a CI job, run `scripts/gitlab_job.py --target PATH --job-url URL --json`. Start from its actual job interval, Pod identity, node, and runner details.
3. Invoke `scripts/storage_report.py`, `scripts/event_trace.py`, and `scripts/cilium_status.py` concurrently when their inputs are independent. Use their native `--namespace`, `--node`, time, object, reason, and `--search` filters; avoid shell pipelines that lose identity or timing.
4. Correlate results by job time, Pod UID, node, PVC, and event reason. State each conclusion as **confirmed** (direct event/read-back), **correlated** (matching time and identity), or **unverified** (missing or expired evidence). Preserve an `UNKNOWN` agent health status when the health command could not run.
5. Use `--output-dir PATH` only when persistent paired JSON and human reports are requested. Review artifacts before sharing; cluster event messages and CI traces can contain sensitive data.

All scripts use read-only API calls and non-TTY `kubectl exec` for ready Cilium agent health. They never change context, log in, or grant roles. The full access gate runs inside every live script invocation, including when scripts are launched concurrently.
