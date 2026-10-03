---
name: k8s-admin-diagnostics
description: Diagnose GitLab CI, Kubernetes storage, Cilium, Ceph, and physical NIC MTUs; perform selected GitLab milestone, issue, wiki, and runner operations.
metadata:
  manifest: tools.json
  default_output: json when stdout is not a terminal
  read_only: false
---

# Kubernetes diagnostics and GitLab operations

Diagnose a Kubernetes-backed CI, storage, or network symptom, or act on an
explicit GitLab request. GitLab operation commands default to an offline
dry-run and write only with `--apply` and the printed plan fingerprint.
`k8s_verify_mtu_consistency.py --apply` creates temporary OpenShift debug Pods
and verifies cleanup; its default invocation reads the selected node inventory
and returns a plan.

## 1. Choose the command for the symptom

Run the relevant command from `tools.json` directly. Each command resolves and
checks only its declared authority: GitLab for a job, or Kubernetes for storage,
events, Cilium, and Ceph. Its `access` field reports the effective source and
identity. A Kubernetes-only target needs only `[kubernetes]`; a GitLab-only
target needs only `[gitlab]`. The selected target file never inherits missing
tables from a lower tier. For a selected GitLab operation, its command also
reads back GitLab identity and target before applying a change.

For one receipt proving all three authorities, run:

    scripts/access_check.py --json

Add `--publication` when repository administration and required-check read-back
are part of the requested proof. Add `--ceph-namespace NAME` when the receipt
must also prove the Ceph collector in that selected namespace. Ceph, storage,
events, and kernel diagnostics require base Kubernetes access; Cilium discovery
and health exec are checked only for Cilium commands and the full receipt.

Do not go looking for credentials. The selected command resolves them and
tells you what it used. For the full access receipt:

- `PASS` — `credential_sources` names the effective source per required
  authority and `surfaces.<name>.identity` names the identity read back.
- `BLOCKED` — `surfaces.<name>.reason` names what failed and `next_step` what
  to do. Report that; do not try other credentials.

Access is resolved from declared locations, first match wins, and the match is
reported. The chain per authority is in `tools.json` under `access_protocol`,
and [references/access.md](references/access.md) is the full contract.
The installed `gh` environment contract uses `GH_TOKEN`/`GITHUB_TOKEN` for
`github.com` and `*.ghe.com`, and enterprise token variables for other GitHub
Enterprise Server hosts. A selected token file clears ambient GitHub tokens.
For Kubernetes, the target may declare `kubernetes.kubeconfig` or the ordered
`kubernetes.kubeconfigs` path. Otherwise resolution uses `KUBECONFIG`, then
`~/.kube/config`. Every route must match the target's context and API server;
the skill does not substitute an ambient current context. For a project-specific
kubeconfig resolver, use the [project binding](references/project-binding.md)
and its declared sources.

Provisioning access is not this skill's job. If a kubeconfig has to be fetched
or minted first, that belongs to the calling project's own instructions.

## 2. Read the command manifest

`tools.json` beside this file is the machine-readable contract for API and
node commands: their purpose, when to use them, required authorities or
execution surface, options, and symptom routing. `<command> --describe` prints
one command's contract as JSON and needs no credentials.

Routing, in short:

| You need | Command |
| --- | --- |
| proof of access across all three authorities | `access_check.py` |
| a named CI job's own facts | `gitlab_job.py --job-url URL` |
| pipeline progress | `gitlab_pipeline.py --project PATH --pipeline-id ID` |
| why a volume or claim is stuck | `storage_report.py` |
| what the cluster said during an interval | `event_trace.py --last 15m` |
| connectivity, or CNI health per node | `cilium_status.py` |
| read back GitLab target and identity | `gitlab_access.py check` |
| create, update, or adjust milestone dates | `gitlab_milestone.py` |
| open a bug issue | `gitlab_issue.py open-bug` |
| create or update a wiki page | `gitlab_wiki.py` |
| assign or create a runner record | `gitlab_runner.py` |
| Ceph hierarchy and Pods | `ceph_cluster.py --namespace NAME` |
| physical PCI NIC MTU mismatch across nodes | `k8s_verify_mtu_consistency.py` |
| Cilium daemon and health on a selected node | `cilium_node.py` |
| Ceph or RBD kernel messages on that node | `ceph_kernel.py` |

## 3. One target and output interface

Use `<command> --describe` for its exact options. Commands share `--target`,
`--json`, `--yaml`, `--human`, `--dry-run`, and `--revision`; diagnostic commands
also accept `--binding` for a project-selected kubeconfig source. Use native
filters such as `--namespace`, `--node`, or `--last` where the command declares
them.

For a GitLab operation, select the exact project or group in the target file or
with `--project`/`--group`. Run the action without `--apply` to get a
machine-readable dry-run plan and its `plan_digest`. Only an explicitly
requested write uses `--apply --confirm-plan DIGEST`; the command reads the
resource before changing it and verifies it afterward. `--token-out PATH` is
required for both the plan and apply when creating a runner record because
GitLab returns its token once and the destination is bound into the plan;
the token never appears in a report. Runner registration and online readiness
are separate from creating its record.

