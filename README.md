# Kubernetes diagnostics and GitLab operations skill

## Scope

This repository provides `k8s-admin-diagnostics`. Its storage, event, and
Cilium diagnostics check the selected GitHub, GitLab, and Kubernetes authorities.
The GitLab job diagnostic checks the selected GitLab host, identity, and job
project before reading the job, pipeline, runner, and trace. The skill also
has separately routed GitLab milestone, bug, wiki, and runner operations. Those
commands default to an offline dry-run and require an explicit apply plus the
dry-run plan fingerprint before a write.

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
access. It assumes you already hold what it will use — in practice cluster
administrator, a GitLab instance administrator identity, and a GitHub identity
that can read the repository. `gh auth login`, `glab auth login`, and whatever
your cluster uses.

**3. Create your target file.** This is the step that makes everything else
argument-free:

```bash
mkdir -p ~/.ci-skills
cp target.toml.template ~/.ci-skills/target.toml
$EDITOR ~/.ci-skills/target.toml
```

Fill in the exact GitHub repository, the exact GitLab origin, and the
Kubernetes context with the API server it must resolve to. Declare
`kubernetes.kubeconfigs` while you are there — see the template for why.
For the `--publication` check in step 4, declare the repository's actual
`github.required_checks` from branch protection.

Keep that file out of this repository, and never point anything that runs
elsewhere at it. The paths inside are true on one machine only; a pipeline
aimed at a path under someone's home directory fails the moment it runs on a
runner, and the failure reads like a credential problem rather than the wiring
mistake it is. A consuming project provides its own target, or sets
`KUBECONFIG` itself — typically as an explicit first step in that project's own
agent instructions. Provisioning access is the project's job; this skill only
resolves what is already there and reports which source it used.

**4. Prove access, before trusting anything else.**

```bash
conda run -n ci-skills python \
  skills/k8s-admin-diagnostics/scripts/access_check.py --publication
```

`PASS` means every authority was reached and every live read succeeded, and the
receipt names the effective credential source and identity for each one. That
is the answer to "which credential am I using" — read it rather than searching
the host.

**5. Install it where your agent looks.** Steps 1 to 4 make the commands work
in a shell. An agent finds the skill only once it is installed into that
runtime's skills directory:

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

The default destination is `$CODEX_HOME/skills/k8s-admin-diagnostics`, or
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
Install the skill from https://github.com/spyroot/ci-skills/tree/main/skills/k8s-admin-diagnostics
```

Either way, each execution host still needs its own credentials and its own
target file — installation grants nothing.

## Target selection

You supply **one nonsecret file** naming the authorities to use. Copy
`target.toml.template`, fill it in, and put it in one of these places. The
first one found wins. Live reports and GitLab operation plans name that source
as `target_source`. The layout is the one agent tooling already uses — a project
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

Read the installed [SKILL.md](skills/k8s-admin-diagnostics/SKILL.md) for the
first call, command routing, status interpretation, and evidence handling.
Its [tools.json](skills/k8s-admin-diagnostics/tools.json) is the machine-readable
command contract.

## Commands

Run `<command> --help` or `--describe` for shared options; only what is
specific to each command is listed here. Pass a full source commit SHA with
`--revision` when the installed copy has no Git metadata, and `--output-dir
PATH` to write paired JSON and text files; without that option no report file
is written.

- `access_check.py` checks the three selected authorities and runs the
  storage, event, and Cilium collector reads. `--publication` also requires
  repository admin permission and read-back of required branch checks.
  `--job-url URL` also checks that job, pipeline, runner, and trace.
- `gitlab_job.py --job-url URL` reads a selected job, pipeline, runner, and
  bounded trace using GitLab-only access. It accepts `--search TEXT`.
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
- `gitlab_access.py check` reads the GitLab identity and selected project or
  group without requiring GitHub or Kubernetes access.
- `gitlab_milestone.py create|update|adjust-time` manages exact milestone IDs
  and dates; `gitlab_issue.py open-bug|create-bug` creates or reuses a bug.
- `gitlab_wiki.py create|update` manages one selected page;
  `gitlab_runner.py assign|create` manages a selected runner scope. These
  commands default to dry-run and require a matching plan fingerprint to apply.

## Validation

The `validate` workflow checks workflow/YAML and Markdown syntax, diff
hygiene, secrets, Ruff lint and format, package behavior, and mocked denial
paths. Its package smoke runs every installed entrypoint from outside the
source tree. Mocked CI is code evidence; the live access receipt must come
from each intended execution host. Use the exact target and tested revision
there, and retain the sanitized receipt only after all required checks pass.
