# Kubernetes and CI diagnostics skill

## Scope

This repository provides `k8s-admin-diagnostics`, a skill with seven read-only
commands for access checks, GitLab jobs, Kubernetes storage and events,
Cilium status, and node-local Cilium and Ceph kernel diagnostics. The access
check verifies the selected GitHub, GitLab, and Kubernetes authorities before
API collectors run. API commands use `gh`, `glab`, and `kubectl`; node-local
commands use `crictl` or `journalctl` on the selected node.

Output follows the reader: a terminal gets a human summary, a pipe or a file
gets versioned JSON. A program calling these commands therefore needs no
`--json` flag, though it may pass one.

## Why Agent Skills?

Agents are increasingly capable, but often don’t have the context they need to
do real work reliably. Skills solve this by packaging procedural knowledge
This ci-skill gives agents:
Domain expertise: Capture specialized knowledge —  analysis pipelines, k8s state
Repeatable workflows: Turn multi-step tasks into consistent, auditable procedures.
Cross-product reuse: Build a skill once and use it across any
skills-compatible agent.
​
Agents load skills through

Discovery -> Activation -> Reading Machine Readble Specfication -> Execution

## The protocol, in one table

You supply **one nonsecret file** naming the authorities to use. Copy
`target.toml.template`, fill it in, and put it in one of these places. The
first one found wins, and API commands report which it used as
`target_source`. Node-local commands read the local host. The layout is the one
agent tooling already uses — a project
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

## After you clone

Five steps. The whole point of the third is that you do it once and no command
afterwards needs an argument.

**1. Install the tools.** Python 3.11 or newer with PyYAML, plus `gh`, `glab`
and `kubectl` on the host where the commands will run.

```bash
python3 -m pip install -r requirements.txt   # PyYAML, pytest, ruff
```

**2. Authenticate, as yourself.** The skill ships no credentials and grants no
access. It assumes you already hold what it will use — in practice cluster
administrator, a GitLab instance administrator identity, and a GitHub identity
that can read the repository. `gh auth login`, `glab auth login`, and whatever
your cluster uses.

