---
name: k8s-admin-diagnostics
description: Collect read-only GitLab CI, Kubernetes storage, event, and Cilium evidence after GitHub, GitLab, and cluster administrator access checks.
---

# Kubernetes admin diagnostics

Use this skill to diagnose Kubernetes-backed CI, storage, and network
symptoms. Commands run on the computer or runner where invoked. Installation
provides no credentials or API permissions.

1. Obtain the operator's nonsecret `--target PATH` TOML file. Run
   `scripts/access_check.py --target PATH --json --publication` on the actual
   execution host. Add `--job-url URL` for a selected GitLab job. Pass the
   exact source commit as `--revision SHA` if the installed copy has no Git
   metadata. Read [access.md](references/access.md) for the live contract.
   `DRY_RUN` is a plan, never access evidence.
2. For a CI job, run `scripts/gitlab_job.py --target PATH --job-url URL --json`.
   Start with its actual job interval, Pod identity, node, and runner details.
3. Run `scripts/storage_report.py`, `scripts/event_trace.py`, and
   `scripts/cilium_status.py` concurrently when inputs are independent. Use
   their native filters to preserve identities and timing.
4. Correlate results by job time, Pod UID, node, PVC, and event reason. Mark
   conclusions **confirmed** by direct read-back, **correlated** by matching
   time and identity, or **unverified** when evidence is missing or expired.
   Preserve `UNKNOWN` when agent health cannot run.
5. Use `--output-dir PATH` only when persistent paired JSON and human reports
   are requested. Review artifacts before sharing; events and traces may
   contain sensitive data.
6. On a selected Linux node, run `scripts/cilium_node.py --json` to inspect
   the local CRI `cilium-agent` without a TTY. Run
   `scripts/ceph_kernel.py --json` for recent Ceph/RBD kernel messages and
   action codes. These node-local commands use `sudo -n`, require no API
   target file, and do not establish the three-surface access receipt.

Every live invocation resolves the same credential sources and target for its
access gate and collector. The gate runs real storage, event, and Cilium reads,
including non-TTY health. No command logs in, changes context, or grants roles.
