# CI Skills: GitLab and Kubernetes operations

CI Skills provides project-neutral commands for GitLab, GitHub, and
Kubernetes/OpenShift. Each command selects its target from caller input or the
configured binding and returns evidence that an operator or agent can inspect.

## Current capabilities

- [Check GitLab access](ci-skills/bin/gitlab_access.py): identify
  the effective credential source and read back the selected project or group.
- [Inspect pipelines](ci-skills/bin/gitlab_pipeline.py) and
  [jobs](ci-skills/bin/gitlab_job.py): read status, stage progress,
  runner details, and bounded traces; search within a job trace.
- [Manage milestones](ci-skills/bin/gitlab_milestone.py): create or
  update a milestone's title, description, dates, and state.
- [Open bug issues](ci-skills/bin/gitlab_issue.py): create or reuse
  an issue with caller-supplied labels and an optional milestone.
- [Write wiki pages](ci-skills/bin/gitlab_wiki.py): create or update
  a page from supplied content, including documentation links.
- [Manage runners](ci-skills/bin/gitlab_runner.py): assign an
  existing runner or create a runner record with runner tags.
- [Diagnose Kubernetes and OpenShift](ci-skills/bin/): inspect
  storage, events, Cilium, Ceph, and node MTU consistency.
- Use [ci-api](ci-skills/bin/ci-api) for bounded Git API reads and
  [ci-binary-build](ci-skills/bin/ci-binary-build) for exact-commit
  OpenShift build planning.

The Python commands use small entry points over reusable code in
[lib/core](ci-skills/lib/core/). Their `--help` output serves
people, while `--json`, `--yaml`, and `--describe` expose versioned reports and
command contracts. The generated [tools.json](ci-skills/tools.json)
records options, access protocols, and target protocols. GitLab writes start
with a dry-run plan and require a confirmed plan digest to apply.

Cilium and Ceph node diagnostics use non-TTY `kubectl exec` in existing Pods.
The OpenShift MTU command creates temporary debug Pods only with `--apply` and
its matching plan digest.

## Tool grouping

Four patterns describe what a tool does. They can overlap: a combined report
may also provide visibility.

- **Visibility:** Collects evidence and answers a status question in one
  report. A sanity check could compare MTU values across Kubernetes nodes; a
  project overview could summarize milestones, issues, merge requests, failed
  jobs, and failed pipelines.
- **CI Combo:** Focuses on agentic CI behavior. One defined action combines
  the `glab`, `glab api`, `gh`, or GitHub Actions steps an agent would otherwise
  invoke separately. For example, it could locate a pipeline, inspect its
  jobs, search relevant output, and return one structured result.
- **Generic Combo:** Assembles a view of a complex object from related
  components. A Ceph report in OpenShift could combine cluster health,
  storage components, workloads, and events.
- **Toolchain Combination:** Follows a prescribed sequence to create or
  configure a complex object for a selected scenario. Examples include
  building an OpenShift image with native build mechanics and publishing it
  to a linked Harbor registry, or creating and attaching a CI runner for a
  project and target cluster.

### Current mapping

- **Visibility:** [Access checks](ci-skills/bin/access_check.py),
  [Kubernetes events](ci-skills/bin/event_trace.py), and
  [node MTU consistency](ci-skills/bin/k8s_verify_mtu_consistency.py).
- **CI Combo:** [Pipeline inspection](ci-skills/bin/gitlab_pipeline.py)
  and [job and trace inspection](ci-skills/bin/gitlab_job.py)
  combine related CI records. [ci-api](ci-skills/bin/ci-api) supplies
  bounded API reads for such workflows.
- **Generic Combo:** [Ceph cluster diagnostics](ci-skills/bin/ceph_cluster.py)
  combines cluster health, OSD hierarchy, placement groups, and Pods;
  [Ceph kernel diagnostics](ci-skills/bin/ceph_kernel.py) adds
  selected node events.
- **Toolchain Combination:** [ci-binary-build](ci-skills/bin/ci-binary-build)
  plans an exact-commit OpenShift build, and
  [gitlab_runner.py](ci-skills/bin/gitlab_runner.py) plans and
  applies runner creation or assignment. The complete example workflows
  above remain proposed.

## Required next delivery

This `CI_SKILL` specification adds GitLab issue, milestone, board, label,
pipeline, job, schedule, and wiki workflows, plus issue-to-merge-request-to-QA
evidence. The commands below are proposed; they are not yet present in the
[command catalog](ci-skills/lib/core/catalog.py).

