# Kubernetes admin diagnostics skill

`k8s-admin-diagnostics` collects read-only GitHub, GitLab CI, Kubernetes
storage, event, and Cilium evidence. Its commands use `gh`, `glab`, and
`kubectl`. They print human summaries by default and support JSON and YAML.

## Install

From a checkout of this repository, run the bundled installer:

```sh
python tools/install_k8s_admin_diagnostics.py --dry-run --json
python tools/install_k8s_admin_diagnostics.py --json
```

It copies the skill into `$CODEX_HOME/skills/k8s-admin-diagnostics`, or
`~/.codex/skills/k8s-admin-diagnostics` when `CODEX_HOME` is unset. It blocks
if that destination already exists and reports the installed file digest.
Installation requires a clean checkout of the skill subtree so the reported
revision is verified against the source bytes.
Use `--skills-dir PATH` for another Codex skills directory. The command also
accepts `--yaml` and `--help`.

Codex can also install the merged skill directly from GitHub:

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

The API commands accept `--target PATH`, `--revision SHA`, `--json`, `--yaml`,
`--dry-run`, and `--help`. Pass a full source commit SHA with `--revision`
when the installed copy has no Git metadata. Reports also accept
`--output-dir PATH` to write paired JSON and text files. Without that option,
no report file is written.

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

The node-local commands run on the selected Linux node with noninteractive
`sudo -n`. They require local `crictl` or `journalctl`, and do not require
the API target file:

- `cilium_node.py --json` reads the local running `cilium-agent` container
  through CRI, then collects `cilium-dbg status --verbose --output json` and
  `cilium-health status --verbose --output json` concurrently. If no agent is
  running, it records the stopped Cilium containers.
- `ceph_kernel.py --json` reads the previous three minutes of kernel journal
  entries matching `libceph|rbd|ceph`. Each record has a UTC timestamp,
  priority, classification, and machine-readable recommended action. It
  performs no recovery action. The window is the previous three minutes, with
  bounded output; a limit hit is reported as `PARTIAL`.

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
collector, and a failure blocks it. The expanded bundle, which adds the
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
