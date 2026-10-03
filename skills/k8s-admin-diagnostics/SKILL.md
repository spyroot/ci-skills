---
name: k8s-admin-diagnostics
description: Collect read-only GitLab CI, Kubernetes, Cilium, and Ceph evidence through verified credentials and selected targets.
metadata:
  manifest: tools.json
  first_call: scripts/access_check.py
  first_call_scope: api_collectors
  default_output: json when stdout is not a terminal
  read_only: true
---

# Kubernetes admin diagnostics

Diagnose a Kubernetes-backed CI, storage or network symptom. Every command is
read-only: none logs in, changes context, grants a role, or mutates anything.

## 1. First call for API diagnostics: access check

Run this before any API collector:

    scripts/access_check.py --publication

Do not go looking for credentials. This one call resolves them and tells you
what it used:

- `PASS` — `credential_sources` names the effective source per authority and
  `surfaces.<name>.identity` the identity read back. You now know what you are
  authenticated as. Stop searching; proceed.
- `BLOCKED` — `surfaces.<name>.reason` names what failed and `next_step` what
  to do. Report that; do not try other credentials.

Access is resolved from declared locations, first match wins, and the match is
reported. The chain per authority is in `tools.json` under `access_protocol`,
and [references/access.md](references/access.md) is the full contract.
The installed `gh` environment contract uses `GH_TOKEN`/`GITHUB_TOKEN` for
`github.com` and `*.ghe.com`, and enterprise token variables for other GitHub
Enterprise Server hosts. A selected token file clears ambient GitHub tokens.
The one trap worth knowing: with no Kubernetes location declared, resolution
ends at `~/.kube/config`, which is usually a *different* cluster — so a target
that declares `kubernetes.kubeconfigs` is how you avoid aiming elsewhere.

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
| proof of access, before trusting anything | `access_check.py` |
| a named CI job's own facts | `gitlab_job.py --job-url URL` |
| why a volume or claim is stuck | `storage_report.py` |
| what the cluster said during an interval | `event_trace.py --last 15m` |
| connectivity, or CNI health per node | `cilium_status.py` |
| Cilium daemon and health on a selected node | `cilium_node.py` |
| Ceph or RBD kernel messages on that node | `ceph_kernel.py` |

## 3. One interface, not five

Every API command accepts `--target`, `--json`, `--yaml`, `--human`, `--dry-run`,
`--revision`, `--output-dir` and `--describe`. A command that filters records
accepts `--search`; one scoped to a namespace accepts `--namespace`; one
reading a time range accepts `--last`, `--from` and `--to`. Learn the tier
once and it holds everywhere.

`--target` resolves in four declared places — the argument, then
`$CI_SKILLS_TARGET`, then `./.ci-skills/target.toml`, then
`~/.ci-skills/target.toml` — and every report says which it used as
`target_source`. One cluster means setting the last one once; many clusters
mean the environment variable or a per-project file. The common case takes no
arguments at all. Output needs no flag either: a terminal gets the human
summary, a pipe or file gets versioned JSON.

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
- `DRY_RUN` — a probe plan. Never access evidence.
- `UNKNOWN` — a per-item reading could not be taken. Preserve it; do not
  coerce it to a failure or a pass.
- An event read returning zero records with zero errors means the events aged
  out of the cluster, not that the read failed. Say "unverified, evidence
  expired".

Exit 0 is `PASS` or `DRY_RUN`; exit 2 is `BLOCKED` or `PARTIAL`.

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
host journal mount is read back first. Both commands use the same pinned
kubeconfig/context/server and three-surface access gate as the API commands;
neither creates a Pod or repairs a node. Both accept `--search TEXT`;
`ceph_kernel.py` also accepts `--classification NAME`.
