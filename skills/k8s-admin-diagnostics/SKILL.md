---
name: k8s-admin-diagnostics
description: Collect GitLab CI and Kubernetes diagnostics, or perform explicitly confirmed GitLab milestone, bug, wiki, and runner operations against a selected target. Reports the effective credential source and verified identity.
metadata:
  manifest: tools.json
  first_call: scripts/access_check.py
  first_call_by_route:
    diagnostics: scripts/access_check.py
    gitlab_operations: scripts/gitlab_access.py check
  default_output: json when stdout is not a terminal
  read_only: false
---

# Kubernetes diagnostics and GitLab operations

Diagnose a Kubernetes-backed CI, storage, or network symptom, or act on an
explicit GitLab request. Diagnostic commands are read-only. GitLab operation
commands default to an offline dry-run and write only with `--apply` and the
printed plan fingerprint.

## 1. Choose the access route

For diagnostics, run the full access check first:

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
The one trap worth knowing: with no Kubernetes location declared, resolution
ends at `~/.kube/config`, which is usually a *different* cluster — so a target
that declares `kubernetes.kubeconfigs` is how you avoid aiming elsewhere.

Provisioning access is not this skill's job. If a kubeconfig has to be fetched
or minted first, that belongs to the calling project's own instructions.

For a GitLab milestone, bug, wiki, or runner request, start with
`scripts/gitlab_access.py check`. It resolves only the selected GitLab target
and effective credential source, then reads back the GitLab identity and
numeric project or group ID. A GitLab-only target file does not select a
Kubernetes context or GitHub repository. Every apply repeats this check with
the same selected source and target; a saved login alone is not proof.

## 2. Read the manifest

`tools.json` beside this file is the machine-readable contract: every command,
its purpose, when to use it, the authorities it needs, its options, and a
symptom-to-command routing table. `<command> --describe` prints one command's
contract as JSON and needs no credentials.

Routing, in short:

| You need | Command |
| --- | --- |
| proof of access, before trusting anything | `access_check.py` |
| a named CI job's own facts | `gitlab_job.py --job-url URL` |
| why a volume or claim is stuck | `storage_report.py` |
| what the cluster said during an interval | `event_trace.py --last 15m` |
| connectivity, or CNI health per node | `cilium_status.py` |
| read back GitLab target and identity | `gitlab_access.py check` |
| create, update, or adjust milestone dates | `gitlab_milestone.py` |
| open a bug issue | `gitlab_issue.py open-bug` |
| create or update a wiki page | `gitlab_wiki.py` |
| assign or create a runner record | `gitlab_runner.py` |

## 3. One target and output interface

Every command accepts `--target`, `--json`, `--yaml`, `--human`, `--dry-run`,
`--revision`, `--output-dir`, `--describe`, `--log-format`, `--log-level`,
`--log-file`, and `--run-id`. A command that filters records
accepts `--search`; one scoped to a namespace accepts `--namespace`; one
reading a time range accepts `--last`, `--from` and `--to`. Learn the tier
once and it holds everywhere.

For a GitLab operation, select the exact project or group in the target file or
with `--project`/`--group`. Run the action without `--apply` to get a
machine-readable dry-run plan and its `plan_digest`. Only an explicitly
requested write uses `--apply --confirm-plan DIGEST`; the command reads the
resource before changing it and verifies it afterward. `--token-out PATH` is
required when creating a runner record because GitLab returns its token once;
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

Prefer a native filter over a shell pipeline — `--namespace`, `--node`,
`--reason`, `--search`, `--last` — because a pipeline discards the identity and
timing you will need to correlate. `--last 15m` beats computing an RFC3339
pair; pass the job's own interval when correlating a job.

## 4. Read the status honestly

- `PASS` — every selected authority and live check passed.
- `PARTIAL` with `access_proven: true` — the read worked and a component is
  unhealthy. That is usually the finding, not an obstacle. Say which component.
- `BLOCKED` — see `blocking_live_checks` and the surface `reason`.
- `DRY_RUN` — a probe plan. Never access evidence.
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

Every report carries the gate that authorized it: `access.profile`, the
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