Every new action needs a concrete script name, arguments, behavior, result,
and independent read-back. Its Python entry point must stay small and call
reusable code in [lib/core](ci-skills/lib/core/). It must offer
`--help` for people, `--json` and `--yaml` for machines, and `--describe` for
its command contract. [tools.json](ci-skills/tools.json) must declare
its options and access and target protocols. Each versioned report kind must
have a paired formal JSON Schema under `schemas/`, declared in the manifest
and checked by validation.
Project, group, board, runner, and label values come from caller input, the
selected binding, or GitLab; reusable code must not hardcode them.

### Proposed GitLab actions

- `ci-skills`:
  `start --project PATH --ref REF`, `retry --project PATH --pipeline-id ID`,
  and `cancel --project PATH --pipeline-id ID`; read back pipeline ID,
  status, ref, SHA, and jobs.
- `ci-skills`:
  `play|retry|cancel --project PATH --job-id ID`; read back the job status
  and linked pipeline.
- `ci-skills`:
  `list|play|create|update --project PATH [--schedule-id ID] [--ref REF]`;
  read back the schedule state and any resulting pipeline.
- `ci-skills`:
  `--project PATH|--group PATH --scope SCOPE --query TEXT`; search the selected
  GitLab scope and report each matching object's type, ID, title, and URL.
- `ci-skills`:
  `--project PATH|--group PATH [--search TEXT] [--state active|closed|all]`;
  report dates, state, linked issue counts, and completion progress.
- `ci-skills`:
  `--project PATH [--search TEXT] [--label LABEL] [--milestone-id ID]`;
  report matching issues, labels, milestones, state, and linked merge requests.
- `ci-skills`:
  `--project PATH --issue-iid IID [--milestone-id ID] [--label LABEL]`
  `[--unlabel LABEL] [--due-date YYYY-MM-DD] [--state opened|closed]`;
  read back the issue's labels, milestone, due date, and state.
- `ci-skills`:
  `list|cards|move --project PATH --board-id ID [--list-id ID]`
  `[--issue-iid IID]`; read boards, lists, and cards, and read back an
  issue's state and labels after a move.
- `ci-skills`:
  `list|create|update --project PATH|--group PATH [--name NAME]`
  `[--color HEX] [--description TEXT]`; manage native GitLab labels using
  structured, extensible naming conventions. Values such as `p0`, `p1`,
  `fix-first`, and `baseline-ready` are examples of data, not constants.
- `ci-skills`:
  `read|record-qa --project PATH --issue-iid IID`
  `[--merge-request-iid IID] [--evidence-url URL]`; show the issue,
  merge-request, and QA evidence relationship using GitLab-native links.
- `ci-skills`:
  `sync --project PATH --slug SLUG --doc-path PATH|--doc-url URL`;
  update a wiki page's repository documentation link and read back its
  slug and content digest.
- `ci-skills`:
  `validate --report PATH --schema KIND`; validate a result against its
  paired formal JSON Schema and report any contract errors.

Pipeline and job actions must support status and output read-back; schedules
need equivalent state read-back after changes.

## Install and first run

Five steps. The third sets a default target, so later commands need no target
argument.

Start from a clean checkout, or enter an existing clean checkout:

```bash
git clone https://github.com/spyroot/ci-skills.git
cd ci-skills
```

**1. Install the tools.** This setup uses `git`, `conda`, and `jq`. Python 3.11
or newer with PyYAML is required for diagnostics; install `gh`, `glab`, and
`kubectl` for the authorities you will access. GitLab-only operations need
`glab` but do not need `gh` or `kubectl`. `ci-binary-build` also needs `yq`.
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

Never commit credential values or credential-bearing kubeconfigs. A project
may keep its target at `./.ci-skills/target.toml`; a pipeline must use paths
valid on its own runner. The paths inside are true on one machine only; a pipeline
aimed at a path under someone's home directory fails the moment it runs on a
runner, and the failure reads like a credential problem rather than the wiring
mistake it is. A consuming project can provide its own target file or the
documented `--binding PATH` protocol for an existing kubeconfig resolver; see
[project-binding.md](ci-skills/references/project-binding.md).
For node diagnostics, declare the selected node and existing Pod routes under
`[kubernetes.node_diagnostics]`. Provisioning access is the project's job;
this skill resolves the selected source and reports it.

