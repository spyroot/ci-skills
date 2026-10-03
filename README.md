# CI Skills: GitLab and Kubernetes operations

## Scope

The installable Codex skill is `ci-skills`. Its
`skills/k8s-admin-diagnostics/` directory is the diagnostic command provider.
The same skill also includes `bin/ci-api` for bounded Git API reads and
`bin/ci-binary-build` for exact-commit OpenShift build planning. Separate
commands read GitLab jobs and pipelines,
storage and events, Cilium, Ceph, and physical NIC MTU consistency. GitLab
milestone, bug, wiki, and runner commands support planned writes with
independent read-back. Each command checks its selected authority;
`access_check.py` proves GitHub, GitLab, and Kubernetes together. Cilium and
Ceph node diagnostics use non-TTY `kubectl exec` into existing Pods; the
OpenShift MTU command creates temporary debug Pods only with `--apply` and a
matching plan digest.

Output follows the reader: a terminal gets a human summary, a pipe or a file
gets versioned JSON. A program calling these commands therefore needs no
`--json` flag, though it may pass one.

## Install and first run

Five steps. The third sets a default target, so later commands need no target
argument.

**1. Install the tools.** Python 3.11 or newer with PyYAML, plus `gh`, `glab`
and `kubectl` for diagnostics. GitLab-only operations need `glab` but do not
need `gh` or `kubectl`.
Create the `ci-skills` conda environment if it is absent, then install the
repository dependencies into it:

```bash
conda create -n ci-skills python=3.11
conda run -n ci-skills python -m pip install -r requirements.txt
```

**2. Authenticate, as yourself.** The skill ships no credentials and grants no
access. Provide the identity needed by the commands you run: a Kubernetes
administrator for cluster diagnostics, a GitLab instance administrator for
GitLab diagnostics, and a GitHub identity for the full access receipt.
`gh auth login`, `glab auth login`, and your cluster's existing login mechanism
are supported. A declared token file takes precedence over ambient token
variables; the report names the effective source.

**3. Create your target file.** This is the step that makes everything else
argument-free:

```bash
mkdir -p ~/.ci-skills
cp target.toml.template ~/.ci-skills/target.toml
$EDITOR ~/.ci-skills/target.toml
```

Fill in the sections needed by your commands. GitLab-only commands can use
`[gitlab]` alone, while the full `access_check.py` receipt needs all three
sections. Kubernetes commands need the selected context and exact API server.
Declare `kubernetes.kubeconfigs` when context and credential span files;
otherwise the resolver uses a declared `kubeconfig`, `KUBECONFIG`, or
`~/.kube/config` in that order, then verifies the context and server. For the
`--publication` check in step 4, declare the repository's actual
`github.required_checks` from branch protection.

Keep that file out of this repository, and never point anything that runs
elsewhere at it. The paths inside are true on one machine only; a pipeline
aimed at a path under someone's home directory fails the moment it runs on a
runner, and the failure reads like a credential problem rather than the wiring
mistake it is. A consuming project can provide its own target file or the
documented `--binding PATH` protocol for an existing kubeconfig resolver; see
[project-binding.md](skills/k8s-admin-diagnostics/references/project-binding.md).
For node diagnostics, declare the selected node and existing Pod routes under
`[kubernetes.node_diagnostics]`. Provisioning access is the project's job;
this skill resolves the selected source and reports it.

**4. Prove access, before trusting anything else.**

```bash
conda run -n ci-skills python \
  skills/k8s-admin-diagnostics/scripts/access_check.py --publication
```

`PASS` means every selected authority was reached and each required live read
succeeded, and the receipt names the effective source and identity. That
is the answer to "which credential am I using" — read it rather than searching
the host.

**5. Install the `ci-skills` agent entry point.** Steps 1 to 4 make the
commands work in a shell. From a clean committed checkout, inspect the
installer plan, set `FINGERPRINT` to its printed `fingerprint`, then install
the top-level skill:

```bash
./install.sh --dry-run
./install.sh --apply --confirm-install "$FINGERPRINT" --timeout 10s
```