For a group runner assignment, the offline plan has no project list and cannot
authorize apply. Run `gitlab_runner.py assign --group GROUP --runner-id ID
--live-plan` to read the exact project IDs and obtain an apply-ready digest.
Apply re-reads that set and blocks if it changed before any assignment.

`--target` resolves in four declared places — the argument, then
`$CI_SKILLS_TARGET`, then `./.ci-skills/target.toml`, then
`~/.ci-skills/target.toml` — and live reports and GitLab operation plans name
the selected source as `target_source`. One cluster means setting the last
one once; many clusters mean the environment variable or a per-project file.
The common case takes no arguments at all. Output needs no flag either: a
terminal gets the human summary, a pipe or file gets versioned JSON.

`--binding PATH` or `K8S_ADMIN_DIAGNOSTICS_BINDING` names a project target and
ordered file, environment, or command sources for its kubeconfig. The receipt
records the selected source; see the binding protocol before using it.

Prefer a native filter over a shell pipeline — `--namespace`, `--node`,
`--reason`, `--search`, `--last` — because a pipeline discards the identity and
timing you will need to correlate. `--last 15m` beats computing an RFC3339
pair; pass the job's own interval when correlating a job.

## 4. Read the status honestly

- `PASS` — every selected authority and live check passed.
- `PARTIAL` with `access_proven: true` — the read worked and a component is
  unhealthy. `records[].findings` names explicit Cilium daemon, peer, or
  endpoint failures with an inspection action. Say which component.
- `BLOCKED` — see `blocking_live_checks` and the surface `reason`.
- `DRY_RUN` — a plan, never proof of NIC MTUs. The MTU planner reads the
  authenticated node inventory; other collectors do not contact their APIs.
- `PLANNED` — a live, read-only group assignment plan with bound project IDs.
- `UNKNOWN` — a per-item reading could not be taken. Preserve it; do not
  coerce it to a failure or a pass.
- For GitLab writes, `PASS` means an applied change or verified no-op with
  independent read-back. A returned API ID alone is not acceptance evidence.
- An event read returning zero records with zero errors means the events aged
  out of the cluster, not that the read failed. Say "unverified, evidence
  expired".

Exit 0 is `PASS`, `DRY_RUN`, or `PLANNED`; exit 2 is `BLOCKED` or `PARTIAL`.

## 5. Correlate, then state your confidence

Correlate by job time, Pod UID, node, claim and event reason. Mark each
conclusion **confirmed** by direct read-back, **correlated** by matching time
and identity, or **unverified** when evidence is missing or expired.

Every API report carries the gate that authorized it: `access.profile`, the
identities, the credential sources, the execution host, and `skill.digest`.
That digest is identical across every report from one installed copy, and each
carries a `receipt_sha256` tying it to its gate — cross-check and cite them,
and several reports become one audit trail.

`skill.revision.verified` is `false` for an installed copy, because a commit
SHA cannot be verified where it is claimed. Treat it as a claim; the digest is
the provenance.

## 6. Persisting evidence

Nothing is written unless you ask. `--output-dir PATH` writes paired JSON and
text. For an artifact that will be kept or committed, use `access_check.py
--receipt-out PATH` instead: only that form digests absolute host paths, and
the captured form names credential locations under someone's home directory.
Review any artifact before sharing — event messages and job traces can carry
sensitive text.

## 7. Read evidence on a selected node

Declare `[kubernetes.node_diagnostics]` and an existing Pod route in the
selected `.ci-skills/target.toml` (see the template). Run
`scripts/cilium_node.py --json` to execute `cilium-dbg` and `cilium-health`
inside that agent Pod without a TTY. Run `scripts/ceph_kernel.py --json` to
classify recent host journal messages through a selected existing Pod whose
host journal mount is read back first. Both accept `--target` or `--binding`
and use the same pinned kubeconfig/context/server and Kubernetes access gate
as the cluster collectors;
neither creates a Pod or repairs a node. Both accept `--search TEXT`;
`ceph_kernel.py` also accepts `--classification NAME`.

## 8. Verify physical uplink MTUs

On OpenShift, for Ceph connectivity, RBD timeout, or cross-node network
symptoms, check physical MTU consistency before searching node interfaces by
hand. This command requires `oc` as well as `kubectl`, and blocks if the
selected API is not OpenShift. Run
`scripts/k8s_verify_mtu_consistency.py --json` in the selected project. It
resolves the same exact Kubernetes target, verifies access, reads the node
inventory, and returns `plan_digest` without creating a Pod. Then run
`scripts/k8s_verify_mtu_consistency.py --apply --confirm-plan SHA256 --json`
using that digest. `--node NAME` restricts both calls to one existing node.

The apply uses `oc debug node/NAME` to read `ip -d -j addr show` through
`chroot /host`. It selects PCI Ethernet interfaces with an IPv4 address,
compares their MTUs, and emits one versioned JSON report or a human table.
The command creates temporary debug Pods in the selected context's namespace,
tags them for this run, deletes any survivors, and reads back their absence.
An incomplete read or cleanup is `BLOCKED` or `PARTIAL`; a real MTU mismatch is
`PARTIAL` with `physical_mtu_mismatch` and an inspection action. It does not
change host interfaces or repair networking.