Until block 0 of CI10-PHASES lands, prefix every command below with
`PYTHONPATH=ci-skills/lib`: no main under `ci-skills/bin/` can import `core`
without it (the shared locator is block 0's first item).

**4. Prove access to the authorities you selected.** For a GitLab-only target,
read back its configured project and administrator identity:

```bash
PYTHONPATH=ci-skills/lib conda run -n ci-skills python \
  ci-skills/bin/gitlab_access.py check --json
```

When all three authorities are configured, run the full publication check:

```bash
PYTHONPATH=ci-skills/lib conda run -n ci-skills python \
  ci-skills/bin/access_check.py --publication
```

Its `PASS` means every selected authority was reached and each required live
read succeeded, and the receipt names the effective source and identity. That
is the answer to "which credential am I using" — read it rather than searching
the host.

**5. Install the `ci-skills` skill.** Steps 1 to 4 make the commands work in a
shell. Activate the `ci-skills` conda environment on a workstation. From a
clean committed checkout, inspect the copy installer plan, set
`FINGERPRINT` to its printed value, then install the package:

```bash
conda activate ci-skills
./install.sh --dry-run
FINGERPRINT="$(./install.sh --dry-run | jq -r '.fingerprint')"
./install.sh --apply --confirm-install "$FINGERPRINT" --timeout 10s
```

`install.sh` forwards to [the copy installer](tools/install_ci_skills.py).
It installs at `$CODEX_HOME/skills/ci-skills`, or
`~/.codex/skills/ci-skills` when `CODEX_HOME` is unset. The installed copy is
independent of the checkout. The package's
[SKILL.md](ci-skills/SKILL.md) routes agent requests to its tools.
For a different skills directory, use the Python installer:

```bash
conda run -n ci-skills python tools/install_ci_skills.py \
  --skills-dir ~/.claude/skills --dry-run --json
```

To upgrade an existing copy or checkout link, make a new plan and use its
fingerprint:

```bash
FINGERPRINT="$(./install.sh --upgrade --dry-run | jq -r '.fingerprint')"
./install.sh --upgrade --apply --confirm-upgrade "$FINGERPRINT" --timeout 10s
```

Interrupted installations have a separate recovery path:

```bash
conda run -n ci-skills python tools/install_ci_skills.py \
  --recover --json
conda run -n ci-skills python tools/install_ci_skills.py \
  --recover --apply --confirm-recover --timeout 10s --json
```

An upgrade preserves the previous copy or link as a hidden sibling and reads
back the installed digest. Installation, upgrade, and recovery default to
dry-run. The installer requires a clean committed skill subtree; compare its
reported digest with `skill.digest` in later reports. It accepts `--json`,
`--yaml`, and `--help`.

A Codex session can install the merged skill straight from GitHub instead:

```text
Install the skill from https://github.com/spyroot/ci-skills/tree/main/ci-skills
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
`K8S_ADMIN_DIAGNOSTICS_BINDING` also selects a project binding at the
environment tier. Setting it together with `CI_SKILLS_TARGET` is an error;
the resolver will not guess between them.

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
`ci-skills` under `access_protocol`, and the
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
diagnostics need only `[kubernetes]`; the full `access_check.py` needs all
three authorities. Set `gitlab.project` in the
selected target for the commands below. The declared `--project` flag overrides
it for one invocation.

The operation access check reads back the effective GitLab identity and exact
numeric target before a write:

```bash
PYTHONPATH=ci-skills/lib conda run -n ci-skills python \
  ci-skills/bin/gitlab_access.py check \
  --json
PYTHONPATH=ci-skills/lib conda run -n ci-skills python \
  ci-skills/bin/gitlab_milestone.py create \
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

Read the package [SKILL.md](ci-skills/SKILL.md) to choose a command and
understand target resolution, status interpretation, and evidence handling. Its
[tools.json](ci-skills/tools.json) is the machine-readable command contract.

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
  receipt; this repository requires it in `tests/acceptance`.
- `gitlab_job.py --job-url URL` reads a selected job, pipeline, runner, and
  bounded trace using GitLab-only access. It accepts `--search TEXT`.
- `gitlab_pipeline.py --pipeline-id ID` reads pipeline progress and bounded
  job results for the selected GitLab project.
- `storage_report.py` correlates PVCs, standalone PVs, Pods, attachments,
  and controllers. Filters: `--namespace NAME|all`, `--node NAME`,
  `--storage-class NAME`, `--phase Pending|Bound|Lost|Released|Failed|all`,
  and `--search TEXT`.
- `event_trace.py` prefers the `events.k8s.io/v1` API and falls back to core
  Events only when that API is unavailable. It accepts `--last`
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

The `validate` workflow, deleted in #27 (`1108cca`), checked workflow/YAML
and Markdown syntax, diff hygiene, secrets, Ruff lint and format, package
behavior and mocked denial paths, and its package smoke ran every installed
entrypoint from outside the source tree. No workflow exists today: we decided
"no gate for now" (D-GATE, `docs/phases/CI03-GATES.md`, G0). Mocked CI is code evidence; the live access receipt must come
from each intended execution host. Use the exact target and tested revision
there, and retain the sanitized receipt only after all required checks pass.
