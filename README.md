# Kubernetes admin diagnostics skill

`k8s-admin-diagnostics` collects read-only GitHub, GitLab CI, Kubernetes
storage, event, and Cilium evidence. Its commands use `gh`, `glab`, and
`kubectl`. They print human summaries by default and support JSON and YAML.

## Install

Ask Codex to install the skill from the repository:

```text
Install the skill from https://github.com/spyroot/ci-skills/tree/main/skills/k8s-admin-diagnostics
```

The skill becomes available in the next Codex turn. Each execution host
still needs its own credentials and target file.

## Prerequisites and target

Install Python 3.11 or newer, PyYAML, `gh`, `glab`, and `kubectl` on the
execution host. Supply a nonsecret TOML file through `--target PATH`:
The suggested local location is `~/.config/ci-skills/target.toml`.

```toml
[github]
host = "github.com"
repository = "owner/repository"
# required_checks = ["validate", "gal19/live-receipt"]

[gitlab]
url = "https://gitlab.example.com"
# token_file = "/home/operator/.config/ci-skills/gitlab.token"

[kubernetes]
context = "admin-context"
server = "https://api.cluster.example.com:6443"
# kubeconfig = "/home/operator/.kube/config"
```

For GitHub and GitLab, an explicit token file takes precedence, followed by
the effective token environment variable, then the selected host's CLI
credential store. Kubernetes uses the explicit kubeconfig, `KUBECONFIG`, or
the default kubeconfig. Its selected user may authenticate with an embedded
token, `tokenFile`, client certificate and key, or exec provider. See
[access.md](skills/k8s-admin-diagnostics/references/access.md) for the checks.

Keep the target and credential files outside the repository and installed
skill. The commands never log in, grant roles, or change the active context.

## Commands

Every command accepts `--target PATH`, `--revision SHA`, `--json`, `--yaml`,
`--dry-run`, and `--help`. Pass a full source commit SHA with `--revision`
when the installed copy has no Git metadata. Reports also accept
`--output-dir PATH` to publish a unique run directory containing paired
`report.json` and `report.txt` files. Without that option, no file is written.

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
- `event_trace.py` reads both Kubernetes event APIs and accepts `--from`,
  `--to`, `--namespace`, `--kind`, `--object`, `--reason`, and `--search`.
  Its default window is the previous hour.
- `cilium_status.py` reads Cilium resources and executes non-TTY health on
  ready agents. It accepts `--namespace NAME|auto`, `--node`, and `--search`.

The full access gate runs before every live collector. Exit code 0 means
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
source tree. Its PR job also requires a fresh, exact-head
`gal19/live-receipt` commit status from the trusted verifier account named by
the repository variable `GAL19_TRUSTED_STATUS_ACTOR`. The private verifier
uses `tools/verify_live_receipt.py` to compare the actual-host receipt with
independently selected targets and the skill bytes at that revision, then
posts that status. PR jobs never receive the private receipt. Until the
trusted job, status actor, and required branch check are configured, live
acceptance remains blocked.
