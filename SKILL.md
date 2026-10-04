---
name: ci-skills
description: Use repeatable GitLab CI and Kubernetes/OpenShift tools for jobs, milestones, issues, runners, storage, events, Cilium, Ceph, node MTUs, API reads, and exact-commit build plans.
---

# CI Skills

Use the maintained commands in this skill for CI and cluster work. The directory
containing this file is the skill root. Keep the working directory at the
calling project root. Run Python commands with Python 3.11 or newer; with the
documented environment, use `conda run -n ci-skills python` followed by the
command's path under the skill root. Invoke Bash commands by their paths.
The project target resolver reads `./.ci-skills/target.toml` from the working
directory. Do not rebuild a supported operation with one-off `gh`, `glab`,
`kubectl`, `jq`, or shell parsing.

## Select the tool

The Python commands below are in `skills/k8s-admin-diagnostics/scripts/` under
the skill root. The Bash commands are in `bin/`.

| Task | Command |
| --- | --- |
| Verify all three selected authorities | `access_check.py` |
| Read a GitLab job, runner, and trace | `gitlab_job.py` |
| Read pipeline progress and jobs | `gitlab_pipeline.py` |
| Create, update, or date a milestone | `gitlab_milestone.py` |
| Open a bug issue | `gitlab_issue.py` |
| Create or update a wiki page | `gitlab_wiki.py` |
| Create or assign a runner record | `gitlab_runner.py` |
| Inspect volumes and claims | `storage_report.py` |
| Trace events in a time window | `event_trace.py` |
| Inspect Cilium cluster health | `cilium_status.py` |
| Inspect one Cilium agent | `cilium_node.py` |
| Inspect Ceph health, hierarchy, and Pods | `ceph_cluster.py` |
| Classify Ceph/RBD kernel messages | `ceph_kernel.py` |
| Check physical PCI NIC MTUs across nodes | `k8s_verify_mtu_consistency.py` |
| Read a GitHub or GitLab API endpoint | `ci-api` |
| Plan an exact-commit OpenShift Binary BuildConfig | `ci-binary-build` |

For a Ceph network or RBD timeout symptom, use the Ceph and event commands,
then the MTU command when physical network consistency matters. For a Cilium
symptom, use the cluster status command first; use its per-node command for an
affected node. Read the diagnostic provider's
[SKILL.md](skills/k8s-admin-diagnostics/SKILL.md) and
[tools.json](skills/k8s-admin-diagnostics/tools.json) for exact options,
filters, output fields, target resolution, and result meanings. Every command
has `--help`; Python commands also have `--describe` for machine-readable
contracts.

The selected target comes from `--target`, `CI_SKILLS_TARGET`, the calling
project's `.ci-skills/target.toml`, or the user's `~/.ci-skills/target.toml`, in
that order. `K8S_ADMIN_DIAGNOSTICS_BINDING` selects an existing project binding
at the environment tier; setting both environment selectors blocks. The
diagnostic provider resolves the effective credentials and
verifies the exact authority before live reads or writes. Never invent a token
path, kubeconfig, context, project, or API host. A selected target file is
complete on its own; missing fields do not come from a lower-priority file.

GitLab write commands default to a dry-run plan. Use `--apply --confirm-plan
DIGEST` only for a requested write, using the digest from that command's plan.
The MTU command also plans first; its apply creates temporary OpenShift debug
Pods, reads the host interfaces, removes those Pods, and verifies cleanup. A
`DRY_RUN` plan does not prove live MTUs. Treat `PARTIAL` as evidence of a
completed read with findings, and `BLOCKED` as a failed access or read; inspect
the command's structured result.

`bin/ci-api` is a bounded GET fallback. Supply its exact provider, host, and
endpoint, and pass `--token-file` when the selected target declares one. It
does not resolve `target.toml` and cannot prove that a configured project's
credential was used. Keep its output separate from exact-target receipts; use
a domain command above when target-bound proof is required.
`bin/ci-binary-build` only produces a plan; it does not change the cluster.
`scripts/check.sh --dry-run` shows the Bash gate, while live validation runs
only on the approved CI execution surface.

Install this skill with `install.sh --help` from a clean, committed checkout;
see [README.md](README.md) for setup, dependencies, and target configuration.
