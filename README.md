# Kubernetes admin diagnostics skill

`k8s-admin-diagnostics` collects read-only GitHub, GitLab CI, Kubernetes storage, event, and Cilium evidence. Each command uses native `gh`, `glab`, and `kubectl` clients and prints a human summary by default. JSON and YAML are available for automation.

## Install

Ask Codex to install this GitHub skill:

```text
Install the skill from https://github.com/spyroot/ci-skills/tree/main/skills/k8s-admin-diagnostics
```

The skill becomes available in the next Codex turn. Installation copies instructions and scripts; each computer still needs its own credentials and target file.

## Prerequisites and target

Install Python 3.11 or newer, PyYAML, `gh`, `glab`, and `kubectl` on the execution host. Authenticate the selected hosts through their CLI profiles or private token files, and provide a Kubernetes credential through `KUBECONFIG`, the default kubeconfig, or an explicit path. Keep the nonsecret target at `~/.config/ci-skills/target.toml` or another explicit `--target PATH` outside the skill:

```toml
[github]
host = "github.com"
repository = "owner/repository"

[gitlab]
url = "https://gitlab.example.com"
# token_file = "/home/operator/.config/ci-skills/credentials/gitlab.token"

[kubernetes]
context = "admin-context"
server = "https://api.cluster.example.com:6443"
# kubeconfig = "/home/operator/.config/ci-skills/credentials/kubeconfig"
```

Keep the target and credential files outside this repository and installed skill. [The storage and gate plan](skills/k8s-admin-diagnostics/references/access.md) defines each required file, where it lives, how it is used, and what verifies it. No command logs in, grants a role, changes the active context, or writes to the cluster.

## Commands

Run from the installed skill's `scripts` directory, or pass its absolute path:

| Script | Required input | Filters and behavior |
|---|---|---|
| `access_check.py` | `--target PATH` | Resolves the effective credential source for each surface, then checks GitHub identity and repository, GitLab instance administration and runner API, Kubernetes context, TLS, identity and wildcard administration, performs every resource read the collectors need, and runs the real non-TTY Cilium health command. Prints one `access_receipt`. Add `--publication` to require GitHub repository administration before configuring required checks. |
| `gitlab_job.py` | `--target PATH --job-url URL` | `--search TEXT`; reads job, pipeline, runner, and last 200 trace lines from the selected GitLab FQDN. |
| `storage_report.py` | `--target PATH` | `--namespace NAME|all`, `--node NAME`, `--storage-class NAME`, `--phase Pending|Bound|Lost|Released|Failed|all`, `--search TEXT`; reads storage and workload resources concurrently and correlates claims to Pods and attachments. |
| `event_trace.py` | `--target PATH` | `--from RFC3339`, `--to RFC3339`, `--namespace NAME|all`, `--kind KIND`, `--object NAME`, `--reason TEXT`, `--search TEXT`; returns a time-ordered event trace. Defaults to the previous hour. |
| `cilium_status.py` | `--target PATH` | `--namespace NAME|auto`, `--node NAME`, `--search TEXT`; aggregates DaemonSet, operator, CiliumNode, and concurrent non-TTY agent health. |
| `tools/check_project_neutrality.py` | `--root PATH` | Checks every versioned or pending path and byte for the prohibited project marker. |

Every report accepts mutually exclusive `--json` and `--yaml`, plus `--dry-run`, `--help`, and optional `--output-dir PATH`. An output directory receives paired `.json` and `.txt` reports from one collection. Without one, nothing is persisted. `--dry-run` lists probes and never counts as a live pass. The neutrality gate accepts `--json`, `--yaml`, and `--help`.

The gate runs before each live report, on the computer or runner you invoke it from. It passes only when every required credential source resolves to an effective source and every required live check succeeds; a saved CLI login is not sufficient, because an environment token overrides it. If anything blocks, the collector does not run and the receipt names what blocked. A command returns 0 for `PASS` or `DRY_RUN`, 2 for `BLOCKED` or `PARTIAL` diagnostics. An unavailable agent health reading is `UNKNOWN` and makes the Cilium report `PARTIAL`. All report objects include `schema_version`, `kind`, `status`, target, filters, records, errors, and summary where applicable.

## Validation

GitHub Actions runs CLI and filter tests, Ruff, skill checks, and the all-file neutrality scan. A passing CI check verifies code behavior with mocked authorities, which is never acceptance evidence for access. Run `access_check.py` on every intended execution host to verify real access there and keep its `access_receipt`; a mock, a dry run, and another computer's receipt do not count.