For GitHub, `gh help environment` assigns `GH_TOKEN`/`GITHUB_TOKEN` to
`github.com` and `*.ghe.com`; GitHub Enterprise Server hosts use
`GH_ENTERPRISE_TOKEN`/`GITHUB_ENTERPRISE_TOKEN`. A declared token file takes
precedence, and the command clears other ambient GitHub token variables.

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
python3 skills/k8s-admin-diagnostics/scripts/access_check.py --publication
```

`PASS` means every authority was reached and every live read succeeded, and the
receipt names the effective credential source and identity for each one. That
is the answer to "which credential am I using" — read it rather than searching
the host.

**5. Install it where your agent looks.** Steps 1 to 4 make the commands work
in a shell. An agent finds the skill only once it is installed into that
runtime's skills directory:

```bash
python tools/install_k8s_admin_diagnostics.py --dry-run --json   # the plan
python tools/install_k8s_admin_diagnostics.py --json             # ~/.codex/skills
python tools/install_k8s_admin_diagnostics.py --skills-dir ~/.claude/skills --json
```

The default destination is `$CODEX_HOME/skills/k8s-admin-diagnostics`, or
`~/.codex/skills/k8s-admin-diagnostics` when `CODEX_HOME` is unset; pass
`--skills-dir PATH` for any other runtime. It refuses to overwrite an existing
destination, requires a clean checkout of the skill subtree so the revision it
reports is verified against the source bytes, and reports the installed digest
— which is the value to compare against the `skill.digest` in any later report.
It also accepts `--yaml` and `--help`.

A Codex session can install the merged skill straight from GitHub instead:

```text
Install the skill from https://github.com/spyroot/ci-skills/tree/main/skills/k8s-admin-diagnostics
```

Either way, each execution host still needs its own credentials and its own
target file — installation grants nothing.

## If you are an agent

Start here and you will not need to read the rest.

1. **Run `access_check.py` before API collectors.** On `PASS`, the receipt
   names the effective credential source and identity for each authority.
   On `BLOCKED`, the surface `reason` names what to fix.
2. **Read `skills/k8s-admin-diagnostics/tools.json`** for the machine-readable
   manifest: every command, what it is for, when to use it, its authorities or
   execution surface, its options, and a symptom-to-command routing table.
   `<command> --describe` prints one command's contract and needs no
   credentials.
3. **Rely on the option tiers.** Every API command takes `--target`, `--json`,
   `--yaml`, `--human`, `--dry-run`, `--revision`, `--output-dir` and
   `--describe`. A command that filters records takes `--search`; one scoped to
   a namespace takes `--namespace`; one reading a time range takes `--last`,
   `--from` and `--to`. Learn the tier once.
4. **Read the status, not the exit code alone.** `PARTIAL` with
   `access_proven: true` means the read worked and a component is unhealthy —
   usually the finding. `DRY_RUN` is a plan and never evidence.

## Commands

The API commands accept the universal tier listed above, so only what is
specific to each one is listed here. Pass a full source commit SHA with
`--revision` when the installed copy has no Git metadata, and `--output-dir
PATH` to write paired JSON and text files; without that option no report file
is written.

- `access_check.py` checks the three selected authorities and runs the
  storage, event, and Cilium collector reads. `--publication` also requires
  repository admin permission and read-back of required branch checks.
  `--job-url URL` also checks that job, pipeline, runner, and trace.
- `gitlab_job.py --job-url URL` reads a selected job, pipeline, runner, and
  bounded trace. It accepts `--search TEXT`.
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
  Successful reads retain `records[].status: PASS`; explicit failed peer or
  endpoint probes appear in `records[].findings` with a path and action, and
  make the report `PARTIAL` without requiring every peer to be healthy.

The node-local commands run on the selected Linux node with noninteractive
`sudo -n`. They require local `crictl` or `journalctl`, and do not require
the API target file or an API access receipt:

- `cilium_node.py --json` reads the local running `cilium-agent` container
  through CRI, then collects `cilium-dbg status --verbose --output json` and
  `cilium-health status --verbose --output json` concurrently. If no agent is
  running, it records the stopped Cilium containers. A successful read keeps
  the raw JSON and reports explicit daemon or peer failures in `findings`.
  Use `--search TEXT` to narrow its returned records.
- `ceph_kernel.py --json` reads the previous three minutes of kernel journal
  entries matching `libceph|rbd|ceph`. Each record has a UTC timestamp,
  priority, classification, and machine-readable recommended action. It
  performs no recovery action. The window is the previous three minutes, with
  bounded output; a limit hit is reported as `PARTIAL`. Use `--search TEXT`
  and `--classification NAME` to narrow returned records without changing the
  underlying read status.

The kernel classifier emits action codes for observed blocklisting, auth
failure, connectivity timeout, and I/O errors. Other priority 0–3 entries
receive `review_ceph_kernel_event`; informational entries have no action.
The action code is a prompt for investigation, not a claimed root cause.

Both accept `--yaml`, `--dry-run`, and `--help`. Exit code 0 means the node
read succeeded or a dry run was requested; code 2 means incomplete evidence.
These node reads supplement the three-surface API receipt; they do not
replace it.

The base access gate -- all three authorities, including a real non-TTY
`cilium-health` exec on a selector-discovered ready agent -- runs before every
API collector, and a failure blocks it. The expanded bundle, which adds the
storage, event and Cilium collector reads, runs in `access_check.py`. Every
report names the gate it actually passed: a collector report in
`access.profile`, and an `access_check.py` receipt in top-level `profile`. So
neither form is implied for the other. Exit code 0 means
`PASS` or an explicitly marked `DRY_RUN`; code 2 means `BLOCKED` or
`PARTIAL`. JSON and YAML failures emit a structured error report on stdout.
An unavailable agent health result is `UNKNOWN` and makes its report partial.

A collector that could not read blocks the gate. One that read successfully
while reporting an unhealthy component does not, as long as the capability it
proves was demonstrated at least once — the skill has to be usable on the
degraded cluster it exists to diagnose. Each live check reports
`access_proven` next to its own `status`.

## Validation

The `validate` workflow checks workflow/YAML and Markdown syntax, diff
hygiene, secrets, Ruff lint and format, package behavior, and mocked denial
paths. Its package smoke runs every installed entrypoint from outside the
source tree. Mocked CI is code evidence; the live access receipt must come
from each intended execution host. Use the exact target and tested revision
there, and retain the sanitized receipt only after all required checks pass.