`install.sh` links this checkout at `$CODEX_HOME/skills/ci-skills`, or
`~/.codex/skills/ci-skills` when `CODEX_HOME` is unset. The source checkout
must remain available because the link follows it. Its
[SKILL.md](SKILL.md) routes agent requests to the commands below. For a fresh
Codex installation, the skill can also be installed directly from the
repository root on GitHub after this change merges.

Existing users can install the diagnostic provider alone with its separate
installer:

```bash
conda run -n ci-skills python tools/install_k8s_admin_diagnostics.py --json
conda run -n ci-skills python tools/install_k8s_admin_diagnostics.py \
  --apply --confirm-install --json
conda run -n ci-skills python tools/install_k8s_admin_diagnostics.py \
  --skills-dir ~/.claude/skills --apply --confirm-install --json
conda run -n ci-skills python tools/install_k8s_admin_diagnostics.py \
  --upgrade --apply --confirm-upgrade --json
conda run -n ci-skills python tools/install_k8s_admin_diagnostics.py \
  --recover --json
conda run -n ci-skills python tools/install_k8s_admin_diagnostics.py \
  --recover --apply --confirm-recover --json
```

That provider's default destination is
`$CODEX_HOME/skills/k8s-admin-diagnostics`, or
`~/.codex/skills/k8s-admin-diagnostics` when `CODEX_HOME` is unset; pass
`--skills-dir PATH` for any other runtime. Existing installs require
`--upgrade --apply --confirm-upgrade`; each previous version is preserved as a
hidden sibling. If an install is interrupted, inspect the `--recover` dry-run
result, then apply that recovery before retrying. Install, upgrade, and recovery
default to dry-run.
The installer requires a clean checkout of the skill subtree so the revision it
reports is verified against the source bytes, and reports the installed digest
— which is the value to compare against the `skill.digest` in any later report.
It also accepts `--yaml` and `--help`.

A Codex session can install the merged skill straight from GitHub instead:

```text
Install the skill from https://github.com/spyroot/ci-skills/tree/main
```

Either way, each execution host still needs its own credentials and its own
target file — installation grants nothing.

## Target selection

You supply **one nonsecret file** naming the authorities each command will use.
Copy `target.toml.template`, fill it in, and put it in one of these places. The
first one found wins, and every command reports which it used as
`target_source`. The layout is the one agent tooling already uses — a project
`./.ci-skills/` beside a user `~/.ci-skills/`, the same shape as `.claude` and
`.codex`:

| # | Scope | Where | Set by |
| --- | --- | --- | --- |
| 1 | one command | `--target PATH` | the caller |
| 2 | one environment | `$CI_SKILLS_TARGET` | a shell, a CI job, an agent |
| 3 | one project | `./.ci-skills/target.toml` | per repository |
| 4 | one user | `~/.ci-skills/target.toml` | you |

Pick the tier that matches how many targets you have.

**One GitLab and one cluster** — put the file at tier 4 once and never pass an
argument again. Same shape as a single API key living in a user config rather
than in every repository.

**Many clusters** — tier 4 stops being useful, and tiers 2 and 3 are the
answer. Keep one target per cluster wherever you like and select it with
`$CI_SKILLS_TARGET`, or put a `.ci-skills/target.toml` in each repository that
owns a cluster so anyone working there is aimed correctly by default. A CI job sets
the variable; an agent can set it per task. Tier 1 stays for one-off runs
against something unusual.

Credentials follow the same shape: declared first, environment next, the
client's own credential store last. The full chain per authority is in
`skills/k8s-admin-diagnostics/tools.json` under `access_protocol`, and the
resolved source for each is reported in `credential_sources`.

**This skill resolves; it does not provision.** It will not create a target
file, mint a token, or fetch a kubeconfig. Somebody has to put them there —
you, or an explicit step in the calling project's own instructions. If nothing
is found, the error names every path it searched and the template to copy.

## GitLab operations

For GitLab-only work, the selected target file may contain only `[gitlab]` with
its exact `url`. Set `gitlab.project` or `gitlab.group` for the intended
operation. `gitlab_job.py` selects its project from `--job-url`. The same
four-tier target selection above applies. Storage, event, and Cilium
diagnostics still need all three authorities. Set `gitlab.project` in the
selected target for the commands below. The declared `--project` flag overrides
it for one invocation.

