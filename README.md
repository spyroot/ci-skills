# Kubernetes admin diagnostics skill

`k8s-admin-diagnostics` collects read-only GitHub, GitLab CI, Kubernetes storage, event, and Cilium evidence. Each command uses native `gh`, `glab`, and `kubectl` clients and prints a human summary by default. JSON and YAML are available for automation.

## Install

Use Codex's GitHub skill installer with repository `spyroot/ci-skills` and path `skills/k8s-admin-diagnostics`. For example:

```text
$skill-installer --repo spyroot/ci-skills --path skills/k8s-admin-diagnostics
```

The skill becomes available in the next Codex turn. Installation copies instructions and scripts; each computer still needs its own credentials and target file.

## Prerequisites and target

Install Python 3.11 or newer, PyYAML, `gh`, `glab`, and `kubectl` on the execution host. Sign in to the selected GitHub and GitLab hosts and provide a Kubernetes credential through `KUBECONFIG`, the default kubeconfig, or an explicit path. The target file contains only host and context identifiers:

```toml
[github]
host = "github.com"
repository = "owner/repository"

[gitlab]
url = "https://gitlab.example.com"

[kubernetes]
context = "admin-context"
server = "https://api.cluster.example.com:6443"
# kubeconfig = "/home/operator/.kube/config"
```

Keep the target file outside this repository. Its exact access contract and verification probes are in [access.md](skills/k8s-admin-diagnostics/references/access.md). No command logs in, grants a role, changes the active context, or writes to the cluster.

## Commands

Run from the installed skill's `scripts` directory, or pass its absolute path:

| Script | Required input | Filters and behavior |
|---|---|---|
| `access_check.py` | `--target PATH` | Checks GitHub identity and repository, GitLab instance administration and runner API, Kubernetes context, TLS, identity, wildcard administration, and Cilium exec permission. |
| `gitlab_job.py` | `--target PATH --job-url URL` | `--search TEXT`; reads job, pipeline, runner, and last 200 trace lines from the selected GitLab FQDN. |
| `storage_report.py` | `--target PATH` | `--namespace NAME|all`, `--node NAME`, `--storage-class NAME`, `--phase Pending|Bound|Lost|Released|Failed|all`, `--search TEXT`; reads storage and workload resources concurrently and correlates claims to Pods and attachments. |
| `event_trace.py` | `--target PATH` | `--from RFC3339`, `--to RFC3339`, `--namespace NAME|all`, `--kind KIND`, `--object NAME`, `--reason TEXT`, `--search TEXT`; returns a time-ordered event trace. Defaults to the previous hour. |
| `cilium_status.py` | `--target PATH` | `--namespace NAME|auto`, `--node NAME`, `--search TEXT`; aggregates DaemonSet, operator, CiliumNode, and concurrent non-TTY agent health. |
| `tools/check_project_neutrality.py` | `--root PATH` | Checks every versioned or pending path and byte for the prohibited project marker. |

Every report accepts mutually exclusive `--json` and `--yaml`, plus `--dry-run`, `--help`, and optional `--output-dir PATH`. An output directory receives paired `.json` and `.txt` reports from one collection. Without one, nothing is persisted. `--dry-run` lists probes and never counts as a live pass. The neutrality gate accepts `--json`, `--yaml`, and `--help`.

The gate runs before each live report. If any surface is blocked, the collector does not run. A command returns 0 for `PASS` or `DRY_RUN`, 2 for blocked or partial diagnostics. All report objects include `schema_version`, `kind`, `status`, target, filters, records, errors, and summary where applicable.

## Validation

GitHub Actions runs CLI and filter tests, Ruff, skill checks, and the all-file neutrality scan. A passing CI check verifies code behavior with mocked authorities. Run `access_check.py` on every intended execution host to verify real access there; another computer needs its own gate receipt.