The operation access check reads back the effective GitLab identity and exact
numeric target before a write:

```bash
conda run -n ci-skills python \
  skills/k8s-admin-diagnostics/scripts/gitlab_access.py check \
  --json
conda run -n ci-skills python \
  skills/k8s-admin-diagnostics/scripts/gitlab_milestone.py create \
  --title "Release checkpoint" --json
```

The second command prints a `DRY_RUN` plan with `plan_digest` and makes no API
call. When the requested change is authorized, pass that digest with
`--apply --confirm-plan DIGEST`; the command rechecks the selected credential,
identity, and target, reads the resource before changing it, and independently
reads it back. The same pattern applies to `gitlab_issue.py open-bug`,
`gitlab_wiki.py create|update`, and `gitlab_runner.py assign|create`. Runner
creation requires the same `--token-out PATH` on the dry-run plan and apply to
bind the one-time token destination into the plan digest. The token is saved at
that caller-selected path; creating a record does not register or start a runner.

If a create response is lost, the next apply reads the complete exact-title or
description list. When no record exists, that call clears the pending marker
and blocks without posting; repeat the same confirmed plan to create it. If a
runner with the description exists, inspect its numeric ID and scope in GitLab
before deciding whether to remove that record through the GitLab UI. A lost
one-time token cannot be recovered from the record. After an authorized removal,
repeat the confirmed plan once to clear the marker and again to create. Do not
remove a runner merely because its description matches.

Group runner assignment has one extra read-only step: select `gitlab.group` in
the target, then call `gitlab_runner.py assign --runner-id ID --live-plan --json`
to read the exact project IDs. Its `PLANNED` result supplies the digest for
`--apply --confirm-plan DIGEST`. Apply rechecks group membership and blocks
before a write if it changed. The default offline `DRY_RUN` plan makes no API
call and is not apply-ready for a group.

Run `<command> --describe` for the machine contract and `--help` for examples.
The generated `tools.json` marks each command read-only or mutating, lists its
subcommands and options, and routes an agent to the correct first access call.

## For agents

Read the top-level [SKILL.md](SKILL.md) to choose a command. The diagnostic
provider's [SKILL.md](skills/k8s-admin-diagnostics/SKILL.md) explains target
resolution, status interpretation, and evidence handling. Its
[tools.json](skills/k8s-admin-diagnostics/tools.json) is the machine-readable
command contract.

## Commands

Run `<command> --help` or `--describe` for shared options; only what is
specific to each command is listed here. Pass a full source commit SHA with
`--revision` when the installed copy has no Git metadata, and `--output-dir
PATH` to write paired JSON and text files; without that option no report file
is written.

- `access_check.py` checks the three selected authorities and runs the
  declared live collector reads. `--publication` also requires
  repository admin permission and read-back of required branch checks.
  `--job-url URL` also checks that job, pipeline, runner, and trace.
  `--ceph-namespace NAME` adds Ceph health, OSD, PG, and Pod reads to that
  receipt; this repository requires it in `acceptance/expected.toml`.
- `gitlab_job.py --job-url URL` reads a selected job, pipeline, runner, and
  bounded trace using GitLab-only access. It accepts `--search TEXT`.
- `gitlab_pipeline.py --pipeline-id ID` reads pipeline progress and bounded
  job results for the selected GitLab project.
- `storage_report.py` correlates PVCs, standalone PVs, Pods, attachments,
  and controllers. Filters: `--namespace NAME|all`, `--node NAME`,
  `--storage-class NAME`, `--phase Pending|Bound|Lost|Released|Failed|all`,
  and `--search TEXT`.
- `event_trace.py` reads both Kubernetes event APIs and accepts `--last`
  (`5m`, `90s`, `2h`, `7d`), `--from`, `--to`, `--namespace`, `--kind`,
  `--object`, `--reason`, and `--search`. `--last 15m` is shorter than
  computing an RFC3339 pair; the default window is the previous hour, which is
  the trap when correlating a job that failed earlier.
- `cilium_status.py` reads Cilium resources and executes non-TTY health on
  ready agents. It accepts `--namespace NAME|auto`, `--node`, and `--search`.
  Peer and endpoint findings stay in the report even when the read succeeds.
- `gitlab_access.py check` reads the GitLab identity and selected project or
  group without requiring GitHub or Kubernetes access.
- `gitlab_milestone.py create|update|adjust-time` manages exact milestone IDs
  and dates; `gitlab_issue.py open-bug|create-bug` creates or reuses a bug.
- `gitlab_wiki.py create|update` manages one selected page;
  `gitlab_runner.py assign|create` manages a selected runner scope. These
  commands default to dry-run and require a matching plan fingerprint to apply.
- `ceph_cluster.py --namespace NAME` reads Ceph health, root/rack/host/OSD
  hierarchy, inactive PGs, and OSD/monitor Pods on the pinned cluster.
  It accepts `--operator`, `--node`, `--ready`, and `--condition` filters.
- `k8s_verify_mtu_consistency.py --json` reads the selected node inventory and
  prints a plan digest. To collect PCI Ethernet interface MTUs, run
  `k8s_verify_mtu_consistency.py --apply --confirm-plan SHA256 --json` with
  that digest. This OpenShift command requires `oc` and `kubectl` and blocks
  on a non-OpenShift API. `--node NAME` scopes both calls. The apply uses
  `oc debug`, creates temporary Pods, and verifies cleanup; its JSON and human
  table show each node, interface, PCI device, optional IPv4 address, and MTU. A
  mismatch is `PARTIAL` with a finding, and no host interface is changed.

The Cilium and Ceph node commands run from the skill's execution host. They
use the same target and Kubernetes access gate as the cluster collectors,
then select exactly one Running Pod on the declared node. They never create a
Pod:

- `cilium_node.py --json` executes `cilium-dbg status --verbose --output json`
  and `cilium-health status --verbose --output json` concurrently inside the
  selected existing agent Pod. It reports bounded summaries and explicit
  daemon or peer failures in `findings`; a degraded agent remains a successful
  read with `PASS` collection status when both commands return valid evidence.
  Use `--search TEXT` to narrow its returned records.
- `ceph_kernel.py --json` runs `journalctl -k` inside the selected existing
  Pod only after reading back its configured host journal mount. It reads the
  previous three minutes of entries matching `libceph|rbd|ceph`. Each record
  has a UTC timestamp,
  priority, classification, and machine-readable recommended action. It
  performs no recovery action. The window is the previous three minutes, with
  bounded output; a limit hit is reported as `PARTIAL`. Use `--search TEXT`
  and `--classification NAME` to narrow returned records without changing the
  underlying read status.

The kernel classifier emits action codes for observed blocklisting, auth
failure, connectivity timeout, and I/O errors. Other priority 0–3 entries
receive `review_ceph_kernel_event`; informational entries have no action.
The action code is a prompt for investigation, not a claimed root cause.

Both accept `--target` or `--binding`, `--yaml`, `--dry-run`, and `--help`.
Exit code 0 means the node read succeeded or a dry run was requested; code 2
means incomplete evidence.
An absent, ambiguous, or wrong-node Pod blocks; `ceph_kernel.py` also blocks
when the declared host journal directory is not mounted in its container.

Each collector checks its declared authority before collecting. The full
three-authority receipt runs in `access_check.py`. A collector report names
its gate in `access.profile`; the receipt names it in top-level `profile`.
Exit code 0 means `PASS` or a marked `DRY_RUN`; code 2 means `BLOCKED` or
`PARTIAL`. JSON and YAML failures emit a structured report on stdout.

A collector that could not read blocks the gate. One that read successfully
while reporting an unhealthy component does not, as long as the capability it
proves was demonstrated at least once. Each live check reports
`access_proven` next to its own `status`.

## Validation

The `validate` workflow checks workflow/YAML and Markdown syntax, diff
hygiene, secrets, Ruff lint and format, package behavior, and mocked denial
paths. Its package smoke runs every installed entrypoint from outside the
source tree. Mocked CI is code evidence; the live access receipt must come
from each intended execution host. Use the exact target and tested revision
there, and retain the sanitized receipt only after all required checks pass.
